"""人脸检测服务：服务于单人注册和合照识别。"""

import os
import cv2
import face_recognition
import numpy as np

_deepface_state = {
    'checked': False,
    'available': False,
    'error': None,
    'extract_faces': None,
}

GROUP_DETECT_PRIMARY_SIDE = int(os.environ.get('GROUP_DETECT_PRIMARY_SIDE', '1600'))
GROUP_DETECT_TILE_SIZE = int(os.environ.get('GROUP_DETECT_TILE_SIZE', '960'))
GROUP_DETECT_TILE_OVERLAP = float(os.environ.get('GROUP_DETECT_TILE_OVERLAP', '0.18'))
GROUP_DETECT_MIN_CONFIDENCE = float(os.environ.get('GROUP_DETECT_MIN_CONFIDENCE', '0.8'))
GROUP_DETECT_MAX_UPSCALE = float(os.environ.get('GROUP_DETECT_MAX_UPSCALE', '1.0'))
GROUP_MIN_EXPECTED_FACES = int(os.environ.get('GROUP_MIN_EXPECTED_FACES', '10'))
GROUP_MIN_FACE_SIZE = int(os.environ.get('GROUP_MIN_FACE_SIZE', '32'))
GROUP_NMS_IOU = float(os.environ.get('GROUP_NMS_IOU', '0.28'))
GROUP_VALIDATE_MIN_SIDE = int(os.environ.get('GROUP_VALIDATE_MIN_SIDE', '220'))

REGISTER_DETECTOR_BACKENDS = os.environ.get('REGISTER_DETECTOR_BACKENDS', 'retinaface,mtcnn,opencv')
REGISTER_DETECT_SIDES = os.environ.get('REGISTER_DETECT_SIDES', '1200,1800,2400')
REGISTER_DETECT_MIN_CONFIDENCE = float(os.environ.get('REGISTER_DETECT_MIN_CONFIDENCE', '0.8'))
REGISTER_DETECT_MAX_UPSCALE = float(os.environ.get('REGISTER_DETECT_MAX_UPSCALE', '2.0'))


def _deepface_backend():
    """???? DeepFace????? `extract_faces` ???"""
    if _deepface_state['checked']:
        return _deepface_state

    _deepface_state['checked'] = True
    try:
        from deepface import DeepFace  # type: ignore

        _deepface_state['available'] = True
        _deepface_state['error'] = None
        _deepface_state['extract_faces'] = DeepFace.extract_faces
    except Exception as exc:
        _deepface_state['available'] = False
        _deepface_state['error'] = str(exc)
        _deepface_state['extract_faces'] = None
    return _deepface_state


def deepface_status():
    """?? DeepFace ??????????"""
    state = _deepface_backend()
    return {
        'available': state.get('available', False),
        'error': state.get('error'),
    }


def _parse_csv_ints(value, default):
    """???????????????????"""
    raw = value if value is not None else default
    out = []
    for part in str(raw).split(','):
        part = part.strip()
        if not part:
            continue
        try:
            out.append(int(part))
        except ValueError:
            continue
    return out or [int(x) for x in str(default).split(',') if x.strip()]


def _parse_csv_strs(value, default):
    """?????????????????????"""
    raw = value if value is not None else default
    out = [part.strip() for part in str(raw).split(',') if part.strip()]
    return out or [part.strip() for part in str(default).split(',') if part.strip()]


def _clip_box(box, shape):
    """?????????????"""
    y0, x1, y1, x0 = [int(v) for v in box]
    h, w = shape[:2]
    y0 = max(0, min(y0, h))
    y1 = max(0, min(y1, h))
    x0 = max(0, min(x0, w))
    x1 = max(0, min(x1, w))
    if y1 <= y0 or x1 <= x0:
        return None
    return y0, x1, y1, x0


def _box_area(box):
    """????????"""
    return max(0, box[2] - box[0]) * max(0, box[1] - box[3])


def _box_min_side(box):
    """??????????????????"""
    return min(max(0, box[2] - box[0]), max(0, box[1] - box[3]))


def _box_center(box):
    """???????????"""
    return ((box[3] + box[1]) / 2.0, (box[0] + box[2]) / 2.0)


