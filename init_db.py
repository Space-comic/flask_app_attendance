"""
数据库初始化脚本。

用途：
1. 创建项目所需的全部数据表。
2. 若系统中不存在管理员账号，则自动生成默认教师账号。

运行方式：
    python init_db.py
"""

import bcrypt

from app import create_app
from models import db
from models.user import User
from models.attendance import AttendanceRecord
from models.emotion import EmotionRecord
from models.activity import Activity, ActivityParticipant
from models.liveness import LivenessSession


def init():
    """执行数据库建表和默认教师账号初始化。"""
    app = create_app()
    with app.app_context():
        # 显式引用模型的目的是确保 SQLAlchemy 在 create_all 前已加载所有表定义。
        _ = (AttendanceRecord, EmotionRecord, Activity, ActivityParticipant, LivenessSession)

        db.create_all()
        print('[OK] 所有数据表已创建')

        if not User.query.get('admin'):
            hashed = bcrypt.hashpw(b'admin123', bcrypt.gensalt()).decode()
            teacher = User(
                id='admin',
                name='管理员',
                password=hashed,
                role='teacher',
            )
            db.session.add(teacher)
            db.session.commit()
            print('[OK] 初始教师账号已创建: admin / admin123')
        else:
            print('[SKIP] 教师账号已存在')


if __name__ == '__main__':
    init()
