"""
用户管理蓝图。

提供：
1. 用户列表分页查询。
2. 单个用户的增删改查。
3. 批量删除、批量字段更新。
4. 单张人脸照片上传。
5. ZIP / 多图 / CSV / Excel 联合批量导入学生。
"""

import os
import zipfile
import tempfile
import csv
import re

import bcrypt
import cv2
import numpy as np
from flask import Blueprint, request, jsonify, current_app

from models import db
from models.user import User
from api import teacher_required

users_bp = Blueprint('users', __name__)


@users_bp.route('', methods=['GET'])
@teacher_required
def list_users():
    """
    分页查询用户列表。

    查询参数：
        search: 按学号、姓名、班级模糊匹配。
        page: 页码。
        per_page: 每页条数。
    """
    search = request.args.get('search', '')
    role = (request.args.get('role') or '').strip()
    page = int(request.args.get('page', 1))
    per_page = int(request.args.get('per_page', 20))

    query = User.query
    if role in ('student', 'teacher'):
        query = query.filter(User.role == role)
    if search:
        like = f'%{search}%'
        query = query.filter(
            db.or_(
                User.id.like(like),
                User.name.like(like),
                User.class_name.like(like),
            )
        )
    paginated = query.paginate(page=page, per_page=per_page, error_out=False)
    return jsonify({
        'users': [user.to_dict() for user in paginated.items],
        'total': paginated.total,
        'pages': paginated.pages,
        'page': paginated.page,
        'per_page': paginated.per_page,
    })


@users_bp.route('', methods=['POST'])
@teacher_required
def create_user():
    """
    创建单个用户。

    请求体中至少需要：
        id, name, password
    """
    data = request.get_json() or {}
    uid = data.get('id', '').strip()
    if not uid or not data.get('name') or not data.get('password'):
        return jsonify({'error': '学号、姓名、密码为必填项'}), 400

    if User.query.get(uid):
        return jsonify({'error': '该学号已存在'}), 409

    hashed = bcrypt.hashpw(data['password'].encode(), bcrypt.gensalt()).decode()
    user = User(
        id=uid,
        name=data['name'],
        password=hashed,
        role=data.get('role', 'student'),
        gender=data.get('gender'),
        age=data.get('age'),
        address=data.get('address'),
        ethnicity=data.get('ethnicity'),
        class_name=data.get('class_name'),
    )
    db.session.add(user)
    db.session.commit()
    return jsonify({'message': '用户创建成功', 'user': user.to_dict()}), 201


@users_bp.route('/<uid>', methods=['GET'])
@teacher_required
def get_user(uid):
    """获取单个用户详情。"""
    user = User.query.get_or_404(uid)
    return jsonify(user.to_dict())


@users_bp.route('/<uid>', methods=['PUT'])
@teacher_required
def update_user(uid):
    """
    更新单个用户信息。

    支持字段：
        name, gender, age, address, ethnicity, role, class_name, password
    """
    user = User.query.get_or_404(uid)
    data = request.get_json() or {}

    for field in ('name', 'gender', 'age', 'address', 'ethnicity', 'role', 'class_name'):
        if field in data:
            setattr(user, field, data[field])
    if 'password' in data and data['password']:
        user.password = bcrypt.hashpw(data['password'].encode(), bcrypt.gensalt()).decode()

    db.session.commit()
    return jsonify({'message': '更新成功', 'user': user.to_dict()})


@users_bp.route('/<uid>', methods=['DELETE'])
@teacher_required
def delete_user(uid):
    """删除单个用户及其关联记录。"""
    user = User.query.get_or_404(uid)
    _hard_delete_user(user)
    db.session.commit()
    _invalidate_recognizers()
    return jsonify({'message': '删除成功'})


