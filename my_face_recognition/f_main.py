"""旧版识别流程封装，主要用于兼容性保留与命令行调试。"""

import traceback

import cv2
import numpy as np

from my_face_recognition import f_face_recognition as rec_face
from my_face_recognition import f_storage as st


class rec:
    """
    旧版识别器类。

    属性：
        db_names: 底库身份标签列表。
        db_features: 底库特征矩阵。
    """

    def __init__(self):
        """初始化时从磁盘底库加载所有注册人脸特征。"""
        self.db_names, self.db_features = st.load_images_to_database()

    def recognize_face(self, im):
        """
        在图像中检测人脸并完成批量识别。

        参数：
            im: 输入图像。

        返回：
            dict:
                status: 执行状态。
                faces: 人脸框列表。
                names: 对应识别结果。
        """
        try:
            box_faces = rec_face.detect_face(im)
            if not box_faces:
                return {'status': 'ok', 'faces': [], 'names': []}

            if not self.db_names:
                return {
                    'status': 'ok',
                    'faces': box_faces,
                    'names': ['unknow'] * len(box_faces),
                }

            actual_features = rec_face.get_features(im, box_faces)
            match_names = rec_face.compare_faces(actual_features, self.db_features, self.db_names)
            return {'status': 'ok', 'faces': box_faces, 'names': match_names}
        except Exception as ex:
            error = ''.join(traceback.format_exception(etype=type(ex), value=ex, tb=ex.__traceback__))
            return {'status': 'error: ' + str(error), 'faces': [], 'names': []}

    def recognize_face2(self, im, box_faces):
        """
        对已知人脸框直接做识别。

        参数：
            im: 输入图像。
            box_faces: 已检测出的人脸框列表。
        """
        try:
            if not self.db_names:
                return 'unknow'
            actual_features = rec_face.get_features(im, box_faces)
            return rec_face.compare_faces(actual_features, self.db_features, self.db_names)
        except Exception:
            return []


def bounding_box(img, box, match_name=None):
    """
    将人脸框与识别姓名绘制到图像上。

    参数：
        img: 原图。
        box: 人脸框列表。
        match_name: 与人脸框一一对应的姓名列表，可为空。
    """
    match_name = match_name or []
    for i in np.arange(len(box)):
        x0, y0, x1, y1 = box[i]
        img = cv2.rectangle(img, (x0, y0), (x1, y1), (0, 255, 0), 3)
        if match_name:
            cv2.putText(img, match_name[i], (x0, y1 - 10), cv2.FONT_HERSHEY_SIMPLEX, 0.9, (0, 255, 0), 2)
    return img


if __name__ == '__main__':
    import argparse

    parse = argparse.ArgumentParser()
    parse.add_argument('-im', '--path_im', help='path image')
    args = parse.parse_args()

    image = cv2.imread(args.path_im)
    recognizer = rec()
    result = recognizer.recognize_face(image)
    image = bounding_box(image, result['faces'], result['names'])
    cv2.imshow('face recogntion', image)
    cv2.waitKey(0)
    print(result)
