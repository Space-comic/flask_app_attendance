"""合照识别服务：负责多人检测、批量编码匹配、活动建档、名单生成和结果绘制。"""

import base64
import os
import threading

import cv2
import face_recognition
import numpy as np
from flask import current_app

from models import db
from models.activity import Activity, ActivityParticipant
from models.emotion import EmotionRecord
from models.user import User
from services import emotion_service
from services.face_detection_service import detect_faces_for_group

try:
    from PIL import Image, ImageDraw, ImageFont

    _PIL_OK = True
except Exception:
    _PIL_OK = False

GROUP_INPUT_MAX_SIDE = int(os.environ.get('GROUP_INPUT_MAX_SIDE', '1600'))
GROUP_ENCODER_MODEL = os.environ.get('GROUP_ENCODER_MODEL', 'large')
GROUP_ENCODER_NUM_JITTERS = int(os.environ.get('GROUP_ENCODER_NUM_JITTERS', '1'))
GROUP_ENCODE_BOX_EXPAND = float(os.environ.get('GROUP_ENCODE_BOX_EXPAND', '0.18'))
GROUP_ENCODE_MIN_SIDE = int(os.environ.get('GROUP_ENCODE_MIN_SIDE', '112'))
GROUP_FACE_CROP_SIZE = int(os.environ.get('GROUP_FACE_CROP_SIZE', str(GROUP_ENCODE_MIN_SIDE)))
GROUP_FACE_TOLERANCE = float(os.environ.get('GROUP_FACE_TOLERANCE', '0.60'))
GROUP_FACE_MARGIN = float(os.environ.get('GROUP_FACE_MARGIN', '0.03'))

_rec_instance = None


def get_rec():
    """获取合照识别复用的底库识别器实例。"""
    global _rec_instance
    if _rec_instance is None:
        from services.attendance_service import _Recognizer, _load_rec

        names, feats = _load_rec()
        _rec_instance = _Recognizer(names, feats)
    return _rec_instance


def _distance_to_confidence(distance, tolerance):
    """复用考勤模块中的距离转置信度算法。"""
    from services.attendance_service import _distance_to_confidence as _attendance_distance_to_confidence

    return _attendance_distance_to_confidence(distance, tolerance)


def _norm_db_feats(db_features):
    """将底库特征统一整理为二维 `numpy` 数组。"""
    arr = np.asarray(db_features, dtype=np.float64)
    if arr.size == 0:
        return np.empty((0, 128), dtype=np.float64)
    if arr.ndim == 1:
        arr = arr.reshape(1, -1)
    return arr


def _clip_box(box, shape):
    """将人脸框裁剪到图像边界以内，并过滤非法框。"""
    y0, x1, y1, x0 = [int(v) for v in box]
    h, w = shape[:2]
    y0 = max(0, min(y0, h))
    y1 = max(0, min(y1, h))
    x0 = max(0, min(x0, w))
    x1 = max(0, min(x1, w))
    if y1 <= y0 or x1 <= x0:
        return None
    return y0, x1, y1, x0


def _expand_box(box, shape, ratio):
    """按比例扩展人脸框，为后续裁剪保留更多上下文。"""
    y0, x1, y1, x0 = box
    face_h = max(1, y1 - y0)
    face_w = max(1, x1 - x0)
    pad_y = int(round(face_h * ratio))
    pad_x = int(round(face_w * ratio))
    return _clip_box((y0 - pad_y, x1 + pad_x, y1 + pad_y, x0 - pad_x), shape)


def _enhance_rgb(rgb):
    """对 RGB 图做亮度增强与锐化，用于提高人脸编码稳定性。"""
    ycrcb = cv2.cvtColor(rgb, cv2.COLOR_RGB2YCrCb)
    ycrcb[:, :, 0] = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8)).apply(ycrcb[:, :, 0])
    enhanced = cv2.cvtColor(ycrcb, cv2.COLOR_YCrCb2RGB)
    blur = cv2.GaussianBlur(enhanced, (0, 0), 1.0)
    return cv2.addWeighted(enhanced, 1.3, blur, -0.3, 0)


