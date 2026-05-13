"""
一次性修复脚本：把批量导入时 name 字段被整个吞掉的用户
（如 `任舒翼-网安-男`）拆分成 name / class_name / gender。

规则：
  - 只处理 `name` 里包含 `- _ 空格` 分隔符的记录
  - 按分隔符切最多 3 段 -> name / class_name / gender
  - 只覆盖 class_name 和 gender 原本为空的字段
  - 性别支持 `男/女/M/F/male/female` 映射
  - 支持 --dry-run（默认）预览，--apply 实际写入
"""

import argparse
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app import create_app
from models import db
from models.user import User

SEP_RE = re.compile(r'[-_\s]+')
GENDER_MAP = {
    '男': '男', 'M': '男', 'm': '男', 'male': '男', 'Male': '男', 'MALE': '男',
    '女': '女', 'F': '女', 'f': '女', 'female': '女', 'Female': '女', 'FEMALE': '女',
}


def split_name(raw):
    """返回 (name, class_name, gender) 或 None 表示无需修复。"""
    if not raw:
        return None
    parts = [p for p in SEP_RE.split(raw, maxsplit=2) if p.strip()]
    if len(parts) <= 1:
        return None

    name = parts[0].strip()
    class_name = None
    gender = None

    if len(parts) >= 2:
        second = parts[1].strip()
        # 第二段如果本身就是性别，直接当性别（如 "张三-男"）
        if second in GENDER_MAP:
            gender = GENDER_MAP[second]
        else:
            class_name = second

    if len(parts) >= 3:
        third = parts[2].strip()
        mapped = GENDER_MAP.get(third)
        if mapped:
            gender = gender or mapped
        elif class_name is None:
            class_name = third

    return (name, class_name, gender)


def main(apply_changes=False):
    app = create_app()
    with app.app_context():
        users = User.query.all()
        plan = []
        for user in users:
            result = split_name(user.name)
            if not result:
                continue
            new_name, new_class, new_gender = result

            # 只覆盖空字段；原来有值的字段保留用户手动填写
            updates = {}
            if new_name and user.name != new_name:
                updates['name'] = new_name
            if new_class and not user.class_name:
                updates['class_name'] = new_class
            if new_gender and not user.gender:
                updates['gender'] = new_gender
            if not updates:
                continue

            plan.append((user, updates))

        print(f'共扫描 {len(users)} 个用户，{len(plan)} 个需要修复')
        print()
        for user, updates in plan[:20]:
            print(f'  {user.id} | {user.name!r} -> {updates}')
        if len(plan) > 20:
            print(f'  ... (共 {len(plan)} 条，仅展示前 20 条)')

        if not plan:
            return

        if not apply_changes:
            print()
            print('(dry-run) 未写入数据库。如确认无误请加 --apply 重跑。')
            return

        print()
        print('正在写入数据库...')
        for user, updates in plan:
            for key, value in updates.items():
                setattr(user, key, value)
        db.session.commit()
        print(f'完成，已更新 {len(plan)} 条记录。')


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--apply', action='store_true', help='实际写入数据库；默认只 dry-run 预览')
    args = parser.parse_args()
    main(apply_changes=args.apply)
