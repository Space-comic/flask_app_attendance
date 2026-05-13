"""考勤蓝图：活体检测、签到识别、记录查询与导出。"""

import uuid
from datetime import datetime, timedelta, timezone

from flask import Blueprint, current_app, jsonify, request, send_file
from flask_jwt_extended import get_jwt, get_jwt_identity, jwt_required

from models import db
from models.attendance import AttendanceRecord
from models.liveness import LivenessSession
from models.user import User
from services import attendance_service, export_service
from services.face_service import (
    LIVENESS_SESSION_MINUTES,
    REQUIRED_MOUTHS,
    check_liveness_frame,
    clear_liveness_session,
    decode_image,
    init_liveness_session,
)

attendance_bp = Blueprint('attendance', __name__)


def _utc_now_naive():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _local_today():
    offset = int(current_app.config.get('TIMEZONE_OFFSET_HOURS', 8))
    return (_utc_now_naive() + timedelta(hours=offset)).date()


@attendance_bp.route('/liveness/start', methods=['POST'])
def liveness_start():
    token = str(uuid.uuid4()).replace('-', '')
    expires_at = _utc_now_naive() + timedelta(minutes=LIVENESS_SESSION_MINUTES)
    session = LivenessSession(
        session_token=token,
        blink_count=0,
        below_threshold=False,
        passed=False,
        expires_at=expires_at,
    )
    db.session.add(session)
    db.session.commit()

    runtime = init_liveness_session(token)
    return jsonify(
        {
            'session_token': token,
            'required_blinks': runtime['required_blinks'],
            'required_mouths': runtime['required_mouths'],
            'required_actions': runtime['required_actions'],
            'instruction': runtime['instruction'],
            'current_step': runtime['current_step'],
        }
    )


@attendance_bp.route('/liveness/check-frame', methods=['POST'])
def liveness_check_frame():
    data = request.get_json() or {}
    token = data.get('session_token', '')
    b64 = data.get('image', '')

    session = LivenessSession.query.get(token)
    if not session:
        return jsonify({'error': '无效的活体会话，请重新开始'}), 400
    if session.expires_at < _utc_now_naive():
        return jsonify({'error': '活体会话已过期，请重新开始'}), 400
    if session.passed:
        return jsonify(
            {
                'blink_count': session.blink_count,
                'mouth_count': session.blink_count,
                'action_count': session.blink_count,
                'required_blinks': REQUIRED_MOUTHS,
                'required_mouths': REQUIRED_MOUTHS,
                'passed': True,
                'face_found': True,
                'current_step': 'done',
                'instruction': '活体验证通过，请开始签到',
            }
        )

    image_np = decode_image(b64)
    result = check_liveness_frame(image_np, session)
    if result.get('status') == 'fake':
        clear_liveness_session(token)
        db.session.delete(session)
        db.session.commit()
        return jsonify(result), 403

    db.session.commit()
    return jsonify(result)


@attendance_bp.route('/recognize', methods=['POST'])
@jwt_required()
def recognize():
    data = request.get_json() or {}
    b64 = data.get('image', '')
    session_token = data.get('session_token', '')

    session = LivenessSession.query.get(session_token)
    if not session or not session.passed or session.expires_at < _utc_now_naive():
        return jsonify({'error': '活体检测未通过'}), 403

    image_np = decode_image(b64)
    result = attendance_service.recognize_and_checkin(image_np, session_token)
    return jsonify(result)


@attendance_bp.route('/records', methods=['GET'])
@jwt_required()
def records():
    claims = get_jwt()
    uid = get_jwt_identity()
    role = claims.get('role')
    student_id = request.args.get('student_id')
    date_from = request.args.get('date_from')
    date_to = request.args.get('date_to')
    page = int(request.args.get('page', 1))

    if role == 'student':
        student_id = uid

    result = attendance_service.get_records(
        student_id=student_id,
        date_from=date_from,
        date_to=date_to,
        page=page,
    )
    return jsonify(result)


@attendance_bp.route('/export', methods=['GET'])
@jwt_required(locations=['headers', 'query_string'])
def export():
    claims = get_jwt()
    if claims.get('role') != 'teacher':
        return jsonify({'error': '需要教师权限'}), 403

    student_id = request.args.get('student_id')
    date_from = request.args.get('date_from')
    date_to = request.args.get('date_to')
    buf = export_service.export_attendance_excel(
        student_id=student_id,
        date_from=date_from,
        date_to=date_to,
    )
    return send_file(
        buf,
        mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
        as_attachment=True,
        download_name='考勤记录.xlsx',
    )


@attendance_bp.route('/today-status', methods=['GET'])
@jwt_required()
def today_status():
    today = _local_today()
    checked = AttendanceRecord.query.filter_by(date=today, status='present').all()
    total = User.query.filter_by(role='student').count()
    return jsonify(
        {
            'checked_in': [record.to_dict() for record in checked],
            'checked_count': len(checked),
            'total_students': total,
        }
    )


@attendance_bp.route('/records/<int:record_id>', methods=['DELETE'])
@jwt_required()
def delete_record(record_id):
    claims = get_jwt()
    if claims.get('role') != 'teacher':
        return jsonify({'error': '需要教师权限'}), 403

    record = AttendanceRecord.query.get(record_id)
    if not record:
        return jsonify({'error': '记录不存在'}), 404
    db.session.delete(record)
    db.session.commit()
    return jsonify({'message': '删除成功'})


@attendance_bp.route('/records/batch-delete', methods=['POST'])
@jwt_required()
def batch_delete_records():
    claims = get_jwt()
    if claims.get('role') != 'teacher':
        return jsonify({'error': '需要教师权限'}), 403

    data = request.get_json() or {}
    ids = data.get('ids') or []
    if not isinstance(ids, list) or not ids:
        return jsonify({'error': '请提供要删除的记录 ID 列表'}), 400

    deleted = AttendanceRecord.query.filter(AttendanceRecord.id.in_(ids)).delete(synchronize_session=False)
    db.session.commit()
    return jsonify({'deleted': int(deleted or 0)})
