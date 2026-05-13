"""用户模型：保存学生/教师账号信息与学生档案字段。"""

from datetime import datetime

from models import db


class User(db.Model):
    """
    系统用户表。

    既用于登录认证，也用于保存学生的人脸底库图片路径、班级等资料。
    """

    __tablename__ = 'users'

    id = db.Column(db.String(20), primary_key=True)
    name = db.Column(db.String(50), nullable=False)
    password = db.Column(db.String(255), nullable=False)
    role = db.Column(db.Enum('student', 'teacher'), nullable=False, default='student')
    gender = db.Column(db.String(10))
    age = db.Column(db.Integer)
    address = db.Column(db.String(100))
    ethnicity = db.Column(db.String(20))
    class_name = db.Column(db.String(50))
    face_image = db.Column(db.String(200))
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    def to_dict(self):
        """将用户对象序列化为可直接返回前端的字典。"""
        return {
            'id': self.id,
            'name': self.name,
            'role': self.role,
            'gender': self.gender,
            'age': self.age,
            'address': self.address,
            'ethnicity': self.ethnicity,
            'class_name': self.class_name,
            'face_image': self.face_image,
            'created_at': self.created_at.isoformat() if self.created_at else None,
        }
