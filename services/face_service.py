"""活体检测服务：基于 Haar + dlib 执行张嘴与头部移动挑战。"""

import base64
import os
import random
import time
from collections import deque

import cv2
import dlib
import numpy as np
from flask import current_app
from imutils import face_utils
from scipy.spatial import distance as dist

from services import anti_spoof_service

LIVENESS_SESSION_MINUTES = int(os.environ.get('LIVENESS_SESSION_MINUTES', '5'))
REQUIRED_MOUTHS = int(os.environ.get('REQUIRED_MOUTHS', '1'))
LIVENESS_DEBUG = os.environ.get('LIVENESS_DEBUG', 'true').lower() in ('1', 'true', 'yes', 'on')

LIVENESS_MOUTH_CLOSED_MAX = float(os.environ.get('LIVENESS_MOUTH_CLOSED_MAX', '0.22'))
LIVENESS_MOUTH_DYNAMIC_RATIO = float(os.environ.get('LIVENESS_MOUTH_DYNAMIC_RATIO', '1.55'))
LIVENESS_MOUTH_RISE = float(os.environ.get('LIVENESS_MOUTH_RISE', '0.08'))
MOUTH_MIN_OPEN_FRAMES = int(os.environ.get('MOUTH_MIN_OPEN_FRAMES', '2'))
MOUTH_MIN_CLOSED_FRAMES = int(os.environ.get('MOUTH_MIN_CLOSED_FRAMES', '1'))
MOUTH_COOLDOWN_SECONDS = float(os.environ.get('MOUTH_COOLDOWN_SECONDS', '0.6'))
LIVENESS_MAX_FACE_SHIFT_RATIO = float(os.environ.get('LIVENESS_MAX_FACE_SHIFT_RATIO', '0.38'))
LIVENESS_MAX_PENDING_FRAMES = int(os.environ.get('LIVENESS_MAX_PENDING_FRAMES', '8'))
LIVENESS_MOVE_CHALLENGE_RATIO = float(os.environ.get('LIVENESS_MOVE_CHALLENGE_RATIO', '0.16'))
LIVENESS_MOVE_NOSE_SHIFT_RATIO = float(os.environ.get('LIVENESS_MOVE_NOSE_SHIFT_RATIO', '0.045'))
LIVENESS_MOVE_MIN_FRAMES = int(os.environ.get('LIVENESS_MOVE_MIN_FRAMES', '2'))
LIVENESS_MOVE_RETURN_CENTER_RATIO = float(os.environ.get('LIVENESS_MOVE_RETURN_CENTER_RATIO', '0.08'))
LIVENESS_MOVE_RETURN_NOSE_RATIO = float(os.environ.get('LIVENESS_MOVE_RETURN_NOSE_RATIO', '0.025'))

_predictor = None
_haar = None
_face_runtime = {}

(M_START, M_END) = face_utils.FACIAL_LANDMARKS_IDXS['inner_mouth']
NOSE_TIP_INDEX = 33


def _get_models():
    global _predictor, _haar
    if _predictor is None:
        _predictor = dlib.shape_predictor(current_app.config['DLIB_MODEL_PATH'])
    if _haar is None:
        _haar = cv2.CascadeClassifier(cv2.data.haarcascades + 'haarcascade_frontalface_default.xml')
    return _predictor, _haar


def _build_runtime():
    primary_direction = random.choice(('left', 'right'))
    secondary_direction = 'right' if primary_direction == 'left' else 'left'
    return {
        'frame_index': 0,
        'closed_mars': deque(maxlen=20),
        'mar_history': deque(maxlen=3),
        'baseline_mar': None,
        'open_threshold': None,
        'closed_threshold': None,
        'closed_frames': 0,
        'open_frames': 0,
        'pending_frames': 0,
        'ready_closed': False,
        'last_action_ts': 0.0,
        'last_face_box': None,
        'missing_frames': 0,
        'current_step': 'mouth',
        'instruction': '请正对摄像头，先闭嘴，再自然张嘴一次',
        'challenge_direction': primary_direction,
        'secondary_direction': secondary_direction,
        'move_baseline_center_x': None,
        'move_baseline_nose_ratio': None,
        'move_frames': 0,
    }


