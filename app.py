"""
项目主入口文件。

本文件负责：
1. 创建 Flask 应用并加载全局配置。
2. 初始化数据库、JWT、CORS 等基础组件。
3. 注册认证、考勤、用户管理、合照识别、情绪统计等蓝图接口。
4. 提供页面路由，将浏览器请求映射到前端模板。
5. 在后台线程中预热识别模型，降低首次调用时延。
"""

import os
import threading

from flask import Flask, render_template, send_from_directory, abort
from flask_jwt_extended import JWTManager
from flask_cors import CORS

from models import db
from config import Config
from api.auth import auth_bp
from api.attendance import attendance_bp
from api.users import users_bp
from api.group_photo import group_photo_bp
from api.emotion import emotion_bp


def create_app():
    """
    创建并初始化 Flask 应用实例。

    返回：
        Flask: 已完成配置加载、扩展初始化、蓝图注册的应用对象。
    """
    app = Flask(__name__)
    app.config.from_object(Config)

    # 确保关键目录存在，避免后续保存图片或上传文件时失败。
    os.makedirs(app.config['IMAGES_DB_PATH'], exist_ok=True)
    os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)

    db.init_app(app)
    JWTManager(app)
    CORS(app)

    # 注册后端业务蓝图。
    app.register_blueprint(auth_bp, url_prefix='/api/auth')
    app.register_blueprint(attendance_bp, url_prefix='/api/attendance')
    app.register_blueprint(users_bp, url_prefix='/api/users')
    app.register_blueprint(group_photo_bp, url_prefix='/api/group-photo')
    app.register_blueprint(emotion_bp, url_prefix='/api/emotion')

    def _warmup():
        """
        在后台异步预热模型。

        设计目的：
        1. 触发 DeepFace 情绪模型权重加载。
        2. 触发 face_recognition 的底层模型和人脸库缓存加载。
        3. 避免第一次签到或第一次情绪识别时等待过久。
        """
        try:
            from services import emotion_service

            emotion_service._ensure_deepface()
            print('[startup] DeepFace warmup done')
        except Exception as exc:
            print('[startup] DeepFace warmup failed:', exc)

        try:
            with app.app_context():
                from services.attendance_service import get_rec
                import cv2
                import numpy as np

                rec = get_rec()
                images_db = app.config['IMAGES_DB_PATH']
                files = [f for f in os.listdir(images_db) if f.lower().endswith(('.jpg', '.jpeg'))]
                if files:
                    img = cv2.imdecode(
                        np.fromfile(os.path.join(images_db, files[0]), dtype=np.uint8),
                        cv2.IMREAD_COLOR,
                    )
                    if img is not None:
                        rec.recognize_face(img)
                print('[startup] face_recognition warmup done, db size =', len(rec.db_names))
        except Exception as exc:
            print('[startup] face_recognition warmup failed:', exc)

    threading.Thread(target=_warmup, daemon=True).start()

    @app.route('/')
    def index():
        """登录页路由。"""
        return render_template('login.html')

    @app.route('/register')
    def register_page():
        """学生自助注册页路由。"""
        return render_template('register.html')

    @app.route('/attendance')
    def attendance_page():
        """基础考勤页路由。"""
        return render_template('attendance.html')

    @app.route('/group-photo')
    def group_photo_page():
        """合照识别页路由。"""
        return render_template('group_photo.html')

    @app.route('/emotion-stats')
    def emotion_stats_page():
        """情绪统计页路由。"""
        return render_template('emotion_stats.html')

    @app.route('/admin/dashboard')
    def admin_dashboard():
        """教师后台首页路由。"""
        return render_template('admin/dashboard.html')

    @app.route('/admin/users')
    def admin_users():
        """用户管理页路由。"""
        return render_template('admin/users.html')

    @app.route('/admin/attendance-records')
    def admin_attendance_records():
        """考勤记录查询页路由。"""
        return render_template('admin/attendance_records.html')

    @app.route('/student/my-records')
    def student_my_records():
        """学生个人考勤记录页路由。"""
        return render_template('student/my_records.html')

    @app.route('/student/profile')
    def student_profile():
        """学生个人中心：只读展示账号基本信息与人脸照片。"""
        return render_template('student/profile.html')

    @app.route('/face-images/<path:filename>')
    def face_images(filename):
        """
        安全输出人脸图库中的静态图片。

        参数：
            filename: 数据库中保存的人脸图片文件名。
        """
        if '..' in filename or filename.startswith('/') or filename.startswith('\\'):
            abort(404)
        return send_from_directory(app.config['IMAGES_DB_PATH'], filename)

    return app


if __name__ == '__main__':
    app = create_app()
    app.run(debug=False, host='0.0.0.0', port=5000)