def _resize_short_side(rgb, target_side):
    """按短边缩放图像到目标尺寸。"""
    h, w = rgb.shape[:2]
    short_side = min(h, w)
    if short_side <= 0 or short_side >= target_side:
        return rgb, 1.0
    scale = target_side / float(short_side)
    resized = cv2.resize(
        rgb,
        (max(1, int(round(w * scale))), max(1, int(round(h * scale)))),
        interpolation=cv2.INTER_CUBIC,
    )
    return resized, scale


def _resize_long_side_limit(image, max_side):
    """将合照最大边限制在给定尺寸以内，控制总推理耗时。"""
    h, w = image.shape[:2]
    long_side = max(h, w)
    if long_side <= 0 or long_side <= max_side:
        return image, 1.0
    scale = max_side / float(long_side)
    resized = cv2.resize(
        image,
        (max(1, int(round(w * scale))), max(1, int(round(h * scale)))),
        interpolation=cv2.INTER_AREA,
    )
    return resized, scale


def _encode_faces(rgb_orig, boxes):
    """
    对检测到的人脸逐个编码。

    算法说明：
        1. 先按比例扩框，保留更多五官上下文。
        2. 再统一缩放到固定尺寸，减少尺度差异带来的编码波动。
        3. 每张脸只提取一次 embedding，避免重复计算。
    """
    model = GROUP_ENCODER_MODEL
    num_jitters = GROUP_ENCODER_NUM_JITTERS
    expand = GROUP_ENCODE_BOX_EXPAND
    target_size = GROUP_FACE_CROP_SIZE

    encodings = []
    kept_indices = []
    kept_boxes = []
    for idx, box in enumerate(boxes):
        clipped_box = _clip_box(box, rgb_orig.shape)
        if clipped_box is None:
            continue

        expanded = _expand_box(clipped_box, rgb_orig.shape, expand) or clipped_box
        y0, x1, y1, x0 = expanded
        crop = rgb_orig[y0:y1, x0:x1]
        if crop.size == 0:
            continue

        # 合照编码统一缩放到固定尺寸，兼顾速度与稳定性。
        crop_h, crop_w = crop.shape[:2]
        scale_y = target_size / float(max(1, crop_h))
        scale_x = target_size / float(max(1, crop_w))
        interpolation = cv2.INTER_AREA if max(crop_h, crop_w) > target_size else cv2.INTER_CUBIC
        crop = cv2.resize(crop, (target_size, target_size), interpolation=interpolation)
        local_box = (
            int(round((clipped_box[0] - y0) * scale_y)),
            int(round((clipped_box[1] - x0) * scale_x)),
            int(round((clipped_box[2] - y0) * scale_y)),
            int(round((clipped_box[3] - x0) * scale_x)),
        )
        local_box = _clip_box(local_box, crop.shape)
        if local_box is None:
            continue

        try:
            encoded = face_recognition.face_encodings(
                crop,
                known_face_locations=[local_box],
                num_jitters=num_jitters,
                model=model,
            )
        except Exception:
            encoded = []

        if encoded:
            encodings.append(encoded[0])
            kept_indices.append(idx)
            kept_boxes.append(clipped_box)

    return encodings, kept_indices, kept_boxes


