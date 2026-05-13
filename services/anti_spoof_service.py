"""
MiniFASNet 纯模型版反欺骗服务。

核心原则：
1. 推翻 blur / area_var / banding / border 等手工规则。
2. 只调用 MiniFASNet / Silent-Face-Anti-Spoofing 模型输出做判断。
3. 保留原 anti_spoof_service.py 的对外接口，方便直接替换：
   - begin_session
   - update_session
   - mark_challenge_passed
   - clear_session
   - get_session_metrics
   - assess_liveness_parallel
   - verify_session
4. 后端只打印精简日志：real/fake、模型分数、原因。

重要说明：
- 本文件不内置模型权重。
- 你需要把官方 Silent-Face-Anti-Spoofing 仓库放到项目中，例如：
    your_project/
      anti_spoof_service.py
      anti_model/
        Silent-Face-Anti-Spoofing/
          src/
          resources/anti_spoof_models/
          resources/detection_model/
"""

from __future__ import annotations

import os
import sys
import time
import threading
from pathlib import Path
from typing import Any, Dict, Optional, Tuple, List

import cv2
import numpy as np


# ============================================================
# 参数区：只保留模型调用相关参数，不再保留任何手工判定参数
# ============================================================

# 是否启用反欺骗模型。
ANTI_SPOOF_ENABLED = True

# 是否打印精简日志。
ANTI_SPOOF_DEBUG = True

# MiniFASNet 项目根目录。
# 默认读取环境变量 SILENT_FACE_ROOT；如果没有，就尝试当前项目下的 anti_model/Silent-Face-Anti-Spoofing。
_THIS_DIR = Path(__file__).resolve().parent
SILENT_FACE_ROOT = Path(
    os.environ.get(
        "SILENT_FACE_ROOT",
        str(_THIS_DIR / "anti_model" / "Silent-Face-Anti-Spoofing"),
    )
).resolve()

# 官方模型目录。
MINIFASNET_MODEL_DIR = Path(
    os.environ.get(
        "MINIFASNET_MODEL_DIR",
        str(SILENT_FACE_ROOT / "resources" / "anti_spoof_models"),
    )
).resolve()

# 设备 ID。
# -1 表示 CPU。
# 0 表示 cuda:0。官方 AntiSpoofPredict 内部会在 torch.cuda 可用时走 cuda:0，否则走 CPU。
# 生产机器没有 GPU 时，建议保持默认 -1。
MINIFASNET_DEVICE_ID = int(os.environ.get("MINIFASNET_DEVICE_ID", "-1"))

# 模型判真人的阈值。
# 官方 demo 逻辑是多模型融合后 label==1 判 RealFace。
# 这里进一步要求 real_score >= 阈值，避免模型低置信度时误放行。
MINIFASNET_REAL_THRESHOLD = float(os.environ.get("MINIFASNET_REAL_THRESHOLD", "0.80"))

# 活体过程中每隔多少帧跑一次模型，避免每一帧都推理导致后端卡顿。
# 注意：这不是手工判定参数，只是推理频率控制。
MINIFASNET_LIVE_INTERVAL_FRAMES = int(os.environ.get("MINIFASNET_LIVE_INTERVAL_FRAMES", "8"))

# 最终 verify_session 时是否必须已经完成前端随机动作挑战。
# 如果你希望“只要模型判真人就通过”，可以改成 False。
REQUIRE_CHALLENGE_PASSED = True

# 活体挑战通过后，最多允许多少秒内完成最终签到。
# 这是业务时序约束，不是反欺骗图像特征规则。
MAX_CHALLENGE_AGE_SECONDS = float(os.environ.get("MAX_CHALLENGE_AGE_SECONDS", "10.0"))


# ============================================================
# 运行时状态
# ============================================================

_runtime: Dict[str, Dict[str, Any]] = {}
_predictor_lock = threading.Lock()
_predictor = None
_cropper = None
_model_names: Optional[List[str]] = None
_model_import_error: Optional[str] = None


# ============================================================
# 基础工具
# ============================================================

def _now_ts() -> float:
    return time.time()


