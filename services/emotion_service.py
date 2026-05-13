"""情绪识别服务：对签到或合照中的人脸区域执行 DeepFace 情绪分类。"""

import cv2
import numpy as np

_DF_READY = False
_DF_FAILED = False


def _ensure_deepface():
    """
    延迟加载并预热 DeepFace 情绪模型。

    返回：
        bool: 模型是否可用。
    """
    global _DF_READY, _DF_FAILED
    if _DF_READY or _DF_FAILED:
        return not _DF_FAILED

    try:
        from deepface import DeepFace

        # 用一张中性灰图触发模型和权重加载，减少首次真实请求的等待时间。
        warm = np.full((112, 112, 3), 128, dtype=np.uint8)
        DeepFace.analyze(
            warm,
            actions=['emotion'],
            enforce_detection=False,
            silent=True,
            detector_backend='skip',
        )
        _DF_READY = True
        return True
    except Exception as exc:
        _DF_FAILED = True
        try:
            print('[emotion_service] DeepFace warmup failed:', exc)
        except Exception:
            pass
        return False


def _pad_crop(image_np, y0, x0, y1, x1, ratio=0.25):
    """
    对人脸框做上下左右扩边，保留少量表情上下文。

    参数：
        image_np: BGR 图像。
        y0, x0, y1, x1: 裁剪框坐标。
        ratio: 扩边比例。
    """
    h, w = image_np.shape[:2]
    bw = x1 - x0
    bh = y1 - y0
    px = int(bw * ratio)
    py = int(bh * ratio)
    yy0 = max(0, y0 - py)
    yy1 = min(h, y1 + py)
    xx0 = max(0, x0 - px)
    xx1 = min(w, x1 + px)
    return image_np[yy0:yy1, xx0:xx1]


def _run_deepface(img):
    """
    调用 DeepFace 执行情绪分析。

    这里使用 `detector_backend='skip'`，因为传入的通常已经是人脸裁剪图，
    可以跳过 DeepFace 内部的人脸检测步骤，减少重复计算。
    """
    from deepface import DeepFace

    result = DeepFace.analyze(
        img,
        actions=['emotion'],
        enforce_detection=False,
        detector_backend='skip',
        silent=True,
    )
    if isinstance(result, list):
        result = result[0]
    return result


def analyze(image_np):
    """
    对一张 BGR 图像执行情绪识别。

    算法说明：
        1. 若图像过小，则先放大到约 224 尺度，提升表情特征稳定性。
        2. DeepFace 返回所有情绪分数与主情绪标签。
        3. 当 `neutral` 仅轻微领先时，回退到第二高分情绪，减少“全是平静”的误判。

    返回：
        str: 情绪标签，如 `happy`、`neutral` 等。
    """
    if image_np is None or image_np.size == 0:
        return 'neutral'
    if not _ensure_deepface():
        return 'neutral'

    try:
        h, w = image_np.shape[:2]
        target = 224
        if max(h, w) < target:
            scale = target / float(max(h, w))
            work = cv2.resize(image_np, (int(w * scale), int(h * scale)), interpolation=cv2.INTER_CUBIC)
        else:
            work = image_np

        result = _run_deepface(work)
        dominant = result.get('dominant_emotion', 'neutral')
        emo_scores = result.get('emotion', {}) or {}

        # 若 neutral 只比第二名略高，则取第二名，减少“过度平静化”的输出。
        if dominant == 'neutral' and emo_scores:
            try:
                ranked = sorted(emo_scores.items(), key=lambda item: item[1], reverse=True)
                if len(ranked) >= 2:
                    top_name, top_value = ranked[0]
                    second_name, second_value = ranked[1]
                    if top_name == 'neutral' and (top_value - second_value) < 12 and second_value > 15:
                        dominant = second_name
            except Exception:
                pass

        return dominant
    except Exception:
        return 'neutral'


def analyze_face_crop(image_np, box=None):
    """
    对大图中的指定人脸框执行情绪识别。

    参数：
        image_np: BGR 图像。
        box: `(top, right, bottom, left)` 格式的人脸框；若为空则直接分析整图。
    """
    if image_np is None or image_np.size == 0:
        return 'neutral'
    if box is None:
        return analyze(image_np)

    try:
        y0, x1, y1, x0 = box
        crop = _pad_crop(image_np, y0, x0, y1, x1, ratio=0.25)
        if crop.size == 0:
            return analyze(image_np)
        return analyze(crop)
    except Exception:
        return analyze(image_np)
