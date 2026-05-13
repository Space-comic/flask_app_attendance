"""旧版人脸底库加载工具：从 `images_db` 中读取图片并构建底库特征矩阵。"""

import os
import traceback

import cv2
import numpy as np

import config as cfg
from my_face_recognition import f_main


def load_images_to_database():
    """
    扫描底库目录中的图片，提取特征并形成底库。

    规则：
        1. 只读取 jpg/jpeg 图片。
        2. 每张底库图必须且只能有一张人脸。
        3. 文件名去掉扩展名后作为该人脸的身份标签。

    返回：
        tuple[list[str], np.ndarray]:
            - 名称列表
            - 底库特征矩阵
    """
    list_images = os.listdir(cfg.path_images)
    list_images = [filename for filename in list_images if filename.endswith(('.jpg', '.jpeg', 'JPEG'))]

    names = []
    feats = []

    for file_name in list_images:
        image = cv2.imread(cfg.path_images + os.sep + file_name)
        box_face = f_main.rec_face.detect_face(image)
        feat = f_main.rec_face.get_features(image, box_face)
        if len(feat) != 1:
            continue

        new_name = file_name.split('.')[0]
        if not new_name:
            continue
        names.append(new_name)
        if len(feats) == 0:
            feats = np.frombuffer(feat[0], dtype=np.float64)
        else:
            feats = np.vstack((feats, np.frombuffer(feat[0], dtype=np.float64)))
    return names, feats


def insert_new_user(rec_face, name, feat, im):
    """
    将新用户的特征追加到内存底库，并将原图写入磁盘。

    参数：
        rec_face: 识别器对象。
        name: 新用户标识。
        feat: 新用户特征列表。
        im: 原始人脸图片。
    """
    try:
        rec_face.db_names.append(name)
        if len(rec_face.db_features) == 0:
            rec_face.db_features = np.frombuffer(feat[0], dtype=np.float64)
        else:
            rec_face.db_features = np.vstack((rec_face.db_features, np.frombuffer(feat[0], dtype=np.float64)))
        cv2.imwrite(cfg.path_images + os.sep + name + '.jpg', im)
        return 'ok'
    except Exception as ex:
        return ''.join(traceback.format_exception(etype=type(ex), value=ex, tb=ex.__traceback__))