@users_bp.route('/batch-delete', methods=['POST'])
@teacher_required
def batch_delete():
    """
    批量删除用户。

    请求体：
        {"ids": ["2022001", "2022002", ...]}
    """
    data = request.get_json() or {}
    ids = data.get('ids') or []
    if not isinstance(ids, list) or not ids:
        return jsonify({'error': '请提供要删除的学号列表'}), 400

    deleted, failed, errors = 0, 0, []
    users = User.query.filter(User.id.in_(ids)).all()
    found_ids = {user.id for user in users}
    for missing in (set(ids) - found_ids):
        errors.append(f'{missing}: 用户不存在')
        failed += 1

    for user in users:
        try:
            _hard_delete_user(user)
            deleted += 1
        except Exception as exc:
            errors.append(f'{user.id}: {exc}')
            failed += 1
            db.session.rollback()

    db.session.commit()
    _invalidate_recognizers()
    return jsonify({
        'deleted': deleted,
        'failed': failed,
        'errors': errors,
    })


@users_bp.route('/batch-update', methods=['POST'])
@teacher_required
def batch_update():
    """
    批量更新用户属性。

    请求体结构：
        {
            "ids": ["2022001", "2022002"],
            "updates": {
                "class_name": "...",
                "role": "...",
                "gender": "...",
                "address": "...",
                "ethnicity": "...",
                "age": 20,
                "password": "123456"
            }
        }

    规则：
        只有非空字段才会真正写入。
    """
    data = request.get_json() or {}
    ids = data.get('ids') or []
    updates = data.get('updates') or {}
    if not isinstance(ids, list) or not ids:
        return jsonify({'error': '请提供要更新的学号列表'}), 400
    if not isinstance(updates, dict) or not updates:
        return jsonify({'error': '请提供要更新的字段'}), 400

    allowed = {'class_name', 'role', 'gender', 'address', 'ethnicity', 'age', 'password'}
    clean_updates = {key: value for key, value in updates.items() if key in allowed and value not in ('', None)}
    if not clean_updates:
        return jsonify({'error': '没有有效字段可更新'}), 400

    users = User.query.filter(User.id.in_(ids)).all()
    updated = 0
    for user in users:
        if 'password' in clean_updates:
            user.password = bcrypt.hashpw(str(clean_updates['password']).encode(), bcrypt.gensalt()).decode()
        for key, value in clean_updates.items():
            if key == 'password':
                continue
            if key == 'age':
                try:
                    value = int(value)
                except Exception:
                    continue
            setattr(user, key, value)
        updated += 1

    db.session.commit()
    return jsonify({'updated': updated})


def _hard_delete_user(user):
    """
    删除用户及其关联考勤、情绪、活动参与记录和底库人脸图片。

    参数：
        user: `User` ORM 对象。
    """
    from models.attendance import AttendanceRecord
    from models.emotion import EmotionRecord
    from models.activity import ActivityParticipant

    AttendanceRecord.query.filter_by(student_id=user.id).delete(synchronize_session=False)
    EmotionRecord.query.filter_by(student_id=user.id).delete(synchronize_session=False)
    ActivityParticipant.query.filter_by(student_id=user.id).delete(synchronize_session=False)

    images_db = current_app.config['IMAGES_DB_PATH']
    if user.face_image:
        filepath = os.path.join(images_db, user.face_image)
        if os.path.exists(filepath):
            try:
                os.remove(filepath)
            except Exception:
                pass

    db.session.delete(user)


def _invalidate_recognizers(warm_attendance=False):
    """
    失效并可选预热识别缓存。

    当底库图片变化时，需要清空内存中的识别器实例，
    否则新注册/新导入的人脸不会立即参与识别。

    为了不阻塞 HTTP 响应，`warm_attendance=True` 时把重建放到后台线程执行，
    前端立即返回，考勤识别会在几秒后自动使用最新底库。
    """
    try:
        import services.attendance_service as attendance_service

        attendance_service._rec_instance = None
        if warm_attendance:
            import threading
            from flask import current_app

            app = current_app._get_current_object()

            def _bg_reload():
                with app.app_context():
                    try:
                        attendance_service.reload_recognizer()
                    except Exception as exc:
                        print(f'[users] background reload_recognizer failed: {exc}', flush=True)

            threading.Thread(target=_bg_reload, daemon=True).start()
    except Exception:
        pass

    try:
        import services.group_photo_service as group_photo_service

        group_photo_service._rec_instance = None
    except Exception:
        pass