def _get_runtime(token):
    st = _face_runtime.get(token)
    if st is None:
        st = _build_runtime()
        _face_runtime[token] = st
    return st


def init_liveness_session(token):
    st = _build_runtime()
    _face_runtime[token] = st
    anti_spoof_service.begin_session(token, st['challenge_direction'])
    return {
        'required_mouths': REQUIRED_MOUTHS,
        'required_blinks': REQUIRED_MOUTHS,
        'required_actions': REQUIRED_MOUTHS + 3,
        'instruction': st['instruction'],
        'current_step': st['current_step'],
    }


def clear_liveness_session(token):
    _face_runtime.pop(token, None)
    anti_spoof_service.clear_session(token)


def _select_face_box(rects, runtime_state):
    if len(rects) == 1:
        return rects[0]

    last_box = runtime_state.get('last_face_box')
    if not last_box:
        return max(rects, key=lambda rect: rect[2] * rect[3])

    last_x, last_y, last_w, last_h = last_box
    last_cx = last_x + last_w / 2.0
    last_cy = last_y + last_h / 2.0

    def _score(rect):
        x, y, w, h = rect
        cx = x + w / 2.0
        cy = y + h / 2.0
        center_dist = ((cx - last_cx) ** 2 + (cy - last_cy) ** 2) ** 0.5
        return (center_dist, -(w * h))

    return min(rects, key=_score)


def _compute_mar(inner_mouth):
    a = dist.euclidean(inner_mouth[1], inner_mouth[7])
    b = dist.euclidean(inner_mouth[2], inner_mouth[6])
    c = dist.euclidean(inner_mouth[3], inner_mouth[5])
    d = dist.euclidean(inner_mouth[0], inner_mouth[4])
    return (a + b + c) / (3.0 * max(d, 1e-6))


def _debug_print(session, st, **payload):
    if not LIVENESS_DEBUG:
        return
    blur_median = payload.get('blur_median')
    print(
        f'[liveness] token={session.session_token[:8]} frame={st.get("frame_index", 0)} '
        f'blur_median={blur_median}',
        flush=True,
    )


def _prompt_direction(direction):
    # challenge_direction 按图像坐标计算；提示语改成按“用户自己的方向”描述。
    return '右侧' if direction == 'left' else '左侧'


def _reset_mouth_state(session, st, reason):
    session.below_threshold = False
    st['closed_frames'] = 0
    st['open_frames'] = 0
    st['pending_frames'] = 0
    st['ready_closed'] = False
    _debug_print(session, st, event='reset', reason=reason, action_count=session.blink_count)


def _step_instruction(st, session):
    if session.passed:
        return '活体验证通过，请开始签到'
    if st['current_step'] == 'move_primary':
        return f'检测到张嘴，请将头部向{_prompt_direction(st["challenge_direction"])}轻移'
    if st['current_step'] == 'move_return':
        return '请把头部回到画面中间'
    if st['current_step'] == 'move_secondary':
        return f'请再将头部向{_prompt_direction(st["secondary_direction"])}轻移'

    remaining = max(0, REQUIRED_MOUTHS - session.blink_count)
    if remaining <= 0:
        return '请继续完成头部移动动作'
    if session.blink_count == 0:
        return '请正对摄像头，先闭嘴，再自然张嘴一次'
    return f'请再完成 {remaining} 次张嘴动作'


def _build_response(session, st, face_found, mar=None, move_ratio=0.0, mouth_open_detected=False):
    instruction = _step_instruction(st, session)
    st['instruction'] = instruction
    return {
        'mar': round(float(mar), 3) if mar is not None else None,
        'mouth_open_detected': mouth_open_detected,
        'action_count': session.blink_count,
        'blink_count': session.blink_count,
        'mouth_count': session.blink_count,
        'required_blinks': REQUIRED_MOUTHS,
        'required_mouths': REQUIRED_MOUTHS,
        'passed': session.passed,
        'face_found': face_found,
        'current_step': st.get('current_step', 'mouth'),
        'instruction': instruction,
        'move_ratio': round(float(move_ratio), 3),
    }


