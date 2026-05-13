"""
API 蓝图公共工具模块。

当前提供教师权限装饰器，用于限制高权限接口只能由教师角色调用。
"""

from functools import wraps

from flask import jsonify, request
from flask_jwt_extended import get_jwt, verify_jwt_in_request


def teacher_required(fn):
    """
    教师权限装饰器。

    设计说明：
        1. 默认从请求头校验 JWT。
        2. 当接口通过 `<a>`、`window.open` 等方式下载文件时，
           允许通过 query string 中的 `access_token` 进行认证。
    """

    @wraps(fn)
    def wrapper(*args, **kwargs):
        locations = ['headers']
        if request.args.get('access_token'):
            locations = ['headers', 'query_string']

        verify_jwt_in_request(locations=locations)
        claims = get_jwt()
        if claims.get('role') != 'teacher':
            return jsonify({'error': '需要教师权限'}), 403
        return fn(*args, **kwargs)

    return wrapper