@users_bp.route('/<uid>/face', methods=['POST'])
@teacher_required
def upload_face(uid):
    """
    为指定用户上传或替换单张标准人脸照片。

    支持：
        1. `multipart/form-data` 文件上传。
        2. JSON base64 图像上传。
    """
    import time
    t_start = time.time()

    user = User.query.get_or_404(uid)

    from services.face_detection_service import detect_single_face_for_register
    from services.face_service import decode_image

    image_np = None
    if 'face' in request.files:
        face_file = request.files['face']
        if not face_file or not face_file.filename:
            return jsonify({'error': '未选择图片文件'}), 400
        raw = face_file.read()
        if not raw:
            return jsonify({'error': '图片文件为空'}), 400
        image_np = cv2.imdecode(np.frombuffer(raw, np.uint8), cv2.IMREAD_COLOR)
    else:
        data = request.get_json(silent=True) or {}
        b64 = data.get('image', '')
        if not b64:
            return jsonify({'error': '未提供图片'}), 400
        image_np = decode_image(b64)

    if image_np is None:
        return jsonify({'error': '图片解码失败'}), 400

    t_decode = time.time()

    rgb = cv2.cvtColor(image_np, cv2.COLOR_BGR2RGB)
    boxes = detect_single_face_for_register(rgb)
    t_detect = time.time()

    if not boxes:
        return jsonify({'error': '图像中未检测到人脸'}), 400
    if len(boxes) > 1:
        return jsonify({'error': '检测到多张人脸，请只上传单人照片'}), 400

    images_db = current_app.config['IMAGES_DB_PATH']
    save_path = os.path.join(images_db, f'{uid}.jpg')
    cv2.imwrite(save_path, image_np)

    user.face_image = f'{uid}.jpg'
    db.session.commit()
    t_save = time.time()

    _invalidate_recognizers(warm_attendance=True)
    t_warm = time.time()

    print(
        f'[upload_face] uid={uid} img={image_np.shape[1]}x{image_np.shape[0]} '
        f'decode={t_decode-t_start:.2f}s detect={t_detect-t_decode:.2f}s '
        f'save={t_save-t_detect:.2f}s warm={t_warm-t_save:.2f}s '
        f'total={t_warm-t_start:.2f}s',
        flush=True,
    )
    return jsonify({'message': '人脸图像上传成功'})