def _debug_print(session_token: Optional[str], result: Dict[str, Any]) -> None:
    """打印精简日志，不再刷一堆无关参数。"""
    if not ANTI_SPOOF_DEBUG:
        return

    token = (session_token or "")[:8]
    detail = result.get("detail") or {}
    print(
        "[anti_spoof] "
        f"token={token} "
        f"status={result.get('status')} "
        f"ok={result.get('ok')} "
        f"method={result.get('method')} "
        f"message={result.get('message')} "
        f"real_score={detail.get('real_score')} "
        f"fake_score={detail.get('fake_score')} "
        f"label={detail.get('label')} "
        f"frame_count={detail.get('frame_count')} "
        f"reason={detail.get('reason', '-')}",
        flush=True,
    )


def _build_runtime(challenge_direction: Optional[str] = None) -> Dict[str, Any]:
    return {
        "started_at": _now_ts(),
        "last_update_at": None,
        "frame_count": 0,
        "challenge_direction": challenge_direction,
        "challenge_passed": False,
        "challenge_passed_at": None,
        "challenge_pass_frame": 0,
        "challenge_duration": None,
        "last_image": None,
        "last_face_box": None,
        "last_model_check_frame": -1,
        "last_model_result": None,
        "last_result": None,
    }


def _get_runtime(session_token: str) -> Dict[str, Any]:
    st = _runtime.get(session_token)
    if st is None:
        st = _build_runtime()
        _runtime[session_token] = st
    return st


def _fake_result(message: str, detail: Optional[Dict[str, Any]] = None, score: float = 0.0) -> Dict[str, Any]:
    return {
        "ok": False,
        "status": "fake",
        "score": float(score),
        "method": "minifasnet",
        "message": f"fake: {message}",
        "detail": detail or {},
    }


def _real_result(message: str = "MiniFASNet 判定为真人", detail: Optional[Dict[str, Any]] = None, score: float = 1.0) -> Dict[str, Any]:
    return {
        "ok": True,
        "status": "real",
        "score": float(score),
        "method": "minifasnet",
        "message": message,
        "detail": detail or {},
    }


def _face_box_to_bbox(face_box: Tuple[int, int, int, int]) -> List[int]:
    """
    将原项目 face_box 转换成 MiniFASNet cropper 需要的 bbox。

    原项目 face_box 格式一般是：
        (top, right, bottom, left) = (y0, x1, y1, x0)

    Silent-Face-Anti-Spoofing 需要：
        [x, y, w, h]
    """
    y0, x1, y1, x0 = [int(v) for v in face_box]
    w = max(1, x1 - x0)
    h = max(1, y1 - y0)
    return [x0, y0, w, h]


def _prepare_image(image_np: np.ndarray) -> np.ndarray:
    """
    确保图像是 BGR uint8。
    你的后端如果本来就是 cv2 解码出来的 BGR，这里不会改变。
    """
    if image_np is None:
        raise ValueError("image_np is None")
    if image_np.dtype != np.uint8:
        image_np = np.clip(image_np, 0, 255).astype(np.uint8)
    if len(image_np.shape) != 3 or image_np.shape[2] != 3:
        raise ValueError("image_np must be BGR image with shape HxWx3")
    return image_np


# ============================================================
# MiniFASNet 加载与预测
# ============================================================