def _iou(a, b):
    """????????????"""
    ay0, ax1, ay1, ax0 = a
    by0, bx1, by1, bx0 = b
    inter_x0 = max(ax0, bx0)
    inter_y0 = max(ay0, by0)
    inter_x1 = min(ax1, bx1)
    inter_y1 = min(ay1, by1)
    iw = max(0, inter_x1 - inter_x0)
    ih = max(0, inter_y1 - inter_y0)
    inter = iw * ih
    if inter == 0:
        return 0.0
    area_a = _box_area(a)
    area_b = _box_area(b)
    return inter / float(area_a + area_b - inter + 1e-6)


def _close_enough(a, b):
    """???? IoU ??????????????????????"""
    if _iou(a, b) >= 0.18:
        return True

    ax, ay = _box_center(a)
    bx, by = _box_center(b)
    dist = float(np.hypot(ax - bx, ay - by))
    ref = max(12.0, min(_box_min_side(a), _box_min_side(b)) * 0.65)
    return dist <= ref


def _dedupe_boxes(boxes, shape, min_face_size, iou_th):
    """? IoU ??????"""
    clipped = []
    for box in boxes:
        clipped_box = _clip_box(box, shape)
        if clipped_box is None:
            continue
        if min(clipped_box[2] - clipped_box[0], clipped_box[1] - clipped_box[3]) < min_face_size:
            continue
        clipped.append(clipped_box)

    kept = []
    for box in sorted(clipped, key=_box_area, reverse=True):
        if all(_iou(box, old) < iou_th for old in kept):
            kept.append(box)
    return kept


def _dedupe_boxes_fast(boxes, shape, min_face_size, iou_th):
    """? IoU ?????????????????"""
    clipped = []
    for box in boxes:
        clipped_box = _clip_box(box, shape)
        if clipped_box is None:
            continue
        if _box_min_side(clipped_box) < min_face_size:
            continue
        clipped.append(clipped_box)

    kept = []
    for box in sorted(clipped, key=_box_area, reverse=True):
        duplicate = False
        for old in kept:
            if _iou(box, old) >= iou_th or _close_enough(box, old):
                duplicate = True
                break
        if not duplicate:
            kept.append(box)
    return kept


def _resize_long_side(rgb, target_side):
    """?????????????"""
    h, w = rgb.shape[:2]
    long_side = max(h, w)
    if long_side <= 0 or long_side == target_side:
        return rgb, 1.0
    scale = target_side / float(long_side)
    interpolation = cv2.INTER_AREA if scale < 1.0 else cv2.INTER_CUBIC
    resized = cv2.resize(
        rgb,
        (max(1, int(round(w * scale))), max(1, int(round(h * scale)))),
        interpolation=interpolation,
    )
    return resized, scale


def _resize_short_side(rgb, target_side):
    """?????????????"""
    h, w = rgb.shape[:2]
    short_side = min(h, w)
    if short_side <= 0 or short_side == target_side:
        return rgb, 1.0
    scale = target_side / float(short_side)
    interpolation = cv2.INTER_AREA if scale < 1.0 else cv2.INTER_CUBIC
    resized = cv2.resize(
        rgb,
        (max(1, int(round(w * scale))), max(1, int(round(h * scale)))),
        interpolation=interpolation,
    )
    return resized, scale


def _enhance_rgb(rgb):
    """?? CLAHE + ????????????????"""
    ycrcb = cv2.cvtColor(rgb, cv2.COLOR_RGB2YCrCb)
    ycrcb[:, :, 0] = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8)).apply(ycrcb[:, :, 0])
    enhanced = cv2.cvtColor(ycrcb, cv2.COLOR_YCrCb2RGB)
    blur = cv2.GaussianBlur(enhanced, (0, 0), 1.1)
    sharpened = cv2.addWeighted(enhanced, 1.35, blur, -0.35, 0)
    return sharpened


def _global_views(rgb, side_values, max_upscale=1.45):
    """????????????"""
    long_side = max(rgb.shape[:2])
    sides = list(side_values)
    if long_side not in sides:
        sides.append(long_side)

    ordered = []
    seen = set()
    for side in sorted(sides):
        side = max(960, int(side))
        if side in seen:
            continue
        seen.add(side)
        ordered.append(side)

    views = []
    for side in ordered:
        if side > int(long_side * max_upscale) and side != long_side:
            continue
        resized, scale = _resize_long_side(rgb, side)
        views.append((resized, 1.0 / float(scale)))
    return views or [(rgb, 1.0)]


