"""
认证蓝图。

提供三类能力：
1. 账号密码登录。
2. 通过“先活体、后人脸识别”的刷脸登录。
3. 学生自助注册，并在注册阶段校验上传照片中必须只有一张清晰人脸。
"""

import os
from datetime import datetime

import bcrypt
import cv2
import numpy as np
from flask import Blueprint, current_app, jsonify, request
from flask_jwt_extended import create_access_token, get_jwt_identity, jwt_required

from models import db
from models.liveness import LivenessSession
from models.user import User
from services import anti_spoof_service
from services.attendance_service import get_rec
from services.face_service import decode_image

auth_bp = Blueprint('auth', __name__)


@auth_bp.route('/login', methods=['POST'])
def login():
    """
    账号密码登录接口。

    请求体：
        {
            "id": "学号或账号",
            "password": "明文密码"
        }
    """
    data = request.get_json() or {}
    uid = data.get('id', '').strip()
    pwd = data.get('password', '')

    if not uid or not pwd:
        return jsonify({'error': '请输入学号和密码'}), 400

    user = User.query.get(uid)
    if not user:
        return jsonify({'error': '用户不存在'}), 401

    if not bcrypt.checkpw(pwd.encode(), user.password.encode()):
        return jsonify({'error': '密码错误'}), 401

    token = create_access_token(
        identity=user.id,
        additional_claims={'role': user.role, 'name': user.name},
    )
    return jsonify({'token': token, 'role': user.role, 'name': user.name, 'id': user.id})


@auth_bp.route('/face-login', methods=['POST'])
def face_login():
    """
    活体通过后的刷脸登录接口。

    请求体：
        {
            "image": "base64编码图像",
            "session_token": "活体检测会话令牌"
        }

    流程：
        1. 检查会话是否存在、是否已通过活体验证、是否过期。
        2. 调用识别器进行单人识别。
        3. 再调用反重放模块做最终签前校验。
        4. 识别成功后签发 JWT。
    """
    data = request.get_json() or {}
    b64 = data.get('image', '')
    session_token = data.get('session_token', '')

    if not b64:
        return jsonify({'error': '未提供图像'}), 400

    session = LivenessSession.query.get(session_token)
    if not session or not session.passed or session.expires_at < datetime.utcnow():
        return jsonify({'error': '活体检测未通过，请先完成张嘴和随机动作验证'}), 403

    image_np = decode_image(b64)
    recognizer = get_rec()
    if not recognizer.db_names:
        return jsonify({'error': '人脸库为空，请先在用户管理中录入学生人脸'}), 400

    result = recognizer.recognize_face(image_np)
    if result['status'] != 'ok':
        return jsonify({'error': f'识别器异常: {result["status"]}'}), 500
    if not result['names']:
        return jsonify({'error': '未检测到人脸'}), 401

    face_box = (result.get('faces') or [None])[0]
    anti_spoof = anti_spoof_service.verify_session(session_token, image_np, face_box)
    if not anti_spoof.get('ok'):
        return jsonify({
            'status': 'fake',
            'error': anti_spoof.get('message') or 'fake: 检测到疑似屏幕/视频重放攻击',
        }), 403
    name = result['names'][0]
    if name == 'unknow':
        return jsonify({'error': '未识别到注册用户'}), 401

    user = User.query.get(name) or User.query.filter_by(name=name).first()
    if not user:
        return jsonify({'error': '数据库中未找到该用户'}), 401

    token = create_access_token(
        identity=user.id,
        additional_claims={'role': user.role, 'name': user.name},
    )
    anti_spoof_service.clear_session(session_token)
    return jsonify({
        'token': token,
        'role': user.role,
        'name': user.name,
        'id': user.id,
    })


@auth_bp.route('/me', methods=['GET'])
@jwt_required()
def me():
    """获取当前登录用户的基础资料。"""
    uid = get_jwt_identity()
    user = User.query.get(uid)
    if not user:
        return jsonify({'error': '用户不存在'}), 404
    return jsonify(user.to_dict())


