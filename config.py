"""项目全局系统配置。"""

import os

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# 兼容旧的人脸库加载模块：`my_face_recognition/f_storage.py` 仍会直接引用。
path_images = os.path.join(BASE_DIR, 'images_db')


class Config:
    """只保留应用级、路径级和系统级配置。"""

    SECRET_KEY = os.environ.get('SECRET_KEY', 'change-me-in-production')
    JWT_SECRET_KEY = os.environ.get(
        'JWT_SECRET_KEY',
        'change-me-please-use-at-least-32-bytes-random-secret',
    )
    JWT_ACCESS_TOKEN_EXPIRES = 7200
    JWT_TOKEN_LOCATION = ['headers', 'query_string']
    JWT_QUERY_STRING_NAME = 'access_token'

    SQLALCHEMY_DATABASE_URI = os.environ.get(
        'DATABASE_URL',
        'mysql+pymysql://root:123456@localhost:3306/face_attendance',
    )
    SQLALCHEMY_TRACK_MODIFICATIONS = False

    IMAGES_DB_PATH = os.path.join(BASE_DIR, 'images_db')
    UPLOAD_FOLDER = os.path.join(BASE_DIR, 'static', 'uploads')

    _dlib_candidates = [
        os.environ.get('DLIB_MODEL_PATH', ''),
        os.path.join(BASE_DIR, 'shape_predictor_68_face_landmarks.dat'),
    ]
    DLIB_MODEL_PATH = next(
        (path for path in _dlib_candidates if path and os.path.exists(path)),
        _dlib_candidates[1],
    )

    TIMEZONE_OFFSET_HOURS = int(os.environ.get('TIMEZONE_OFFSET_HOURS', '8'))
    ALLOW_MULTI_CHECKIN_PER_DAY = os.environ.get(
        'ALLOW_MULTI_CHECKIN_PER_DAY',
        'false',
    ).lower() in ('1', 'true', 'yes', 'on')
