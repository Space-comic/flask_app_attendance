"""合照识别蓝图：处理上传识别、活动列表、参与名单与统计导出。"""

from flask import Blueprint, request, jsonify, send_file
from flask_jwt_extended import get_jwt_identity

from models.activity import Activity, ActivityParticipant
from services.face_service import decode_image
from services import group_photo_service, export_service
from api import teacher_required
from models import db

group_photo_bp = Blueprint('group_photo', __name__)


@group_photo_bp.route('/activities/<int:activity_id>/participants', methods=['GET'])
@teacher_required
def activity_participants(activity_id):
    """获取某次活动的参与名单。"""
    participants = ActivityParticipant.query.filter_by(activity_id=activity_id).all()
    return jsonify({'participants': [p.to_dict() for p in participants]})


@group_photo_bp.route('/activities/<int:activity_id>/export', methods=['GET'])
@teacher_required
def export_activity(activity_id):
    """导出单次活动的参与名单 Excel。"""
    buf = export_service.export_activity_participants_excel(activity_id)
    return send_file(
        buf,
        mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
        as_attachment=True,
        download_name=f'activity_{activity_id}_participants.xlsx',
    )


@group_photo_bp.route('/export/stats', methods=['GET'])
@teacher_required
def export_activity_stats():
    """导出活动参与统计汇总 Excel。"""
    buf = export_service.export_activity_stats_excel()
    return send_file(
        buf,
        mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
        as_attachment=True,
        download_name='activity_stats.xlsx',
    )


@group_photo_bp.route('/recognize', methods=['POST'])
@teacher_required
def recognize():
    """
    合照上传识别接口。

    支持两种输入：
    1. `multipart/form-data` 上传图片文件。
    2. JSON 直接传 base64 图像。
    """
    teacher_id = get_jwt_identity()
    activity_name = ''

    if request.content_type and 'multipart' in request.content_type:
        file = request.files.get('image')
        activity_name = request.form.get('activity_name', '未命名活动')
        if not file:
            return jsonify({'error': '未提供图像文件'}), 400

        import cv2
        import numpy as np

        img_bytes = file.read()
        nparr = np.frombuffer(img_bytes, np.uint8)
        image_np = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
    else:
        data = request.get_json() or {}
        b64 = data.get('image', '')
        activity_name = data.get('activity_name', '未命名活动')
        if not b64:
            return jsonify({'error': '未提供图像'}), 400
        image_np = decode_image(b64)

    result = group_photo_service.recognize_group_photo(image_np, activity_name, teacher_id)
    return jsonify(result)


@group_photo_bp.route('/activities', methods=['GET'])
@teacher_required
def list_activities():
    """分页返回历史活动列表。"""
    page = int(request.args.get('page', 1))
    per_page = int(request.args.get('per_page', 10))
    paginated = Activity.query.order_by(Activity.created_at.desc()).paginate(
        page=page, per_page=per_page, error_out=False
    )
    return jsonify({
        'activities': [a.to_dict() for a in paginated.items],
        'total': paginated.total,
        'pages': paginated.pages,
    })


@group_photo_bp.route('/activities/<int:activity_id>', methods=['GET'])
@teacher_required
def get_activity(activity_id):
    """获取活动详情及其参与名单。"""
    activity = Activity.query.get_or_404(activity_id)
    participants = ActivityParticipant.query.filter_by(activity_id=activity_id).all()
    return jsonify({
        'activity': activity.to_dict(),
        'participants': [p.to_dict() for p in participants],
    })


@group_photo_bp.route('/activities/<int:activity_id>', methods=['DELETE'])
@teacher_required
def delete_activity(activity_id):
    """删除活动记录及其参与名单。"""
    activity = Activity.query.get(activity_id)
    if not activity:
        return jsonify({'error': '活动不存在'}), 404

    ActivityParticipant.query.filter_by(activity_id=activity_id).delete(synchronize_session=False)
    db.session.delete(activity)
    db.session.commit()
    return jsonify({'deleted': activity_id})


@group_photo_bp.route('/stats', methods=['GET'])
@teacher_required
def stats():
    """返回学生活动参与次数统计，并为前端柱状图准备图表数据。"""
    data = group_photo_service.get_participation_stats()
    chart_data = {
        'labels': [r['student_name'] for r in data],
        'values': [r['count'] for r in data],
    }
    return jsonify({'stats': data, 'chart_data': chart_data})
