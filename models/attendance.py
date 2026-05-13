"""考勤记录模型：保存学生每次签到的时间、日期、状态与方式。"""

from datetime import datetime, timedelta

from flask import has_app_context, current_app

from models import db


class AttendanceRecord(db.Model):
    """单条签到记录。"""

    __tablename__ = 'attendance_records'

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    student_id = db.Column(db.String(20), db.ForeignKey('users.id'), nullable=False)
    student_name = db.Column(db.String(50), nullable=False)
    # 统一以 UTC naive 时间保存真实签到时刻。
    check_time = db.Column(db.DateTime, default=datetime.utcnow)
    # 业务日期按本地时区计算，用于“今天是否已签到”的逻辑判断。
    date = db.Column(db.Date, nullable=False)
    status = db.Column(db.Enum('present', 'absent'), default='present')
    method = db.Column(db.Enum('face', 'manual'), default='face')

    @staticmethod
    def _to_local(dt):
        """将 UTC naive 时间转换为当前业务时区时间。"""
        if not dt:
            return None
        if has_app_context():
            offset = int(current_app.config.get('TIMEZONE_OFFSET_HOURS', 8))
            return dt + timedelta(hours=offset)
        return dt

    def to_dict(self):
        """序列化为前端表格可直接使用的字典。"""
        local_check_time = self._to_local(self.check_time)
        return {
            'id': self.id,
            'student_id': self.student_id,
            'student_name': self.student_name,
            'check_time': local_check_time.isoformat(timespec='seconds') if local_check_time else None,
            'date': self.date.isoformat() if self.date else None,
            'status': self.status,
            'method': self.method,
        }