def _match_faces(features, db_features, db_names):
    """
    将合照中每张人脸与底库做最近邻匹配。

    匹配策略：
        1. 使用欧式距离找最近邻。
        2. 若第一名与第二名距离差太小，则认为结果不稳定。
        3. 同一学号默认只分配给距离最优的一张脸，减少重复认错。
    """
    tolerance = GROUP_FACE_TOLERANCE
    margin = GROUP_FACE_MARGIN
    db_feats = _norm_db_feats(db_features)
    n = len(features)
    if n == 0 or len(db_names) == 0 or db_feats.shape[0] == 0:
        return ['unknow'] * n, [None] * n, [None] * n

    candidates = []
    for face_idx, feat in enumerate(features):
        dists = face_recognition.face_distance(db_feats, feat)
        order = np.argsort(dists)
        best_idx = int(order[0])
        best_dist = float(dists[best_idx])
        second_dist = float(dists[int(order[1])]) if len(order) > 1 else 1.0
        confidence = _distance_to_confidence(best_dist, tolerance)
        candidates.append(
            {
                'face_idx': face_idx,
                'label': db_names[best_idx],
                'distance': best_dist,
                'confidence': confidence,
                'gap_ok': (second_dist - best_dist) >= margin,
                'easy_ok': best_dist <= max(0.0, tolerance - 0.05),
            }
        )

    names = ['unknow'] * n
    distances = [None] * n
    confidences = [None] * n
    used_labels = set()

    for cand in sorted(candidates, key=lambda item: item['distance']):
        if cand['distance'] > tolerance:
            continue
        if not (cand['gap_ok'] or cand['easy_ok']):
            continue
        if cand['label'] in used_labels:
            continue

        face_idx = cand['face_idx']
        names[face_idx] = cand['label']
        distances[face_idx] = cand['distance']
        confidences[face_idx] = cand['confidence']
        used_labels.add(cand['label'])

    return names, distances, confidences


def _resolve_name_to_user(label):
    """将识别标签解析为数据库用户对象。"""
    if not label or label == 'unknow':
        return None
    user = User.query.get(label)
    if user:
        return user
    return User.query.filter_by(name=label).first()


def _persist_group_photo_emotions(app, emotion_jobs):
    """后台线程中批量写入合照情绪记录，避免阻塞主识别流程。"""
    if not emotion_jobs:
        return

    with app.app_context():
        try:
            for job in emotion_jobs:
                emotion = emotion_service.analyze(job['face_crop'])
                db.session.add(
                    EmotionRecord(
                        student_id=job['student_id'],
                        student_name=job['student_name'],
                        emotion=emotion,
                        source='group_photo',
                    )
                )
            db.session.commit()
        except Exception as exc:
            db.session.rollback()
            try:
                print(f'[group_photo] async emotion failed: {exc}', flush=True)
            except Exception:
                pass
        finally:
            db.session.remove()


def _dispatch_group_photo_emotions(emotion_jobs):
    """启动异步线程执行合照情绪分析。"""
    if not emotion_jobs:
        return

    app = current_app._get_current_object()
    worker = threading.Thread(
        target=_persist_group_photo_emotions,
        args=(app, emotion_jobs),
        daemon=True,
    )
    worker.start()