def _iter_tiles(rgb, tile_size, overlap):
    """? tile ??????????????"""
    h, w = rgb.shape[:2]
    stride = max(64, int(round(tile_size * (1.0 - overlap))))
    if h <= tile_size and w <= tile_size:
        yield (0, 0, rgb)
        return

    ys = list(range(0, max(h - tile_size + 1, 1), stride))
    xs = list(range(0, max(w - tile_size + 1, 1), stride))
    if not ys or ys[-1] != max(0, h - tile_size):
        ys.append(max(0, h - tile_size))
    if not xs or xs[-1] != max(0, w - tile_size):
        xs.append(max(0, w - tile_size))

    for y in ys:
        for x in xs:
            yield (x, y, rgb[y:min(h, y + tile_size), x:min(w, x + tile_size)])


def _extract_boxes_deepface(rgb, backend, min_confidence, align=False):
    """?? DeepFace ??????????"""
    state = _deepface_backend()
    if not state['available'] or not state.get('extract_faces'):
        return []

    extract_faces = state['extract_faces']
    try:
        result = extract_faces(
            img_path=rgb,
            detector_backend=backend,
            enforce_detection=False,
            align=align,
        )
    except Exception as exc:
        print(f'[face_detection] deepface backend={backend} failed: {exc}', flush=True)
        return []

    boxes = []
    for item in result or []:
        area = item.get('facial_area') or {}
        confidence = float(item.get('confidence', 1.0) or 0.0)
        x = int(area.get('x', 0))
        y = int(area.get('y', 0))
        w = int(area.get('w', 0))
        h = int(area.get('h', 0))
        if w <= 0 or h <= 0:
            continue
        if confidence < min_confidence:
            continue
        boxes.append((y, x + w, y + h, x))
    return boxes


def _collect_deepface_candidates(rgb, backends, variants, global_sides, tile_sizes, overlap, min_conf):
    """??????????? tile ????????????????"""
    candidates = []
    used_backends = set()
    max_upscale = GROUP_DETECT_MAX_UPSCALE

    for backend in backends:
        for variant_name, variant in variants:
            for view_idx, (view_rgb, inv_scale) in enumerate(_global_views(variant, global_sides, max_upscale=max_upscale)):
                for box in _extract_boxes_deepface(view_rgb, backend, min_confidence=min_conf, align=False):
                    candidates.append(
                        {
                            'box': (
                                int(round(box[0] * inv_scale)),
                                int(round(box[1] * inv_scale)),
                                int(round(box[2] * inv_scale)),
                                int(round(box[3] * inv_scale)),
                            ),
                            'backend': backend,
                            'source': f'{backend}:{variant_name}:global:{view_idx}',
                            'confidence': float(min_conf),
                        }
                    )
                    used_backends.add(backend)

            for tile_size in tile_sizes:
                for tile_idx, (x, y, tile) in enumerate(_iter_tiles(variant, tile_size, overlap)):
                    if min(tile.shape[:2]) < 160:
                        continue
                    for box in _extract_boxes_deepface(tile, backend, min_confidence=min_conf, align=False):
                        candidates.append(
                            {
                                'box': (box[0] + y, box[1] + x, box[2] + y, box[3] + x),
                                'backend': backend,
                                'source': f'{backend}:{variant_name}:tile:{tile_size}:{tile_idx}',
                                'confidence': float(min_conf),
                            }
                        )
                        used_backends.add(backend)

    return candidates, used_backends


def _cluster_candidates(candidates, shape, min_face_size):
    """?????????????????"""
    clusters = []
    for item in candidates:
        box = _clip_box(item.get('box'), shape)
        if box is None or _box_min_side(box) < min_face_size:
            continue

        matched = None
        for cluster in clusters:
            if _close_enough(cluster['box'], box):
                matched = cluster
                break

        if matched is None:
            clusters.append(
                {
                    'box': box,
                    'boxes': [box],
                    'hits': 1,
                    'backends': {item.get('backend')},
                    'sources': {item.get('source')},
                    'confidence': float(item.get('confidence') or 0.0),
                }
            )
            continue

        matched['boxes'].append(box)
        matched['hits'] += 1
        matched['backends'].add(item.get('backend'))
        matched['sources'].add(item.get('source'))
        matched['confidence'] = max(matched['confidence'], float(item.get('confidence') or 0.0))
        coords = np.asarray(matched['boxes'], dtype=np.float32)
        matched['box'] = tuple(np.mean(coords, axis=0).round().astype(int).tolist())

    return clusters


