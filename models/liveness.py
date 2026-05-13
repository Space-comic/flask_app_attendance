"""活体检测会话模型：保存一次签到挑战流程的数据库状态。"""

from datetime import datetime

from models import db


class LivenessSession(db.Model):
    """活体检测会话表。"""

    __tablename__ = 'liveness_sessions'

    session_token = db.Column(db.String(64), primary_key=True)
    user_id = db.Column(db.String(20))
    blink_count = db.Column(db.Integer, default=0)
    below_threshold = db.Column(db.Boolean, default=False)
    passed = db.Column(db.Boolean, default=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    expires_at = db.Column(db.DateTime, nullable=False)
