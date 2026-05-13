"""底层人脸识别工具封装，基于 `face_recognition` 库实现检测、编码与比对。"""

import face_recognition
import numpy as np


def detect_face(image, model='hog', upsample=0):
    """
    检测图像中的人脸框。

    参数：
        image: RGB 格式的 numpy 图像数组。
        model: `face_recognition.face_locations` 所使用的检测模型，常见取值为 `hog` 或 `cnn`。
        upsample: 金字塔上采样次数，值越大越容易检出小脸，但速度越慢。

    返回：
        list[tuple]: 每个元素均为 `(top, right, bottom, left)` 形式的人脸框。
    """
    return face_recognition.face_locations(
        image,
        number_of_times_to_upsample=upsample,
        model=model,
    )


def get_features(img, box):
    """
    提取指定人脸框的 128 维人脸特征向量。

    参数：
        img: RGB 图像。
        box: 人脸框列表，格式与 `detect_face` 的返回值一致。

    返回：
        list[np.ndarray]: 每张人脸对应一个 128 维特征向量。
    """
    return face_recognition.face_encodings(img, box, num_jitters=1, model='small')


def compare_faces(face_encodings, db_features, db_names):
    """
    将待识别人脸特征与底库特征做最近邻匹配。

    参数：
        face_encodings: 当前待识别人脸的特征列表。
        db_features: 底库中的特征矩阵。
        db_names: 与 `db_features` 一一对应的学号/姓名列表。

    返回：
        list[str]: 匹配成功返回对应名称，失败返回 `unknow`。
    """
    match_name = []
    names_temp = db_names
    feats_temp = db_features

    for face_encoding in face_encodings:
        try:
            dist = face_recognition.face_distance(feats_temp, face_encoding)
        except Exception:
            dist = face_recognition.face_distance([feats_temp], face_encoding)

        index = np.argmin(dist)
        if dist[index] <= 0.6:
            match_name.append(names_temp[index])
        else:
            match_name.append('unknow')
    return match_name
