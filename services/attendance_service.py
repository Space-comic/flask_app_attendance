"""基础考勤服务：加载人脸库、执行识别并写入签到记录。"""

import os
import pickle
import sys
import threading
from datetime import datetime, timedelta, timezone

import cv2
import numpy as np
from flask import current_app

from models import db
from models.attendance import AttendanceRecord
from models.emotion import EmotionRecord
from models.user import User
from services import anti_spoof_service, emotion_service

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

ATTENDANCE_FRAME_WIDTH = int(os.environ.get('ATTENDANCE_FRAME_WIDTH', '480'))
ATTENDANCE_ENCODER_NUM_JITTERS = int(os.environ.get('ATTENDANCE_ENCODER_NUM_JITTERS', '1'))
ATTENDANCE_ENCODER_MODEL = os.environ.get('ATTENDANCE_ENCODER_MODEL', 'small')
ATTENDANCE_MATCH_TOLERANCE = float(os.environ.get('ATTENDANCE_MATCH_TOLERANCE', '0.48'))
ATTENDANCE_STRONG_MATCH_DISTANCE = float(os.environ.get('ATTENDANCE_STRONG_MATCH_DISTANCE', '0.30'))
ATTENDANCE_MIN_CONFIDENCE = float(os.environ.get('ATTENDANCE_MIN_CONFIDENCE', '55'))

_rec_instance = None
_rec_lock = threading.Lock()


def _utc_now_naive():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _local_now():
    offset = int(current_app.config.get('TIMEZONE_OFFSET_HOURS', 8))
    return _utc_now_naive() + timedelta(hours=offset)


def _distance_to_confidence(distance, tolerance):
    """将 face_recognition 的欧氏距离映射为更保守的百分比分数。"""
    if distance is None:
        return None

    face_distance = float(distance)
    reject_threshold = max(float(tolerance), ATTENDANCE_STRONG_MATCH_DISTANCE + 1e-6)
    strong_threshold = min(ATTENDANCE_STRONG_MATCH_DISTANCE, reject_threshold - 1e-6)

    if face_distance <= strong_threshold:
        bonus_ratio = (strong_threshold - face_distance) / max(strong_threshold, 1e-6)
        return min(99.0, 90.0 + bonus_ratio * 9.0)

    if face_distance <= reject_threshold:
        match_ratio = (reject_threshold - face_distance) / max(reject_threshold - strong_threshold, 1e-6)
        return 50.0 + match_ratio * 40.0

    upper_bound = max(0.75, reject_threshold + 0.15)
    reject_ratio = 1.0 - min(
        max((face_distance - reject_threshold) / max(upper_bound - reject_threshold, 1e-6), 0.0),
        1.0,
    )
    return max(0.0, reject_ratio * 49.0)


def _cache_path():
    return os.path.join(current_app.config['IMAGES_DB_PATH'], '_face_encodings.pkl')


def _load_feature_cache():
    path = _cache_path()
    if not os.path.exists(path):
        return {}
    try:
        with open(path, 'rb') as file:
            data = pickle.load(file)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _save_feature_cache(cache):
    try:
        with open(_cache_path(), 'wb') as file:
            pickle.dump(cache, file)
    except Exception:
        pass


def _file_signature(path):
    stat = os.stat(path)
    return (int(stat.st_mtime), stat.st_size)


def _load_rec():
    from my_face_recognition import f_face_recognition as rec_face

    images_db = current_app.config['IMAGES_DB_PATH']
    cached = _load_feature_cache()
    next_cache = {}
    cache_dirty = False
    names = []
    feats = []

    for fname in sorted(os.listdir(images_db)):
        if not fname.lower().endswith(('.jpg', '.jpeg')):
            continue

        fpath = os.path.join(images_db, fname)
        sig = _file_signature(fpath)
        cached_item = cached.get(fname)
        vec = None

        if cached_item and cached_item.get('sig') == sig:
            raw_vec = cached_item.get('encoding') or []
            if len(raw_vec) == 128:
                vec = np.asarray(raw_vec, dtype=np.float64)

        if vec is None:
            img = cv2.imdecode(np.fromfile(fpath, dtype=np.uint8), cv2.IMREAD_COLOR)
            if img is None:
                cache_dirty = True
                continue

            rgb = cv2.cvtColor(img, cv2.COLOR_BGR2RGB)
            boxes = rec_face.detect_face(rgb)
            feat = rec_face.get_features(rgb, boxes)
            if len(feat) != 1:
                cache_dirty = True
                continue

            vec = np.asarray(feat[0], dtype=np.float64)
            cache_dirty = True

        next_cache[fname] = {'sig': sig, 'encoding': vec.tolist()}
        names.append(os.path.splitext(fname)[0])
        feats.append(vec)

    if set(cached.keys()) != set(next_cache.keys()):
        cache_dirty = True
    if cache_dirty:
        _save_feature_cache(next_cache)

    feat_arr = np.vstack(feats) if feats else np.empty((0, 128), dtype=np.float64)
    return names, feat_arr