def _ensure_minifasnet_loaded() -> None:
    """延迟加载 MiniFASNet，避免 Flask 启动时就失败。"""
    global _predictor, _cropper, _model_names, _model_import_error

    if _predictor is not None and _cropper is not None and _model_names is not None:
        return

    with _predictor_lock:
        if _predictor is not None and _cropper is not None and _model_names is not None:
            return

        try:
            if not SILENT_FACE_ROOT.exists():
                raise RuntimeError(f"MiniFASNet 项目目录不存在: {SILENT_FACE_ROOT}")
            if not MINIFASNET_MODEL_DIR.exists():
                raise RuntimeError(f"MiniFASNet 模型目录不存在: {MINIFASNET_MODEL_DIR}")

            root_str = str(SILENT_FACE_ROOT)
            if root_str not in sys.path:
                sys.path.insert(0, root_str)

            # 这些模块来自官方 Silent-Face-Anti-Spoofing 仓库。
            from src.anti_spoof_predict import AntiSpoofPredict  # type: ignore
            from src.generate_patches import CropImage  # type: ignore

            model_names = [
                name for name in os.listdir(str(MINIFASNET_MODEL_DIR))
                if name.endswith((".pth", ".pth.tar"))
            ]
            model_names.sort()
            if not model_names:
                raise RuntimeError(f"模型目录中没有 .pth 模型文件: {MINIFASNET_MODEL_DIR}")

            # 官方 AntiSpoofPredict 在 __init__ 里用相对路径读取
            # ./resources/detection_model/Widerface-RetinaFace.caffemodel。
            # Flask/后端项目的 cwd 往往不是 Silent-Face-Anti-Spoofing 根目录，
            # 因此这里临时切 cwd，避免 detector 模型加载失败。
            old_cwd = os.getcwd()
            try:
                os.chdir(str(SILENT_FACE_ROOT))
                _predictor = AntiSpoofPredict(MINIFASNET_DEVICE_ID)
            finally:
                os.chdir(old_cwd)

            _cropper = CropImage()
            _model_names = model_names
            _model_import_error = None

        except Exception as exc:
            _model_import_error = str(exc)
            raise


def _predict_minifasnet(image_np: np.ndarray, face_box: Optional[Tuple[int, int, int, int]] = None) -> Dict[str, Any]:
    """
    调用 MiniFASNet 做一次反欺骗预测。

    返回字段：
    - is_real: 是否真人
    - real_score: 真人分数
    - fake_score: 非真人分数
    - label: 模型融合后的类别，官方约定 label==1 为 RealFace
    - raw_prediction: 三分类融合概率
    """
    _ensure_minifasnet_loaded()
    assert _predictor is not None
    assert _cropper is not None
    assert _model_names is not None

    image = _prepare_image(image_np)

    # 优先使用你现有系统已经检测到的人脸框，避免官方 RetinaFace 检测器和你的人脸框不一致。
    # 如果调用方没有传 face_box，则使用官方 detector 自动检测。
    if face_box is not None:
        bbox = _face_box_to_bbox(face_box)
    else:
        bbox = _predictor.get_bbox(image)

    from src.utility import parse_model_name  # type: ignore

    prediction = np.zeros((1, 3), dtype=np.float64)
    used_models = []

    for model_name in _model_names:
        h_input, w_input, _model_type, scale = parse_model_name(model_name)
        param = {
            "org_img": image,
            "bbox": bbox,
            "scale": scale,
            "out_w": w_input,
            "out_h": h_input,
            "crop": True,
        }
        if scale is None:
            param["crop"] = False

        img = _cropper.crop(**param)
        model_path = str(MINIFASNET_MODEL_DIR / model_name)
        prediction += _predictor.predict(img, model_path)
        used_models.append(model_name)

    model_count = max(1, len(used_models))
    avg_prediction = prediction / float(model_count)

    label = int(np.argmax(avg_prediction))
    real_score = float(avg_prediction[0][1])
    fake_score = float(max(avg_prediction[0][0], avg_prediction[0][2]))
    confidence = float(avg_prediction[0][label])

    is_real = bool(label == 1 and real_score >= MINIFASNET_REAL_THRESHOLD)

    return {
        "is_real": is_real,
        "label": label,
        "confidence": confidence,
        "real_score": real_score,
        "fake_score": fake_score,
        "threshold": MINIFASNET_REAL_THRESHOLD,
        "bbox": bbox,
        "models": used_models,
        "raw_prediction": avg_prediction.round(6).tolist(),
    }