def check_liveness_frame(image_np, session):
    predictor, haar = _get_models()
    st = _get_runtime(session.session_token)
    st['frame_index'] += 1

    if image_np is None:
        _debug_print(session, st, event='empty_frame')
        return _build_response(session, st, face_found=False)

    gray = cv2.cvtColor(image_np, cv2.COLOR_BGR2GRAY)
    rects = haar.detectMultiScale(gray, scaleFactor=1.1, minNeighbors=4, minSize=(60, 60))
    if len(rects) == 0:
        st['missing_frames'] += 1
        st['move_frames'] = 0
        if st['missing_frames'] >= 2 and not str(st['current_step']).startswith('move'):
            _reset_mouth_state(session, st, 'missing_face')
        _debug_print(session, st, event='no_face', missing_frames=st['missing_frames'])
        return _build_response(session, st, face_found=False)

    st['missing_frames'] = 0
    x, y, w, h = _select_face_box(rects, st)
    last_box = st.get('last_face_box')
    move_ratio = 0.0
    if last_box:
        last_x, last_y, last_w, last_h = last_box
        move_ratio = max(
            abs((x + w / 2.0) - (last_x + last_w / 2.0)) / max(last_w, 1),
            abs((y + h / 2.0) - (last_y + last_h / 2.0)) / max(last_h, 1),
        )
        if not str(st['current_step']).startswith('move') and move_ratio > LIVENESS_MAX_FACE_SHIFT_RATIO:
            _reset_mouth_state(session, st, 'face_shift')
    st['last_face_box'] = (int(x), int(y), int(w), int(h))

    rect = dlib.rectangle(int(x), int(y), int(x + w), int(y + h))
    shape = predictor(gray, rect)
    shape_np = face_utils.shape_to_np(shape)
    inner_mouth = shape_np[M_START:M_END]
    mar = _compute_mar(inner_mouth)

    face_box = (int(y), int(x + w), int(y + h), int(x))
    anti_spoof_service.update_session(session.session_token, image_np, face_box=face_box)
    anti_spoof_metrics = anti_spoof_service.get_session_metrics(session.session_token)
    liveness_spoof = anti_spoof_service.assess_liveness_parallel(
        session.session_token,
        image_np=image_np,
        face_box=face_box,
    )
    if liveness_spoof and not liveness_spoof.get('ok'):
        message = liveness_spoof.get('message') or '检测到疑似视频攻击，请重新开始活体验证'
        return {
            'status': 'fake',
            'passed': False,
            'face_found': True,
            'current_step': 'failed',
            'instruction': message,
            'error': message,
        }

    st['mar_history'].append(float(mar))
    smooth_mar = float(np.mean(st['mar_history']))

    if smooth_mar <= LIVENESS_MOUTH_CLOSED_MAX:
        st['closed_mars'].append(smooth_mar)

    if len(st['closed_mars']) >= 5:
        baseline = float(np.median(st['closed_mars']))
        st['baseline_mar'] = baseline
        st['open_threshold'] = max(
            0.30,
            baseline * LIVENESS_MOUTH_DYNAMIC_RATIO,
            baseline + LIVENESS_MOUTH_RISE,
        )
        st['closed_threshold'] = min(LIVENESS_MOUTH_CLOSED_MAX, baseline + 0.03)

    baseline = st.get('baseline_mar')
    open_threshold = (
        st['open_threshold']
        if st['open_threshold'] is not None
        else max(0.30, LIVENESS_MOUTH_CLOSED_MAX + 0.08)
    )
    closed_threshold = st['closed_threshold'] if st['closed_threshold'] is not None else LIVENESS_MOUTH_CLOSED_MAX

    raw_rise = None if baseline is None else (mar - baseline)
    smooth_rise = None if baseline is None else (smooth_mar - baseline)
    smooth_rise_ok = baseline is None or smooth_rise >= LIVENESS_MOUTH_RISE
    raw_rise_ok = baseline is None or raw_rise >= (LIVENESS_MOUTH_RISE * 0.75)
    is_closed = smooth_mar <= closed_threshold
    is_open = (
        (smooth_mar >= open_threshold and smooth_rise_ok)
        or (mar >= (open_threshold - 0.02) and raw_rise_ok)
    )

    mouth_open_detected = False
    now = time.time()

    if st['current_step'] == 'mouth':
        if is_closed:
            st['closed_frames'] += 1
            st['open_frames'] = 0
            if st['closed_frames'] >= MOUTH_MIN_CLOSED_FRAMES:
                st['ready_closed'] = True
                session.below_threshold = False
                st['pending_frames'] = 0
        elif st['ready_closed'] and is_open:
            session.below_threshold = True
            st['open_frames'] += 1
            st['pending_frames'] += 1
        else:
            if session.below_threshold:
                st['pending_frames'] += 1
            else:
                st['closed_frames'] = 0
            st['open_frames'] = 0

        if st['ready_closed'] and st['open_frames'] >= MOUTH_MIN_OPEN_FRAMES:
            if now - st['last_action_ts'] > MOUTH_COOLDOWN_SECONDS:
                session.blink_count += 1
                st['last_action_ts'] = now
                mouth_open_detected = True
            _reset_mouth_state(session, st, 'mouth_detected')
        elif session.below_threshold and st['pending_frames'] >= LIVENESS_MAX_PENDING_FRAMES:
            _reset_mouth_state(session, st, 'pending_timeout')

        if session.blink_count >= REQUIRED_MOUTHS:
            st['current_step'] = 'move_primary'
            st['move_baseline_center_x'] = x + (w / 2.0)
            st['move_baseline_nose_ratio'] = float(shape_np[NOSE_TIP_INDEX][0] - x) / float(max(w, 1))
            st['move_frames'] = 0

    if str(st['current_step']).startswith('move') and not session.passed:
        center_x = x + (w / 2.0)
        delta_center = 0.0
        if st['move_baseline_center_x'] is not None:
            delta_center = (center_x - st['move_baseline_center_x']) / float(max(w, 1))

        nose_ratio = float(shape_np[NOSE_TIP_INDEX][0] - x) / float(max(w, 1))
        base_nose_ratio = st['move_baseline_nose_ratio']
        delta_nose = 0.0 if base_nose_ratio is None else (nose_ratio - base_nose_ratio)

        moved_ok = False
        step = st['current_step']
        if step == 'move_primary':
            if st['challenge_direction'] == 'left':
                moved_ok = delta_center <= -LIVENESS_MOVE_CHALLENGE_RATIO or delta_nose <= -LIVENESS_MOVE_NOSE_SHIFT_RATIO
            else:
                moved_ok = delta_center >= LIVENESS_MOVE_CHALLENGE_RATIO or delta_nose >= LIVENESS_MOVE_NOSE_SHIFT_RATIO
        elif step == 'move_return':
            moved_ok = (
                abs(delta_center) <= LIVENESS_MOVE_RETURN_CENTER_RATIO
                and abs(delta_nose) <= LIVENESS_MOVE_RETURN_NOSE_RATIO
            )
        elif step == 'move_secondary':
            if st['secondary_direction'] == 'left':
                moved_ok = delta_center <= -LIVENESS_MOVE_CHALLENGE_RATIO or delta_nose <= -LIVENESS_MOVE_NOSE_SHIFT_RATIO
            else:
                moved_ok = delta_center >= LIVENESS_MOVE_CHALLENGE_RATIO or delta_nose >= LIVENESS_MOVE_NOSE_SHIFT_RATIO

        st['move_frames'] = st['move_frames'] + 1 if moved_ok else 0
        if st['move_frames'] >= LIVENESS_MOVE_MIN_FRAMES:
            st['move_frames'] = 0
            if step == 'move_primary':
                st['current_step'] = 'move_return'
            elif step == 'move_return':
                st['current_step'] = 'move_secondary'
            else:
                anti_spoof_service.mark_challenge_passed(session.session_token)
                session.passed = True
                _debug_print(session, st, passed=True, action_count=session.blink_count)

    _debug_print(
        session,
        st,
        blur_median=round(float(anti_spoof_metrics.get('blur_median', 0.0)), 3),
    )

    return _build_response(
        session,
        st,
        face_found=True,
        mar=smooth_mar,
        move_ratio=move_ratio,
        mouth_open_detected=mouth_open_detected,
    )


def decode_image(b64_string):
    img_bytes = base64.b64decode(b64_string)
    nparr = np.frombuffer(img_bytes, np.uint8)
    return cv2.imdecode(nparr, cv2.IMREAD_COLOR)