def _box_expand(box, shape, ratio):
    """?????????"""
    y0, x1, y1, x0 = box
    h, w = shape[:2]
    face_h = max(1, y1 - y0)
    face_w = max(1, x1 - x0)
    pad_y = int(round(face_h * ratio))
    pad_x = int(round(face_w * ratio))
    y0 = max(0, y0 - pad_y)
    y1 = min(h, y1 + pad_y)
    x0 = max(0, x0 - pad_x)
    x1 = min(w, x1 + pad_x)
    if y1 <= y0 or x1 <= x0:
        return None
    return y0, x1, y1, x0


def _validate_cluster(rgb, cluster):
    """?????????????????/landmark ???????"""
    expanded = _box_expand(cluster['box'], rgb.shape, 0.22)
    if expanded is None:
        return False

    y0, x1, y1, x0 = expanded
    crop = rgb[y0:y1, x0:x1]
    if crop.size == 0:
        return False

    crop, scale = _resize_short_side(crop, GROUP_VALIDATE_MIN_SIDE)
    box = cluster['box']
    local_box = (
        int(round((box[0] - y0) * scale)),
        int(round((box[1] - x0) * scale)),
        int(round((box[2] - y0) * scale)),
        int(round((box[3] - x0) * scale)),
    )
    local_box = _clip_box(local_box, crop.shape)
    if local_box is None:
        return False

    try:
        encodings = face_recognition.face_encodings(
            crop,
            known_face_locations=[local_box],
            num_jitters=1,
            model='small',
        )
        if encodings:
            return True
    except Exception:
        pass

    try:
        landmarks = face_recognition.face_landmarks(crop, [local_box])
        return bool(landmarks)
    except Exception:
        return False


def detect_faces_for_group(rgb):
    """???????????? RetinaFace??????? tile ??????????"""
    det_side = GROUP_DETECT_PRIMARY_SIDE
    tile_size = GROUP_DETECT_TILE_SIZE
    min_expected = GROUP_MIN_EXPECTED_FACES
    overlap = GROUP_DETECT_TILE_OVERLAP
    min_conf = GROUP_DETECT_MIN_CONFIDENCE
    min_face_size = GROUP_MIN_FACE_SIZE
    iou_th = GROUP_NMS_IOU

    state = _deepface_backend()
    if not state['available']:
        return [], 'retinaface_unavailable'

    work_rgb, scale = _resize_long_side(rgb, det_side)
    inv_scale = 1.0 / float(scale) if scale else 1.0

    boxes = []
    for box in _extract_boxes_deepface(work_rgb, 'retinaface', min_confidence=min_conf, align=False):
        boxes.append(
            (
                int(round(box[0] * inv_scale)),
                int(round(box[1] * inv_scale)),
                int(round(box[2] * inv_scale)),
                int(round(box[3] * inv_scale)),
            )
        )

    all_boxes = _dedupe_boxes_fast(boxes, rgb.shape, min_face_size, iou_th)
    used_backend = 'retinaface'

    if len(all_boxes) >= min_expected:
        return all_boxes, used_backend

    tile_boxes = []
    for x, y, tile in _iter_tiles(work_rgb, tile_size, overlap):
        if min(tile.shape[:2]) < 160:
            continue
        for box in _extract_boxes_deepface(tile, 'retinaface', min_confidence=min_conf, align=False):
            tile_boxes.append(
                (
                    int(round((box[0] + y) * inv_scale)),
                    int(round((box[1] + x) * inv_scale)),
                    int(round((box[2] + y) * inv_scale)),
                    int(round((box[3] + x) * inv_scale)),
                )
            )

    if tile_boxes:
        all_boxes = _dedupe_boxes_fast(all_boxes + tile_boxes, rgb.shape, min_face_size, iou_th)
        used_backend = 'retinaface+tile'

    return all_boxes, used_backend