def recognize_group_photo(image_np, activity_name, teacher_id):
    """
    合照识别主流程。

    核心阶段：
        1. 统一缩图到最大边不超过 1600。
        2. 调用 `detect_faces_for_group()` 做多人检测。
        3. 对每张人脸只编码一次，并与底库做批量匹配。
        4. 生成活动参与名单并落库。
        5. 异步执行情绪识别，避免阻塞主链路。
    """
    recognizer = get_rec()
    if not recognizer.db_names:
        return {'status': 'error', 'message': '人脸库为空，请先录入学生人脸'}

    work_bgr, _ = _resize_long_side_limit(image_np, GROUP_INPUT_MAX_SIDE)
    rgb_work = cv2.cvtColor(work_bgr, cv2.COLOR_BGR2RGB)

    # 检测阶段只在统一缩图后的合照上运行，显著压缩 RetinaFace 推理成本。
    detected_boxes, detector_backend = detect_faces_for_group(rgb_work)
    if not detected_boxes:
        return {'status': 'no_face', 'message': '图片中未检测到人脸'}

    features, feature_indices, feature_boxes = _encode_faces(rgb_work, detected_boxes)
    names = ['unknow'] * len(detected_boxes)
    distances = [None] * len(detected_boxes)
    confidences = [None] * len(detected_boxes)

    if features:
        # 合照中的每张人脸只编码一次，再用距离阈值和 margin 规则完成身份分配。
        matched_names, matched_distances, matched_confidences = _match_faces(
            features,
            recognizer.db_features,
            recognizer.db_names,
        )
        for local_idx, original_idx in enumerate(feature_indices):
            names[original_idx] = matched_names[local_idx]
            distances[original_idx] = matched_distances[local_idx]
            confidences[original_idx] = matched_confidences[local_idx]
            detected_boxes[original_idx] = feature_boxes[local_idx]

    activity = Activity(name=activity_name, created_by=teacher_id)
    db.session.add(activity)
    db.session.flush()

    participants = []
    detections = []
    display_names = []
    seen_student_ids = set()
    emotion_jobs = []

    for idx, label in enumerate(names):
        user = _resolve_name_to_user(label)
        distance = distances[idx]
        confidence = confidences[idx]

        if user:
            display_names.append(f'{user.id}-{user.name}')
            detections.append(
                {
                    'index': idx + 1,
                    'student_id': user.id,
                    'student_name': user.name,
                    'distance': round(distance, 3) if distance is not None else None,
                    'confidence': round(confidence, 1) if confidence is not None else None,
                }
            )

            if user.id not in seen_student_ids:
                db.session.add(
                    ActivityParticipant(
                        activity_id=activity.id,
                        student_id=user.id,
                        student_name=user.name,
                    )
                )
                participants.append({'id': user.id, 'name': user.name})
                seen_student_ids.add(user.id)

                y0, x1, y1, x0 = detected_boxes[idx]
                face_crop = work_bgr[max(0, y0):y1, max(0, x0):x1]
                if face_crop.size > 0:
                    emotion_jobs.append(
                        {
                            'student_id': user.id,
                            'student_name': user.name,
                            'face_crop': face_crop.copy(),
                        }
                    )
        else:
            display_names.append('未知')
            detections.append(
                {
                    'index': idx + 1,
                    'student_id': None,
                    'student_name': '未知',
                    'distance': round(distance, 3) if distance is not None else None,
                    'confidence': round(confidence, 1) if confidence is not None else None,
                }
            )

    db.session.commit()
    # 情绪识别改为后台线程异步执行，避免阻塞主识别耗时。
    _dispatch_group_photo_emotions(emotion_jobs)

    annotated = _draw_boxes(work_bgr.copy(), detected_boxes, display_names, distances)
    ok, encoded = cv2.imencode('.jpg', annotated, [cv2.IMWRITE_JPEG_QUALITY, 90])
    annotated_b64 = base64.b64encode(encoded).decode('utf-8') if ok else ''

    return {
        'status': 'success',
        'activity_id': activity.id,
        'participants': participants,
        'detections': detections,
        'recognized_count': len(participants),
        'unknown_count': names.count('unknow'),
        'total_faces': len(detected_boxes),
        'detector_backend': detector_backend,
        'annotated_image': annotated_b64,
    }


def _get_cn_font(size):
    """为 PIL 标注流程选择可用的中文字体。"""
    candidates = [
        r'C:\Windows\Fonts\msyh.ttc',
        r'C:\Windows\Fonts\msyhbd.ttc',
        r'C:\Windows\Fonts\simhei.ttf',
        r'C:\Windows\Fonts\simsun.ttc',
        '/usr/share/fonts/truetype/wqy/wqy-microhei.ttc',
        '/System/Library/Fonts/PingFang.ttc',
    ]
    if not _PIL_OK:
        return None
    for path in candidates:
        if os.path.exists(path):
            try:
                return ImageFont.truetype(path, size)
            except Exception:
                continue
    try:
        return ImageFont.load_default()
    except Exception:
        return None