def _model_result_to_response(session_token: str, model_result: Dict[str, Any], frame_count: int, reason_prefix: str = "") -> Dict[str, Any]:
    """把模型输出转换成原后端统一返回格式。"""
    label = model_result.get("label")
    real_score = float(model_result.get("real_score", 0.0))
    fake_score = float(model_result.get("fake_score", 0.0))
    threshold = float(model_result.get("threshold", MINIFASNET_REAL_THRESHOLD))

    detail = {
        "frame_count": frame_count,
        "label": label,
        "real_score": round(real_score, 6),
        "fake_score": round(fake_score, 6),
        "threshold": threshold,
        "reason": "MiniFASNet 模型判定",
        "raw_prediction": model_result.get("raw_prediction"),
        "bbox": model_result.get("bbox"),
        "models": model_result.get("models"),
    }

    if model_result.get("is_real"):
        result = _real_result(
            message="MiniFASNet 判定为真人",
            detail=detail,
            score=real_score,
        )
    else:
        reason = f"判定为非真人(real_score={real_score:.4f})"
        if reason_prefix:
            reason = f"{reason_prefix}：{reason}"
        detail["reason"] = reason
        result = _fake_result(
            message=reason,
            detail=detail,
            score=fake_score,
        )

    _debug_print(session_token, result)
    return result


# ============================================================
# 对外接口：保持和旧 anti_spoof_service.py 一致
# ============================================================

def begin_session(session_token: str, challenge_direction: Optional[str] = None) -> Dict[str, Any]:
    """开始一次检测会话。"""
    st = _build_runtime(challenge_direction=challenge_direction)
    _runtime[session_token] = st
    return st


def update_session(session_token: str, image_np: np.ndarray, face_box: Optional[Tuple[int, int, int, int]] = None) -> Optional[Dict[str, Any]]:
    """
    更新会话。

    纯模型版不再累计 blur / area_var / banding 等手工参数。
    这里只保存最近一帧和人脸框，供 verify_session 或 assess_liveness_parallel 调模型。
    """
    if not ANTI_SPOOF_ENABLED:
        return None

    st = _get_runtime(session_token)
    st["frame_count"] += 1
    st["last_update_at"] = _now_ts()
    st["last_image"] = image_np
    st["last_face_box"] = face_box

    return {
        "frame_count": st["frame_count"],
        "model_only": True,
    }


def mark_challenge_passed(session_token: str) -> Dict[str, Any]:
    """标记随机动作挑战已经通过。"""
    st = _get_runtime(session_token)
    st["challenge_passed"] = True
    st["challenge_passed_at"] = _now_ts()
    st["challenge_pass_frame"] = st["frame_count"]
    st["challenge_duration"] = float(st["challenge_passed_at"] - st["started_at"])
    return st


def clear_session(session_token: str) -> None:
    """清理会话状态。"""
    _runtime.pop(session_token, None)


def get_session_metrics(session_token: str) -> Dict[str, Any]:
    """
    兼容旧 face_service.py 的调试接口。

    旧版 anti_spoof_service 会返回 blur_median 等手工指标；
    纯 MiniFASNet 版本已经不再计算这些手工特征，但 face_service.py 仍会调用
    get_session_metrics() 做日志打印。没有这个函数会直接 AttributeError。
    """
    st = _get_runtime(session_token)
    last_model = st.get("last_model_result") or {}
    return {
        "model_only": True,
        "frame_count": int(st.get("frame_count") or 0),
        "challenge_passed": bool(st.get("challenge_passed")),
        "challenge_passed_at": st.get("challenge_passed_at"),
        "challenge_duration": st.get("challenge_duration"),
        "last_model_check_frame": st.get("last_model_check_frame"),
        "real_score": last_model.get("real_score"),
        "fake_score": last_model.get("fake_score"),
        "label": last_model.get("label"),
        "threshold": last_model.get("threshold", MINIFASNET_REAL_THRESHOLD),
        # 只为兼容 face_service.py 的 _debug_print，不再参与任何判定。
        "blur_median": 0.0,
    }


