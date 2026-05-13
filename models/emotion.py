"""情绪记录模型：保存考勤或合照过程中识别出的情绪结果。"""

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


class EmotionRecord(db.Model):
    """单条情绪识别记录。"""

    __tablename__ = 'emotion_records'

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    student_id = db.Column(db.String(20), db.ForeignKey('users.id'), nullable=False)
    student_name = db.Column(db.String(50), nullable=False)
    emotion = db.Column(
        db.Enum('angry', 'disgust', 'fear', 'happy', 'neutral', 'sad', 'surprise'),
        nullable=False
    )
    source = db.Column(db.Enum('attendance', 'group_photo'), nullable=False)
    recorded_at = db.Column(db.DateTime, default=datetime.utcnow)

    def to_dict(self):
        """输出给前端图表和记录表使用的序列化结果。"""
        local_recorded_at = _to_local(self.recorded_at)
        return {
            'id': self.id,
            'student_id': self.student_id,
            'student_name': self.student_name,
            'emotion': self.emotion,
            'source': self.source,
            'recorded_at': local_recorded_at.isoformat() if local_recorded_at else None,
        }
