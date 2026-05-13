"""情绪统计蓝图：提供情绪记录查询、统计图表与 Excel 导出接口。"""

from flask import Blueprint, request, jsonify, send_file
from flask_jwt_extended import jwt_required, get_jwt_identity, get_jwt
from sqlalchemy import func

from models import db
from models.emotion import EmotionRecord
from api import teacher_required
from services import export_service

emotion_bp = Blueprint('emotion', __name__)

# 系统支持的情绪类别，与 DeepFace 返回的主情绪标签保持一致。
EMOTIONS = ['angry', 'disgust', 'fear', 'happy', 'neutral', 'sad', 'surprise']


@emotion_bp.route('/records', methods=['GET'])
@jwt_required()
def records():
    """
    查询情绪识别记录。

    权限规则：
        - 学生只允许查看自己的情绪记录。
        - 教师可按学号、时间范围、来源筛选。
    """
    claims = get_jwt()
    uid = get_jwt_identity()
    role = claims.get('role')
    student_id = request.args.get('student_id')
    date_from = request.args.get('date_from')
    date_to = request.args.get('date_to')
    source = request.args.get('source')
    page = int(request.args.get('page', 1))
    per_page = int(request.args.get('per_page', 20))

    if role == 'student':
        student_id = uid

    query = EmotionRecord.query
    if student_id:
        query = query.filter_by(student_id=student_id)
    if source:
        query = query.filter_by(source=source)
    if date_from:
        query = query.filter(EmotionRecord.recorded_at >= date_from)
    if date_to:
        query = query.filter(EmotionRecord.recorded_at <= date_to)

    paginated = query.order_by(EmotionRecord.recorded_at.desc()).paginate(
        page=page, per_page=per_page, error_out=False
    )
    return jsonify({
        'records': [r.to_dict() for r in paginated.items],
        'total': paginated.total,
        'pages': paginated.pages,
    })


@emotion_bp.route('/stats', methods=['GET'])
@jwt_required()
def stats():
    """统计当前筛选范围内的情绪分布。"""
    claims = get_jwt()
    uid = get_jwt_identity()
    role = claims.get('role')
    student_id = request.args.get('student_id')
    source = request.args.get('source')

    if role == 'student':
        student_id = uid

    query = EmotionRecord.query
    if student_id:
        query = query.filter_by(student_id=student_id)
    if source:
        query = query.filter_by(source=source)

    rows = (
        query
        .with_entities(EmotionRecord.emotion, func.count(EmotionRecord.id).label('cnt'))
        .group_by(EmotionRecord.emotion)
        .all()
    )
    distribution = {emotion: 0 for emotion in EMOTIONS}
    for row in rows:
        distribution[row.emotion] = row.cnt

    return jsonify({
        'distribution': distribution,
        'chart_data': {
            'labels': list(distribution.keys()),
            'values': list(distribution.values()),
        },
    })


@emotion_bp.route('/class-stats', methods=['GET'])
@teacher_required
def class_stats():
    """
    教师端查看全班情绪统计。

    返回：
        1. 整体情绪分布。
        2. 按学生聚合后的情绪次数明细。
        3. 可直接给柱状图/饼图使用的 `chart_data`。
    """
    rows = (
        EmotionRecord.query
        .with_entities(EmotionRecord.emotion, func.count(EmotionRecord.id).label('cnt'))
        .group_by(EmotionRecord.emotion)
        .all()
    )
    by_emotion = {emotion: 0 for emotion in EMOTIONS}
    for row in rows:
        by_emotion[row.emotion] = row.cnt

    student_rows = (
        EmotionRecord.query
        .with_entities(
            EmotionRecord.student_id,
            EmotionRecord.student_name,
            EmotionRecord.emotion,
            func.count(EmotionRecord.id).label('cnt'),
        )
        .group_by(EmotionRecord.student_id, EmotionRecord.student_name, EmotionRecord.emotion)
        .all()
    )
    by_student = {}
    for row in student_rows:
        key = row.student_id
        if key not in by_student:
            by_student[key] = {
                'student_id': row.student_id,
                'student_name': row.student_name,
                'emotions': {emotion: 0 for emotion in EMOTIONS},
            }
        by_student[key]['emotions'][row.emotion] = row.cnt

    return jsonify({
        'by_emotion': by_emotion,
        'by_student': list(by_student.values()),
        'chart_data': {
            'labels': list(by_emotion.keys()),
            'values': list(by_emotion.values()),
        },
    })


@emotion_bp.route('/export', methods=['GET'])
@jwt_required(locations=['headers', 'query_string'])
def export():
    """导出情绪记录 Excel。学生只能导出自己的记录。"""
    claims = get_jwt()
    role = claims.get('role')
    uid = get_jwt_identity()
    student_id = request.args.get('student_id')
    if role == 'student':
        student_id = uid

    source = request.args.get('source') or None
    date_from = request.args.get('date_from') or None
    date_to = request.args.get('date_to') or None

    buf = export_service.export_emotion_records_excel(
        student_id=student_id,
        date_from=date_from,
        date_to=date_to,
        source=source,
    )
    return send_file(
        buf,
        mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
        as_attachment=True,
        download_name='情绪记录.xlsx',
    )


@emotion_bp.route('/records/<int:record_id>', methods=['DELETE'])
@teacher_required
def delete_emotion_record(record_id):
    """删除单条情绪记录，仅教师可操作。"""
    rec = EmotionRecord.query.get(record_id)
    if not rec:
        return jsonify({'error': '记录不存在'}), 404
    db.session.delete(rec)
    db.session.commit()
    return jsonify({'message': '删除成功'})


@emotion_bp.route('/records/batch-delete', methods=['POST'])
@teacher_required
def batch_delete_emotion():
    """批量删除情绪记录。请求体：{"ids":[...]}"""
    data = request.get_json() or {}
    ids = data.get('ids') or []
    if not isinstance(ids, list) or not ids:
        return jsonify({'error': '请提供要删除的记录 ID 列表'}), 400
    deleted = EmotionRecord.query.filter(EmotionRecord.id.in_(ids)).delete(synchronize_session=False)
    db.session.commit()
    return jsonify({'deleted': int(deleted or 0)})
