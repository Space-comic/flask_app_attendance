"""活动与参与记录模型：为合照识别名单和参与统计提供数据基础。"""

from datetime import datetime, timedelta

from flask import has_app_context, current_app

from models import db


def _to_local(dt):
    """将 UTC naive 时间转换为业务时区时间。"""
    if not dt:
        return None
    if has_app_context():
        offset = int(current_app.config.get('TIMEZONE_OFFSET_HOURS', 8))
        return dt + timedelta(hours=offset)
    return dt


class Activity(db.Model):
    """活动主表，一次合照识别对应一次活动记录。"""

    __tablename__ = 'activities'

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    name = db.Column(db.String(100), nullable=False)
    photo_path = db.Column(db.String(200))
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    created_by = db.Column(db.String(20), nullable=False)

    participants = db.relationship('ActivityParticipant', backref='activity', lazy=True)

    def to_dict(self):
        """输出活动详情及参与人数。"""
        local_created_at = _to_local(self.created_at)
        return {
            'id': self.id,
            'name': self.name,
            'photo_path': self.photo_path,
            'created_at': local_created_at.isoformat() if local_created_at else None,
            'created_by': self.created_by,
            'participant_count': len(self.participants),
        }


class ActivityParticipant(db.Model):
    """活动参与者明细表，记录某位学生参加了某次活动。"""

    __tablename__ = 'activity_participants'

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    activity_id = db.Column(db.Integer, db.ForeignKey('activities.id'), nullable=False)
    student_id = db.Column(db.String(20), db.ForeignKey('users.id'), nullable=False)
    student_name = db.Column(db.String(50), nullable=False)

    def to_dict(self):
        """序列化为名单展示使用的字典。"""
        return {
            'id': self.id,
            'activity_id': self.activity_id,
            'student_id': self.student_id,
            'student_name': self.student_name,
        }