@auth_bp.route('/change-password', methods=['POST'])
@jwt_required()
def change_password():
    """
    当前登录用户修改自己的密码。

    请求体：
        {
            "old_password": "旧密码",
            "new_password": "新密码"
        }
    """
    data = request.get_json() or {}
    old_pwd = data.get('old_password') or ''
    new_pwd = data.get('new_password') or ''

    if not old_pwd or not new_pwd:
        return jsonify({'error': '请填写旧密码和新密码'}), 400
    if len(new_pwd) < 6:
        return jsonify({'error': '新密码至少 6 位'}), 400
    if old_pwd == new_pwd:
        return jsonify({'error': '新密码不能与旧密码相同'}), 400

    uid = get_jwt_identity()
    user = User.query.get(uid)
    if not user:
        return jsonify({'error': '用户不存在'}), 404

    if not bcrypt.checkpw(old_pwd.encode(), user.password.encode()):
        return jsonify({'error': '旧密码不正确'}), 401

    user.password = bcrypt.hashpw(new_pwd.encode(), bcrypt.gensalt()).decode()
    db.session.commit()
    return jsonify({'message': '密码修改成功，请使用新密码登录'})


@auth_bp.route('/register', methods=['POST'])
def register():
    """
    学生自助注册接口。

    支持两种请求方式：
    1. `multipart/form-data` 上传照片文件。
    2. `application/json` 直接提交 base64 图像。

    核心校验：
    1. 学号、姓名、密码必填。
    2. 密码长度至少 6 位。
    3. 上传图像中必须且只能有一张人脸。
    """
    from services.face_detection_service import detect_single_face_for_register

    if request.content_type and 'multipart/form-data' in request.content_type:
        uid = (request.form.get('id') or '').strip()
        name = (request.form.get('name') or '').strip()
        password = request.form.get('password') or ''
        class_name = (request.form.get('class_name') or '').strip() or None
        face_file = request.files.get('face')
        b64 = None
    else:
        data = request.get_json(silent=True) or {}
        uid = str(data.get('id') or '').strip()
        name = str(data.get('name') or '').strip()
        password = data.get('password') or ''
        class_name = (data.get('class_name') or '').strip() or None
        face_file = None
        b64 = data.get('image') or ''

    if not uid or not name or not password:
        return jsonify({'error': '学号、姓名、密码均为必填项'}), 400
    if len(password) < 6:
        return jsonify({'error': '密码至少 6 位'}), 400
    if User.query.get(uid):
        return jsonify({'error': '该学号已被注册'}), 409

    image_np = None
    if face_file and face_file.filename:
        raw = face_file.read()
        if not raw:
            return jsonify({'error': '人脸图片为空'}), 400
        image_np = cv2.imdecode(np.frombuffer(raw, np.uint8), cv2.IMREAD_COLOR)
    elif b64:
        image_np = decode_image(b64)

    if image_np is None:
        return jsonify({'error': '请提供人脸照片'}), 400

    rgb = cv2.cvtColor(image_np, cv2.COLOR_BGR2RGB)
    boxes = detect_single_face_for_register(rgb)
    if not boxes:
        return jsonify({'error': '图像中未检测到人脸，请重新拍摄或上传清晰正脸照'}), 400
    if len(boxes) > 1:
        return jsonify({'error': '检测到多张人脸，请只保留本人正脸'}), 400

    images_db = current_app.config['IMAGES_DB_PATH']
    os.makedirs(images_db, exist_ok=True)
    save_path = os.path.join(images_db, f'{uid}.jpg')
    ok, encoded = cv2.imencode('.jpg', image_np)
    if not ok:
        return jsonify({'error': '图片编码失败'}), 500
    encoded.tofile(save_path)

    hashed = bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()
    user = User(
        id=uid,
        name=name,
        password=hashed,
        role='student',
        class_name=class_name,
        face_image=f'{uid}.jpg',
    )
    db.session.add(user)
    db.session.commit()

    # 学生底库变更后，清空识别缓存以保证新账号可被立即识别。
    import services.attendance_service as _svc

    _svc._rec_instance = None
    try:
        import services.group_photo_service as _gsvc

        _gsvc._rec_instance = None
    except Exception:
        pass

    return jsonify({'message': '注册成功，请使用学号和密码登录', 'user': user.to_dict()}), 201