def detect_single_face_for_register(rgb):
    """
    单人注册照人脸检测：先走快通道，命中即返回；漏检时才退化到多后端、多尺寸搜索。

    快通道（几乎所有正脸都能命中）：
        retinaface 在原图（或 long_side<=1200 的轻量缩放）上一次调用。
    慢通道（fast 失败时）：
        遍历配置中的 backends × sides × (原图/增强)，并最后再回退到 face_recognition.HOG。
    """
    backends = _parse_csv_strs(
        REGISTER_DETECTOR_BACKENDS,
        REGISTER_DETECTOR_BACKENDS,
    )
    sides = _parse_csv_ints(
        REGISTER_DETECT_SIDES,
        REGISTER_DETECT_SIDES,
    )
    min_conf = REGISTER_DETECT_MIN_CONFIDENCE
    min_face_size = max(48, GROUP_MIN_FACE_SIZE)

    # ---------- Fast path ----------
    # 单人正脸不需要 >1024 的分辨率；过大反而成倍增加 RetinaFace 推理时间。
    fast_target = 1024 if max(rgb.shape[:2]) > 1024 else max(rgb.shape[:2])
    fast_view, fast_scale = _resize_long_side(rgb, fast_target)
    fast_inv = 1.0 / float(fast_scale) if fast_scale else 1.0

    # 先尝试 OpenCV 级联（热启 70ms 级别，对单人正脸够用），比 RetinaFace 快 40 倍。
    for fast_backend in ('opencv', 'retinaface'):
        try:
            fast_boxes = _extract_boxes_deepface(fast_view, fast_backend, min_confidence=min_conf, align=False)
        except Exception:
            fast_boxes = []
        if fast_boxes:
            boxes = []
            for box in fast_boxes:
                boxes.append((
                    int(round(box[0] * fast_inv)),
                    int(round(box[1] * fast_inv)),
                    int(round(box[2] * fast_inv)),
                    int(round(box[3] * fast_inv)),
                ))
            boxes = _dedupe_boxes(boxes, rgb.shape, min_face_size=min_face_size, iou_th=0.25)
            if boxes:
                return boxes

    # ---------- Slow path ----------
    boxes = []
    enhanced = None
    for backend in backends:
        backend_boxes = []
        # 第一遍只用原图 + 配置最小尺寸；找到就跳出，不必跑 enhanced
        for view_rgb, inv_scale in _global_views(
            rgb, sides[:1] or sides,
            max_upscale=REGISTER_DETECT_MAX_UPSCALE,
        ):
            for box in _extract_boxes_deepface(view_rgb, backend, min_confidence=min_conf, align=False):
                backend_boxes.append((
                    int(round(box[0] * inv_scale)),
                    int(round(box[1] * inv_scale)),
                    int(round(box[2] * inv_scale)),
                    int(round(box[3] * inv_scale)),
                ))
        boxes = _dedupe_boxes(backend_boxes, rgb.shape, min_face_size=min_face_size, iou_th=0.25)
        if boxes:
            return boxes

        # 第二遍：本 backend 加增强 + 全尺寸
        if enhanced is None:
            enhanced = _enhance_rgb(rgb)
        for variant in (enhanced,):
            for view_rgb, inv_scale in _global_views(
                variant, sides,
                max_upscale=REGISTER_DETECT_MAX_UPSCALE,
            ):
                for box in _extract_boxes_deepface(view_rgb, backend, min_confidence=min_conf, align=False):
                    backend_boxes.append((
                        int(round(box[0] * inv_scale)),
                        int(round(box[1] * inv_scale)),
                        int(round(box[2] * inv_scale)),
                        int(round(box[3] * inv_scale)),
                    ))
        boxes = _dedupe_boxes(backend_boxes, rgb.shape, min_face_size=min_face_size, iou_th=0.25)
        if boxes:
            return boxes

    if not boxes:
        fallback = []
        for upsample in (0, 1, 2):
            try:
                found = face_recognition.face_locations(rgb, number_of_times_to_upsample=upsample, model='hog')
            except Exception:
                continue
            fallback.extend(found)
        boxes = _dedupe_boxes(fallback, rgb.shape, min_face_size=min_face_size, iou_th=0.25)

    return boxes