@users_bp.route('/batch-import', methods=['POST'])
@teacher_required
def batch_import():
    """
    批量导入学生接口。

    支持两种输入方式：
    1. 上传 ZIP，其中可同时包含照片、`students.csv`、`students.xlsx`。
    2. 直接上传多张照片。

    文件名约定：
        - `学号-姓名.jpg`
        - `学号_姓名.jpg`
        - `学号 姓名.jpg`
        - `学号.jpg`（姓名回退为学号）

    批量导入算法：
        1. 统一收集 ZIP 解压或多图上传得到的所有文件。
        2. 读取 CSV/Excel 元数据作为高优先级档案信息。
        3. 从照片文件名中解析学号/姓名作为补充信息。
        4. 每张照片都必须经过单人脸检测校验。
        5. 写入数据库并刷新识别缓存。
    """
    has_zip = 'zip_file' in request.files and request.files['zip_file'].filename
    photos = request.files.getlist('photos')
    photos = [photo for photo in photos if photo and photo.filename]

    if not has_zip and not photos:
        return jsonify({'error': '请上传 ZIP 或选择若干图片文件'}), 400

    default_class = (request.form.get('default_class') or '').strip() or None
    default_password = request.form.get('default_password') or '123456'
    images_db = current_app.config['IMAGES_DB_PATH']
    imported, updated, failed = 0, 0, 0
    errors = []

    try:
        import openpyxl
    except Exception:
        openpyxl = None

    with tempfile.TemporaryDirectory() as tmpdir:
        # 第一步：如果是 ZIP，则先解压到临时目录。
        if has_zip:
            zip_file = request.files['zip_file']
            zip_path = os.path.join(tmpdir, 'upload.zip')
            zip_file.save(zip_path)
            try:
                with zipfile.ZipFile(zip_path, 'r') as zf:
                    zf.extractall(tmpdir)
            except zipfile.BadZipFile:
                return jsonify({'error': 'ZIP 文件损坏或格式错误'}), 400

        # 第二步：将直接上传的多张照片也落到统一的临时目录，后续走同一流程。
        for photo in photos:
            safe_name = os.path.basename(photo.filename)
            if not safe_name:
                continue
            target = os.path.join(tmpdir, safe_name)
            base, ext = os.path.splitext(safe_name)
            index = 1
            while os.path.exists(target):
                target = os.path.join(tmpdir, f'{base}__{index}{ext}')
                index += 1
            photo.save(target)

        collected_files = []
        for root, _, files in os.walk(tmpdir):
            for filename in files:
                collected_files.append(os.path.join(root, filename))

        meta = {}

        def _normalize_row(row):
            """
            统一规范 CSV/Excel 的一行学生元数据。

            支持中英文字段名混用，例如：
                id / 学号、name / 姓名、class_name / 班级 等。
            """
            if not row:
                return None
            uid = str(row.get('id') or row.get('学号') or '').strip()
            if not uid:
                return None
            return {
                'id': uid,
                'name': str(row.get('name') or row.get('姓名') or uid).strip(),
                'class_name': (row.get('class_name') or row.get('班级') or default_class or None),
                'password': row.get('password') or row.get('密码') or default_password,
                'gender': row.get('gender') or row.get('性别'),
                'age': row.get('age') or row.get('年龄'),
                'address': row.get('address') or row.get('籍贯'),
                'ethnicity': row.get('ethnicity') or row.get('民族'),
                'role': (row.get('role') or row.get('角色') or 'student'),
            }

        # 第三步：读取 CSV/Excel 元数据。
        for filepath in collected_files:
            lower = filepath.lower()
            if lower.endswith('.csv'):
                try:
                    with open(filepath, 'r', encoding='utf-8-sig') as file:
                        for row in csv.DictReader(file):
                            info = _normalize_row(row)
                            if info:
                                meta[info['id']] = info
                except Exception as exc:
                    errors.append(f'读取 CSV 失败: {exc}')
            elif lower.endswith('.xlsx') and openpyxl is not None:
                try:
                    wb = openpyxl.load_workbook(filepath, read_only=True, data_only=True)
                    ws = wb.active
                    rows = list(ws.iter_rows(values_only=True))
                    if not rows:
                        continue
                    headers = [str(cell).strip() if cell is not None else '' for cell in rows[0]]
                    for row_values in rows[1:]:
                        row = {
                            headers[index]: row_values[index]
                            for index in range(min(len(headers), len(row_values)))
                        }
                        info = _normalize_row(row)
                        if info:
                            meta[info['id']] = info
                except Exception as exc:
                    errors.append(f'读取 Excel 失败: {exc}')

        # 第四步：从图片文件名解析学号、姓名、班级、性别。
        # 支持的分隔符：`-`、`_`、空格。文件名约定（最多 4 段）：
        #     学号.jpg
        #     学号-姓名.jpg
        #     学号-姓名-班级.jpg
        #     学号-姓名-班级-性别.jpg
        photo_files = {}
        photo_meta = {}
        sep_re = re.compile(r'[-_\s]+')
        _gender_synonyms = {
            '男': '男', 'M': '男', 'm': '男', 'male': '男', 'Male': '男', 'MALE': '男',
            '女': '女', 'F': '女', 'f': '女', 'female': '女', 'Female': '女', 'FEMALE': '女',
        }

        for filepath in collected_files:
            filename = os.path.basename(filepath)
            lower = filename.lower()
            if not lower.endswith(('.jpg', '.jpeg', '.png')):
                continue
            stem = os.path.splitext(filename)[0].strip()
            parts = [p for p in sep_re.split(stem, maxsplit=3) if p.strip()]
            if not parts:
                continue

            uid = parts[0].strip()
            extra = {}
            if len(parts) >= 2:
                extra['name'] = parts[1].strip()
            if len(parts) >= 3:
                extra['class_name'] = parts[2].strip()
            if len(parts) >= 4:
                raw_gender = parts[3].strip()
                extra['gender'] = _gender_synonyms.get(raw_gender, raw_gender)

            photo_meta[uid] = extra
            photo_files[uid] = filepath

        all_ids = set(meta.keys()) | set(photo_files.keys())

        from services.face_detection_service import detect_single_face_for_register

        for uid in sorted(all_ids):
            row_meta = meta.get(uid)
            fname_meta = photo_meta.get(uid) or {}
            # 优先级：CSV/Excel 元数据 > 文件名解析 > 默认值。
            info = row_meta or {
                'id': uid,
                'name': fname_meta.get('name') or uid,
                'class_name': fname_meta.get('class_name') or default_class,
                'password': default_password,
                'role': 'student',
                'gender': fname_meta.get('gender'),
                'age': None,
                'address': None,
                'ethnicity': None,
            }
            # CSV 里没给的字段，用文件名解析的值兜底
            if row_meta:
                if (not info.get('name') or info.get('name') == uid) and fname_meta.get('name'):
                    info['name'] = fname_meta['name']
                if not info.get('class_name') and fname_meta.get('class_name'):
                    info['class_name'] = fname_meta['class_name']
                if not info.get('gender') and fname_meta.get('gender'):
                    info['gender'] = fname_meta['gender']

            user = User.query.get(uid)
            is_new = user is None
            if is_new:
                hashed = bcrypt.hashpw(str(info['password']).encode(), bcrypt.gensalt()).decode()
                try:
                    age_val = int(info['age']) if info.get('age') not in (None, '', ' ') else None
                except Exception:
                    age_val = None

                user = User(
                    id=uid,
                    name=info['name'] or uid,
                    password=hashed,
                    role=info.get('role') or 'student',
                    gender=info.get('gender') or None,
                    age=age_val,
                    address=info.get('address') or None,
                    ethnicity=info.get('ethnicity') or None,
                    class_name=info.get('class_name') or None,
                )
                db.session.add(user)
            else:
                if info.get('name'):
                    user.name = info['name']
                if info.get('class_name'):
                    user.class_name = info['class_name']
                if info.get('gender'):
                    user.gender = info['gender']
                if info.get('address'):
                    user.address = info['address']
                if info.get('ethnicity'):
                    user.ethnicity = info['ethnicity']

            # 第五步：处理对应的人脸照片，并确保照片中只有一张有效人脸。
            filepath = photo_files.get(uid)
            if filepath:
                img = cv2.imdecode(np.fromfile(filepath, dtype=np.uint8), cv2.IMREAD_COLOR)
                if img is None:
                    errors.append(f'{uid}: 无法读取图像')
                    failed += 1
                else:
                    boxes = detect_single_face_for_register(cv2.cvtColor(img, cv2.COLOR_BGR2RGB))
                    if not boxes:
                        errors.append(f'{uid}: 图片中未检测到人脸')
                        failed += 1
                    elif len(boxes) > 1:
                        errors.append(f'{uid}: 检测到多张人脸，不能用于单人底库录入')
                        failed += 1
                    else:
                        dst = os.path.join(images_db, f'{uid}.jpg')
                        ok, encoded = cv2.imencode('.jpg', img)
                        if ok:
                            encoded.tofile(dst)
                            user.face_image = f'{uid}.jpg'

            if is_new:
                imported += 1
            else:
                updated += 1

        db.session.commit()

    _invalidate_recognizers()

    return jsonify({
        'imported': imported,
        'updated': updated,
        'failed': failed,
        'errors': errors[:50],
    })