def assess_liveness_parallel(session_token: str, image_np: Optional[np.ndarray] = None, face_box: Optional[Tuple[int, int, int, int]] = None) -> Optional[Dict[str, Any]]:
    """
    活体过程中并行模型检测。

    说明：
    - 只调用 MiniFASNet。
    - 不再使用任何手工参数。
    - 为了性能，每隔 MINIFASNET_LIVE_INTERVAL_FRAMES 帧推理一次。
    - 如果模型判 fake，立即返回 fake；如果模型判 real，返回 None，让原流程继续。
    """
    if not ANTI_SPOOF_ENABLED:
        return None

    st = _get_runtime(session_token)

    if image_np is not None:
        st["last_image"] = image_np
    if face_box is not None:
        st["last_face_box"] = face_box

    frame_count = int(st.get("frame_count") or 0)
    last_check = int(st.get("last_model_check_frame") or -1)
    if frame_count <= 0:
        return None
    if MINIFASNET_LIVE_INTERVAL_FRAMES > 0 and frame_count - last_check < MINIFASNET_LIVE_INTERVAL_FRAMES:
        return None

    image = st.get("last_image")
    box = st.get("last_face_box")
    if image is None:
        return None

    st["last_model_check_frame"] = frame_count

    try:
        model_result = _predict_minifasnet(image, box)
    except Exception as exc:
        detail = {
            "frame_count": frame_count,
            "reason": f"MiniFASNet 调用失败: {exc}",
            "import_error": _model_import_error,
        }
        result = _fake_result("MiniFASNet 调用失败，为安全起见拒绝通过", detail=detail, score=0.0)
        st["last_result"] = result
        _debug_print(session_token, result)
        return result

    st["last_model_result"] = model_result

    if model_result.get("is_real"):
        return None

    result = _model_result_to_response(session_token, model_result, frame_count, reason_prefix="活体过程中检测失败")
    st["last_result"] = result
    return result


def verify_session(session_token: str, image_np: Optional[np.ndarray], face_box: Optional[Tuple[int, int, int, int]] = None) -> Dict[str, Any]:
    """
    最终签到前模型校验。

    判定原则：
    - 若未启用反欺骗，直接通过。
    - 若要求完成随机挑战，则必须先 mark_challenge_passed。
    - 最终一定调用 MiniFASNet。
    - 只要 MiniFASNet 不满足 label==1 且 real_score>=阈值，就判 fake。
    """
    if not ANTI_SPOOF_ENABLED:
        result = _real_result(message="anti-spoof disabled", detail={"reason": "disabled"}, score=1.0)
        _debug_print(session_token, result)
        return result

    st = _get_runtime(session_token)

    if image_np is not None:
        st["last_image"] = image_np
    if face_box is not None:
        st["last_face_box"] = face_box

    frame_count = int(st.get("frame_count") or 0)

    if REQUIRE_CHALLENGE_PASSED:
        if not st.get("challenge_passed"):
            detail = {"frame_count": frame_count, "reason": "未完成随机动作挑战"}
            result = _fake_result("未完成随机动作挑战", detail=detail, score=0.0)
            st["last_result"] = result
            _debug_print(session_token, result)
            return result

        challenge_age = None
        if st.get("challenge_passed_at"):
            challenge_age = _now_ts() - float(st["challenge_passed_at"])
        if challenge_age is None or challenge_age > MAX_CHALLENGE_AGE_SECONDS:
            detail = {
                "frame_count": frame_count,
                "challenge_age": None if challenge_age is None else round(float(challenge_age), 3),
                "reason": "活体挑战已过期，请重新验证",
            }
            result = _fake_result("活体挑战已过期，请重新验证", detail=detail, score=0.0)
            st["last_result"] = result
            _debug_print(session_token, result)
            return result

    image = st.get("last_image") if image_np is None else image_np
    box = st.get("last_face_box") if face_box is None else face_box

    if image is None:
        detail = {"frame_count": frame_count, "reason": "缺少待检测图像"}
        result = _fake_result("缺少待检测图像", detail=detail, score=0.0)
        st["last_result"] = result
        _debug_print(session_token, result)
        return result

    try:
        model_result = _predict_minifasnet(image, box)
    except Exception as exc:
        detail = {
            "frame_count": frame_count,
            "reason": f"MiniFASNet 调用失败: {exc}",
            "import_error": _model_import_error,
            "silent_face_root": str(SILENT_FACE_ROOT),
            "model_dir": str(MINIFASNET_MODEL_DIR),
        }
        result = _fake_result("MiniFASNet 调用失败，为安全起见拒绝通过", detail=detail, score=0.0)
        st["last_result"] = result
        _debug_print(session_token, result)
        return result

    st["last_model_result"] = model_result
    result = _model_result_to_response(session_token, model_result, frame_count)
    st["last_result"] = result
    return result