def _draw_boxes(image, boxes, display_names, distances=None):
    """使用 PIL 在合照上绘制人脸框、学号姓名和匹配距离。"""
    h_img, w_img = image.shape[:2]
    if not _PIL_OK:
        return _draw_boxes_cv2(image, boxes, display_names, distances)

    pil_img = Image.fromarray(cv2.cvtColor(image, cv2.COLOR_BGR2RGB))
    draw = ImageDraw.Draw(pil_img)

    for idx, ((y0, x1, y1, x0), name) in enumerate(zip(boxes, display_names)):
        known = name != '未知'
        color_box = (255, 140, 0) if known else (220, 30, 30)
        thickness = max(2, int(min(x1 - x0, y1 - y0) / 42))
        for t in range(thickness):
            draw.rectangle([x0 - t, y0 - t, x1 + t, y1 + t], outline=color_box)

        face_w = max(1, x1 - x0)
        font_size = int(max(14, min(28, face_w * 0.23)))
        font = _get_cn_font(font_size)
        label = f'{idx + 1}.{name}'
        if distances and distances[idx] is not None:
            label += f' ({distances[idx]:.2f})'

        try:
            bbox = draw.textbbox((0, 0), label, font=font)
            tw = bbox[2] - bbox[0]
            th = bbox[3] - bbox[1]
        except Exception:
            tw, th = len(label) * font_size // 2, font_size

        pad = max(3, font_size // 5)
        lx1 = max(0, x0)
        ly2 = max(th + pad * 2 + 2, y0 - 3)
        ly1 = ly2 - th - pad * 2
        if ly1 < 0:
            ly1 = min(h_img - th - pad * 2 - 2, y1 + 3)
            ly2 = ly1 + th + pad * 2
        lx2 = min(w_img, lx1 + tw + pad * 2)

        draw.rectangle([lx1, ly1, lx2, ly2], fill=(20, 20, 20))
        draw.text((lx1 + pad, ly1 + pad), label, font=font, fill=(255, 255, 255))

    return cv2.cvtColor(np.array(pil_img), cv2.COLOR_RGB2BGR)


def _draw_boxes_cv2(image, boxes, display_names, distances=None):
    """当 PIL 不可用时，使用 OpenCV 作为备选绘制实现。"""
    for idx, ((y0, x1, y1, x0), name) in enumerate(zip(boxes, display_names)):
        known = name != '未知'
        color = (0, 140, 255) if known else (0, 0, 255)
        label = f'{idx + 1}.{name}'
        if distances and distances[idx] is not None:
            label += f' ({distances[idx]:.2f})'

        face_w = max(1, x1 - x0)
        scale = max(0.55, min(1.0, face_w / 135.0))
        cv2.rectangle(image, (x0, y0), (x1, y1), color, 2)
        (tw, th), _ = cv2.getTextSize(label, cv2.FONT_HERSHEY_SIMPLEX, scale, 1)
        pad = 5
        ly2 = max(y0 - 4, th + pad * 2 + 2)
        ly1 = ly2 - th - pad * 2
        lx1, lx2 = x0, x0 + tw + pad * 2
        cv2.rectangle(image, (lx1, ly1), (lx2, ly2), (28, 28, 28), cv2.FILLED)
        cv2.putText(
            image,
            label,
            (lx1 + pad, ly2 - pad),
            cv2.FONT_HERSHEY_SIMPLEX,
            scale,
            (255, 255, 255),
            1,
            cv2.LINE_AA,
        )
    return image


def get_participation_stats():
    """统计每位学生参加活动的次数。"""
    from sqlalchemy import func

    rows = (
        db.session.query(
            ActivityParticipant.student_id,
            ActivityParticipant.student_name,
            func.count(ActivityParticipant.id).label('count'),
        )
        .group_by(ActivityParticipant.student_id, ActivityParticipant.student_name)
        .order_by(func.count(ActivityParticipant.id).desc())
        .all()
    )
    return [{'student_id': row.student_id, 'student_name': row.student_name, 'count': row.count} for row in rows]