class _Recognizer:
    def __init__(self, names, feats):
        self.db_names = names
        self.db_features = np.asarray(feats, dtype=np.float64)
        if self.db_features.ndim == 1 and self.db_features.size:
            self.db_features = self.db_features.reshape(1, -1)

    def recognize_face(self, image_np):
        from my_face_recognition import f_face_recognition as rec_face
        import face_recognition

        try:
            rgb = cv2.cvtColor(image_np, cv2.COLOR_BGR2RGB)
            height, width = rgb.shape[:2]
            scale = 1.0
            small_rgb = rgb
            if width > ATTENDANCE_FRAME_WIDTH:
                scale = ATTENDANCE_FRAME_WIDTH / float(width)
                small_rgb = cv2.resize(
                    rgb,
                    (ATTENDANCE_FRAME_WIDTH, int(height * scale)),
                    interpolation=cv2.INTER_AREA,
                )

            small_boxes = rec_face.detect_face(small_rgb)
            if not small_boxes:
                return {'status': 'ok', 'faces': [], 'names': [], 'distances': []}

            inv = 1.0 / scale
            full_boxes = [
                (int(y0 * inv), int(x1 * inv), int(y1 * inv), int(x0 * inv))
                for (y0, x1, y1, x0) in small_boxes
            ]
            if not self.db_names or self.db_features.size == 0:
                return {
                    'status': 'ok',
                    'faces': full_boxes,
                    'names': ['unknow'] * len(full_boxes),
                    'distances': [None] * len(full_boxes),
                }

            biggest = max(small_boxes, key=lambda box: (box[2] - box[0]) * (box[1] - box[3]))
            y0, x1, y1, x0 = biggest
            full_box = (int(y0 * inv), int(x1 * inv), int(y1 * inv), int(x0 * inv))
            feats = face_recognition.face_encodings(
                rgb,
                known_face_locations=[full_box],
                num_jitters=ATTENDANCE_ENCODER_NUM_JITTERS,
                model=ATTENDANCE_ENCODER_MODEL,
            )
            if not feats:
                return {
                    'status': 'ok',
                    'faces': full_boxes,
                    'names': ['unknow'] * len(full_boxes),
                    'distances': [None] * len(full_boxes),
                }

            dists = face_recognition.face_distance(self.db_features, feats[0])
            best_idx = int(np.argmin(dists))
            best_dist = float(dists[best_idx])
            name = self.db_names[best_idx] if best_dist <= ATTENDANCE_MATCH_TOLERANCE else 'unknow'
            return {
                'status': 'ok',
                'faces': [full_box],
                'names': [name],
                'distances': [best_dist],
            }
        except Exception as exc:
            return {'status': f'error: {exc}', 'faces': [], 'names': [], 'distances': []}


def reload_recognizer():
    global _rec_instance
    with _rec_lock:
        names, feats = _load_rec()
        _rec_instance = _Recognizer(names, feats)
        return _rec_instance


def get_rec():
    global _rec_instance
    if _rec_instance is None:
        with _rec_lock:
            if _rec_instance is None:
                names, feats = _load_rec()
                _rec_instance = _Recognizer(names, feats)
    return _rec_instance


def recognize_and_checkin(image_np, session_token):
    recognizer = get_rec()
    if not recognizer.db_names:
        return {'status': 'error', 'message': '人脸库为空，请先录入学生人脸'}

    result = recognizer.recognize_face(image_np)
    if result['status'] != 'ok':
        return {'status': 'error', 'message': f'识别器异常: {result["status"]}'}
    if not result['names']:
        return {'status': 'no_face', 'message': '未检测到人脸'}

    name = result['names'][0]
    face_box = (result.get('faces') or [None])[0]
    distance = (result.get('distances') or [None])[0]
    confidence = _distance_to_confidence(distance, ATTENDANCE_MATCH_TOLERANCE) if distance is not None else None

    if name != 'unknow' and (confidence is None or confidence < ATTENDANCE_MIN_CONFIDENCE):
        name = 'unknow'

    anti_spoof = anti_spoof_service.verify_session(session_token, image_np, face_box)
    if not anti_spoof.get('ok'):
        return {
            'status': 'fake',
            'message': anti_spoof.get('message') or '检测到疑似屏幕或视频重放攻击',
        }

    if name == 'unknow':
        return {
            'status': 'unknown',
            'message': '未识别到已注册用户，请正对摄像头并确保光线充足后重试',
        }

    user = User.query.get(name) or User.query.filter_by(name=name).first()
    if not user:
        return {'status': 'unknown', 'message': f'未找到用户 {name}'}

    local_now = _local_now()
    today_local = local_now.date()
    allow_multi = current_app.config.get('ALLOW_MULTI_CHECKIN_PER_DAY', False)

    existing = AttendanceRecord.query.filter_by(student_id=user.id, date=today_local).first()
    if existing and not allow_multi:
        return {
            'status': 'already_checked',
            'message': f'{user.name} 今日已签到',
            'student': user.to_dict(),
            'check_time': existing.to_dict().get('check_time'),
        }

    record = AttendanceRecord(
        student_id=user.id,
        student_name=user.name,
        check_time=_utc_now_naive(),
        date=today_local,
        status='present',
        method='face',
    )
    db.session.add(record)

    emotion = emotion_service.analyze_face_crop(image_np, face_box)
    db.session.add(
        EmotionRecord(
            student_id=user.id,
            student_name=user.name,
            emotion=emotion,
            source='attendance',
        )
    )
    db.session.commit()
    anti_spoof_service.clear_session(session_token)

    return {
        'status': 'success',
        'message': f'{user.name} 签到成功',
        'student': user.to_dict(),
        'emotion': emotion,
        'check_time': record.to_dict().get('check_time'),
        'date': today_local.isoformat(),
    }


def get_records(student_id=None, date_from=None, date_to=None, page=1, per_page=20):
    query = AttendanceRecord.query
    if student_id:
        query = query.filter_by(student_id=student_id)
    if date_from:
        query = query.filter(AttendanceRecord.date >= date_from)
    if date_to:
        query = query.filter(AttendanceRecord.date <= date_to)

    query = query.order_by(AttendanceRecord.check_time.desc())
    paginated = query.paginate(page=page, per_page=per_page, error_out=False)
    return {
        'records': [record.to_dict() for record in paginated.items],
        'total': paginated.total,
        'pages': paginated.pages,
        'page': page,
    }
