"""数据库初始化模块，向全项目导出统一的 SQLAlchemy 实例 `db`。"""

from flask_sqlalchemy import SQLAlchemy

db = SQLAlchemy()
