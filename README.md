# 基于 Flask 的人脸考勤与合照识别系统

一个面向课堂、班级活动、实验教学演示的智能考勤系统。  
系统将“基础考勤 + 合照识别 + 情绪分析 + 安全防伪”整合到同一套 Web 平台中，支持教师与学生双角色使用。

本项目当前已经覆盖以下能力：

- 基础考勤：摄像头采集、活体检测、人脸识别签到、考勤记录查询与 Excel 导出
- 合照识别：上传多人合照后自动检测并批量识别人脸，生成活动参与名单和参与次数统计
- 情绪分析：在考勤与合照流程中提取面部区域并记录情绪结果，支持后端统计和前端图表展示
- 系统安全：通过“动作活体 + 反照片/反视频重放”双层机制降低虚假考勤风险
- 权限控制：支持教师与学生账号区分，教师可管理班级、导出数据、查看全局统计

---

## 1. 项目目标

本项目的设计目标不是只做“能识别”的人脸签到，而是做一套可用于课程展示、实验验收和协作开发的完整系统：

1. 让学生能够通过摄像头快速完成签到，同时尽量减少误识别与冒签
2. 让教师能够通过上传合照自动生成活动参与名单，减少手工点名成本
3. 让系统能够在识别过程中同步产出情绪数据，形成可统计、可视化的行为数据
4. 让项目结构清晰、算法路径明确，便于协作者理解、讲解和二次开发

---

## 2. 技术栈

### 2.1 后端

- `Flask`：Web 服务框架
- `Flask-JWT-Extended`：登录认证与权限控制
- `Flask-SQLAlchemy`：ORM 数据访问
- `PyMySQL`：MySQL 驱动
- `OpenPyXL`：Excel 导出

### 2.2 计算机视觉与识别

- `OpenCV`：图像处理、摄像头帧操作、基础检测辅助
- `dlib`：68 点人脸关键点模型
- `face_recognition`：基于 dlib 的人脸编码与相似度匹配
- `DeepFace`：情绪分析；在可用时也参与 anti-spoof 辅助判断
- `RetinaFace`：合照场景下的小人脸检测主力后端（通过 DeepFace 检测接口调用）

### 2.3 前端

- 原生 `HTML + CSS + JavaScript`
- `Chart.js`：情绪统计、活动参与次数柱状图

---

## 3. 核心业务功能

### 3.1 基础考勤

- 学生或教师登录系统
- 前端启动摄像头并进入活体检测流程
- 活体通过后拍照并上传到后端
- 后端执行人脸识别和最终反欺骗校验
- 若识别成功，则写入考勤记录、情绪记录，并返回学生信息

### 3.2 合照识别

- 教师上传活动合照
- 后端检测合照中的所有人脸
- 对每张脸提取特征并与人脸底库批量匹配
- 系统生成活动名单、识别结果表格、标注图像、活动参与次数统计

### 3.3 情绪分析

- 在基础考勤成功后，对签到人脸区域执行情绪识别
- 在合照识别成功后，对已识别学生的人脸区域异步执行情绪识别
- 所有情绪结果均写入 `emotion_records` 表
- 教师或学生可查看情绪统计图、记录表和导出结果

### 3.4 安全防伪

- 前置活体挑战：张嘴 + 左右头部移动
- 并行反重放检测：模糊度、像素运动、框运动、屏幕条纹、高光、挑战时序
- 最终反欺骗判定：活体通过后仍需在签到前做一次最终验证

---

## 4. 目录结构与整体架构

> 下面是当前项目的核心目录树，省略了缓存、数据文件和编译产物，只保留实际开发相关部分。

```text
flask_app_attendance/
├── app.py
├── config.py
├── init_db.py
├── requirements.txt
├── requirements_web.txt
├── README.md
├── shape_predictor_68_face_landmarks.dat
├── api/
│   ├── __init__.py
│   ├── auth.py
│   ├── attendance.py
│   ├── users.py
│   ├── group_photo.py
│   └── emotion.py
├── models/
│   ├── __init__.py
│   ├── user.py
│   ├── attendance.py
│   ├── activity.py
│   ├── emotion.py
│   └── liveness.py
├── services/
│   ├── __init__.py
│   ├── attendance_service.py
│   ├── face_service.py
│   ├── anti_spoof_service.py
│   ├── face_detection_service.py
│   ├── group_photo_service.py
│   ├── emotion_service.py
│   └── export_service.py
├── my_face_recognition/
│   ├── __init__.py
│   ├── f_face_recognition.py
│   ├── f_storage.py
│   └── f_main.py
├── templates/
│   ├── base.html
│   ├── login.html
│   ├── register.html
│   ├── attendance.html
│   ├── group_photo.html
│   ├── emotion_stats.html
│   ├── admin/
│   │   ├── dashboard.html
│   │   ├── users.html
│   │   └── attendance_records.html
│   └── student/
│       └── my_records.html
├── static/
│   ├── css/
│   │   └── style.css
│   └── js/
│       ├── camera.js
│       ├── liveness.js
│       ├── attendance.js
│       ├── group_photo.js
│       ├── admin.js
│       └── emotion.js
└── images_db/
    └── *.jpg
```

---

## 5. 各层职责说明

### 5.1 `app.py`

应用总入口，负责：

- 创建 Flask 应用
- 加载配置
- 初始化数据库、JWT、CORS
- 注册全部蓝图
- 注册页面路由
- 在后台线程中预热 DeepFace 和人脸识别底库

### 5.2 `api/`

接口层，负责：

- 接收前端请求
- 解析参数
- 执行权限校验
- 调用服务层
- 返回 JSON 或 Excel 文件

主要接口模块：

- `auth.py`：登录、刷脸登录、注册、获取当前用户信息
- `attendance.py`：活体会话、活体逐帧检测、签到识别、记录查询、导出
- `users.py`：用户管理、人脸上传、批量导入、批量删除、批量更新
- `group_photo.py`：合照识别、活动名单查询、统计导出
- `emotion.py`：情绪记录查询、统计与导出

### 5.3 `models/`

数据模型层：

- `User`：账号与学生档案
- `AttendanceRecord`：考勤记录
- `Activity`：活动主表
- `ActivityParticipant`：活动参与明细
- `EmotionRecord`：情绪识别结果
- `LivenessSession`：活体会话数据库状态

### 5.4 `services/`

算法和业务核心层：

- `attendance_service.py`：签到识别与考勤落库
- `face_service.py`：活体检测状态机
- `anti_spoof_service.py`：反照片/反视频重放攻击
- `face_detection_service.py`：合照/注册照人脸检测
- `group_photo_service.py`：合照多人识别与名单生成
- `emotion_service.py`：DeepFace 情绪分析
- `export_service.py`：各类 Excel 导出

### 5.5 `my_face_recognition/`

兼容旧版的人脸识别封装，主要保留：

- 人脸检测
- 人脸特征提取
- 特征比对
- 底库图片加载

### 5.6 `templates/` 与 `static/`

前端展示层：

- `templates/`：页面骨架
- `static/js/`：业务交互逻辑
- `static/css/`：统一样式

---

## 6. 页面与角色说明

### 6.1 教师角色

教师可访问：

- 登录页
- 管理后台首页
- 用户管理
- 考勤记录查询与导出
- 合照识别页面
- 情绪统计页面

教师权限能力：

- 创建/修改/删除学生
- 批量导入人脸底库
- 上传合照并识别
- 导出考勤、活动、情绪数据
- 查看全局统计图表

### 6.2 学生角色

学生可访问：

- 登录页
- 注册页
- 考勤签到页
- 我的考勤记录页
- 个人情绪记录/统计（受接口权限限制）

---

## 7. 数据库表设计

### 7.1 `users`

字段含义：

- `id`：学号/账号主键
- `name`：姓名
- `password`：加密后的密码
- `role`：角色，`student` / `teacher`
- `gender`、`age`、`address`、`ethnicity`、`class_name`：学生档案信息
- `face_image`：底库人脸图片文件名
- `created_at`：创建时间

### 7.2 `attendance_records`

- `student_id`、`student_name`：签到学生
- `check_time`：UTC 存储的签到时间
- `date`：业务日期，用于判断“今天是否已签到”
- `status`：签到状态
- `method`：签到方式

### 7.3 `activities`

- 一次合照识别对应一条活动记录
- 保存活动名称、创建时间、创建人

### 7.4 `activity_participants`

- 保存某位学生参加了哪次活动
- 可直接用来统计活动参与次数

### 7.5 `emotion_records`

- 保存学生、来源、时间、情绪标签
- 来源包括 `attendance` 与 `group_photo`

### 7.6 `liveness_sessions`

- 保存活体会话的数据库状态
- 包括是否通过、是否过期、动作计数等

---

## 8. 运行环境要求

推荐环境：

- Windows 10 / 11
- Python 3.10 ~ 3.12
- MySQL 5.7+ 或 8.x
- Chrome / Edge 浏览器
- 可用摄像头

说明：

- `dlib` 在 Windows 下安装可能依赖编译环境，若 `pip install dlib` 失败，建议使用预编译 wheel
- `DeepFace` 首次使用会下载模型权重，因此第一次运行可能较慢

---

## 9. 安装与部署

### 9.1 创建虚拟环境

```bash
python -m venv venv
```

Windows：

```bash
venv\Scripts\activate
```

### 9.2 安装依赖

```bash
pip install -r requirements.txt
```

如果你只需要部署 Web 相关的基础环境，也可以参考：

```bash
pip install -r requirements_web.txt
```

### 9.3 创建数据库

```sql
CREATE DATABASE face_attendance CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
```

### 9.4 配置数据库与密钥

推荐通过环境变量设置：

```bash
set DATABASE_URL=mysql+pymysql://root:你的密码@localhost:3306/face_attendance
set SECRET_KEY=your_flask_secret_key
set JWT_SECRET_KEY=your_jwt_secret_key
```

Linux / macOS：

```bash
export DATABASE_URL=mysql+pymysql://root:你的密码@localhost:3306/face_attendance
export SECRET_KEY=your_flask_secret_key
export JWT_SECRET_KEY=your_jwt_secret_key
```

### 9.5 初始化数据库

```bash
python init_db.py
```

系统会自动创建默认教师账号：

- 账号：`admin`
- 密码：`admin123`

### 9.6 启动服务

```bash
python app.py
```

浏览器访问：

```text
http://127.0.0.1:5000
```

---

## 10. 前端到后端的完整业务流程

## 10.1 基础考勤完整流程

### 前端流程

1. 进入考勤页，调用浏览器摄像头
2. 点击“开始活体检测”
3. 前端按固定频率采集视频帧并上传到 `/api/attendance/liveness/check-frame`
4. 活体通过后，点击拍照或自动识别
5. 将图像上传到 `/api/attendance/recognize`
6. 实时渲染返回的学生信息、情绪、置信度和防伪结果

### 后端流程

1. 创建活体会话 `LivenessSession`
2. 每帧执行活体状态机和并行反重放检测
3. 活体通过后，执行单人识别
4. 再做最终反重放验证
5. 若通过，写入考勤记录和情绪记录
6. 返回签到结果给前端

---

## 10.2 合照识别完整流程

### 前端流程

1. 教师上传合照或使用摄像头拍摄
2. 输入活动名称
3. 调用 `/api/group-photo/recognize`
4. 前端展示：
   - 合照标注图
   - 每张脸的识别结果表格
   - 识别人数、未知人数、总人数
5. 成功后刷新活动列表、参与人数统计和柱状图

### 后端流程

1. 合照缩放到最大边不超过 `1600`
2. 执行多人脸检测
3. 对每张脸提取一次特征
4. 与底库特征矩阵做批量匹配
5. 保存活动记录与活动参与名单
6. 将情绪分析拆到后台线程异步执行
7. 生成标注图并返回

---

## 10.3 情绪分析完整流程

### 考勤场景

1. 签到识别成功
2. 根据最终人脸框裁剪人脸区域
3. 调用 DeepFace 进行情绪分类
4. 将结果写入 `emotion_records`

### 合照场景

1. 合照识别成功
2. 对已识别学生的人脸区域创建后台任务
3. 后台线程逐个调用 DeepFace
4. 将结果异步写入 `emotion_records`

---

## 11. 核心算法详解

这一部分是项目展示的重点。

---

## 11.1 基础人脸识别算法

使用组件：

- `face_recognition.face_locations`
- `face_recognition.face_encodings`
- `face_recognition.face_distance`

### 识别流程

1. 将输入图像从 BGR 转换为 RGB
2. 若图像宽度过大，先缩小到 `ATTENDANCE_FRAME_WIDTH`
3. 检测人脸位置
4. 若存在多张脸，只选择面积最大的人脸作为签到目标
5. 提取 128 维特征向量
6. 与底库特征矩阵计算欧式距离
7. 取最近邻结果
8. 若距离超过阈值，则判定为 `unknow`
9. 再将距离值映射为百分比置信度用于前端展示

### 为什么只取最大脸

签到场景默认是单人面对摄像头。  
若画面中出现背景人脸，直接全部识别会增加误判风险。  
因此系统只对最大人脸做识别，这是一个偏安全的设计。

---

## 11.2 活体检测算法

实现文件：

- `services/face_service.py`

使用组件：

- OpenCV Haar 人脸检测
- dlib 68 点关键点
- Mouth Aspect Ratio（MAR）

### 核心思想

不是让用户做“眨眼”，而是做“张嘴 + 左右转头”动作挑战。  
这种设计的原因是：

- 张嘴在普通摄像头下比眨眼更稳定
- 左右转头可以增加动作随机性
- 与反重放模块结合后，更容易拦截照片和视频攻击

### 算法步骤

1. 检测当前帧中的人脸
2. 基于 dlib 68 点提取口部关键点
3. 计算 `MAR = 口部纵向开度 / 横向宽度`
4. 使用最近多帧的闭嘴数据估计个人基线
5. 动态计算“开嘴阈值”和“闭嘴阈值”
6. 当系统检测到“先稳定闭嘴，再稳定张嘴”时，记为一次有效动作
7. 达到要求次数后，进入头部移动挑战
8. 记录头部中心与鼻尖相对位置
9. 要求头部朝随机方向偏移，再回到中间，再向反方向偏移
10. 通过后标记活体挑战成功

### 为什么使用动态阈值

不同人的脸型、摄像头角度、嘴唇厚度差异很大。  
固定阈值容易误伤，因此系统通过若干闭嘴帧估计当前用户的个体基线，再动态放宽或收紧阈值。

---

## 11.3 反照片/反视频重放算法

实现文件：

- `services/anti_spoof_service.py`

这是本系统安全性的核心。

### 总体思路

系统不只依赖动作活体，而是做“双层防护”：

1. 前层：活体动作挑战
2. 后层：反重放特征分析

即使攻击者用预录视频试图模仿动作，也仍然可能在第二层被拦截。

### 采集的统计特征

系统会对连续多帧人脸区域统计以下指标：

- `blur`：清晰度，使用拉普拉斯方差估计
- `brightness`：亮度均值
- `area_ratio`：人脸框占整张图的面积比例
- `motion`：像素级运动强度
- `box_motion`：人脸框中心移动幅度
- `banding`：屏幕条纹/扫描线强度
- `highlight_ratio`：高亮反光区域比例
- `challenge_age`：活体通过后到签到前的时间间隔
- `frames_after_pass`：活体通过后是否还有确认帧

### 早期并行拦截（活体阶段）

在活体检测过程中，系统会并行检查：

- 是否存在持续的屏幕条纹
- 是否出现“低清晰度 + 条纹 + 强运动”的典型回放特征
- 是否挑战持续时间异常过长

如果同类可疑现象连续多次出现，就直接提前中断本次会话。

### 最终反重放判定（签到前）

当用户点击签到时，系统会综合以下规则：

#### 硬失败规则

命中任一条即判定失败：

- 没有完成随机动作挑战
- 活体总帧数过少
- 挑战通过后等待时间过长
- 挑战通过后缺少确认帧
- 出现“低清晰度 + 强条纹 + 明显运动”的强视频回放特征

#### 可疑规则

用于增加怀疑度或拉低最终分数：

- 画面过模糊
- 像素有变化但人脸框几乎不动
- 条纹与亮度抖动同时明显
- 高亮反光与条纹同时出现

#### 综合得分

系统会根据以下项目累计得分：

- 是否完成挑战
- 总帧数是否足够
- 清晰度是否足够
- 像素运动是否自然
- 外框运动是否自然
- 人脸面积变化是否自然
- 条纹是否较弱
- 高光是否较弱
- 活体验证后是否及时签到

若总分低于 `ANTI_SPOOF_MIN_SCORE`，则拒绝签到。

### DeepFace anti-spoof 辅助

若当前环境中的 DeepFace 版本支持 `anti_spoofing=True`，系统还会额外调用一次其内置防伪能力。  
最终结果会融合：

- DeepFace anti-spoof 输出
- 本地规则系统输出

严格模式下，只要其中一侧较强地判假，系统就倾向保守拒绝。

---

## 11.4 合照检测算法

实现文件：

- `services/face_detection_service.py`

### 当前优化后的策略

为提升速度并尽量保留小人脸检测能力，当前合照检测流程已经做了简化：

1. 只保留 `RetinaFace`
2. 合照先统一缩到最大边不超过 `1600`
3. 先做一次整图检测
4. 若检测人脸数少于阈值（默认 `10`），再补一次 `tile` 检测
5. 使用 `IoU + 中心距离` 做简单去重
6. 不再对每个候选框做二次编码验证

### 为什么这样设计

旧方案中“多后端 + 多尺度 + 多 tile + 多增强 + 二次验证”虽然召回高，但重复计算严重，速度非常慢。  
当前方案把检测压缩为：

- 一次整图主检测
- 一次条件性补充 tile 检测

这样可以在速度与小脸能力之间取得更适合课堂场景的平衡。

---

## 11.5 合照批量识别算法

实现文件：

- `services/group_photo_service.py`

### 批量识别流程

1. 对合照做统一缩放
2. 检测所有人脸框
3. 对每张脸扩框裁剪
4. 将人脸裁剪统一缩放到 `112 × 112`
5. 每张脸只提取一次特征向量
6. 与底库矩阵批量计算距离
7. 采用“最近邻 + margin”规则过滤不稳定匹配
8. 同一学号只分配给距离最优的一张脸

### 为什么加入 margin 规则

如果最近邻与次近邻距离非常接近，说明模型不够确定。  
这时系统不会轻易输出已知身份，而是保守地保留为“未知”，减少误认。

---

## 11.6 情绪识别算法

实现文件：

- `services/emotion_service.py`

### 使用模型

- DeepFace Emotion 分析

### 流程

1. 先对输入人脸区域进行裁剪
2. 若图像过小，则放大到约 `224` 尺度
3. 调用 `DeepFace.analyze(..., detector_backend='skip')`
4. 输出主情绪标签

### “neutral 纠偏”机制

实际项目中，DeepFace 很容易把很多样本都判成 `neutral`。  
因此系统增加了一个简单修正：

- 如果 `neutral` 只比第二高情绪略高
- 且第二高情绪分数不低

则改为输出第二高情绪，减少“全是平静”的现象。

---

## 12. 当前合照优化说明

当前项目中的合照识别流程已经针对速度做过一轮显著优化：

- 删除了多模型轮询（如 MTCNN、OpenCV）
- 删除了合照多尺度大循环
- 删除了逐框二次 `face_encodings + landmarks` 验证
- 删除了“增强图再提一次特征”
- 将情绪识别从合照主线程中移出，改为后台异步记录

### 优化后的目标

- 整体耗时降低到旧流程的约 20%~30%
- 在 10~25 人合照中尽量保持较高召回率
- 在教学展示环境中明显提升响应速度

> 若实际场景以 30~50 人大合照为主，建议进一步调优：
> `GROUP_DETECT_PRIMARY_SIDE`、`GROUP_DETECT_TILE_SIZE`、`GROUP_MIN_FACE_SIZE`

---

## 13. 主要页面与接口清单

### 页面路由

- `/`：登录页
- `/register`：注册页
- `/attendance`：考勤页
- `/group-photo`：合照识别页
- `/emotion-stats`：情绪统计页
- `/admin/dashboard`：教师后台首页
- `/admin/users`：用户管理
- `/admin/attendance-records`：考勤记录查询
- `/student/my-records`：学生个人记录页

### 主要 API

#### 认证

- `POST /api/auth/login`
- `POST /api/auth/face-login`
- `POST /api/auth/register`
- `GET /api/auth/me`

#### 考勤

- `POST /api/attendance/liveness/start`
- `POST /api/attendance/liveness/check-frame`
- `POST /api/attendance/recognize`
- `GET /api/attendance/records`
- `GET /api/attendance/export`
- `GET /api/attendance/today-status`

#### 用户管理

- `GET /api/users`
- `POST /api/users`
- `GET /api/users/<uid>`
- `PUT /api/users/<uid>`
- `DELETE /api/users/<uid>`
- `POST /api/users/<uid>/face`
- `POST /api/users/batch-delete`
- `POST /api/users/batch-update`
- `POST /api/users/batch-import`

#### 合照识别

- `POST /api/group-photo/recognize`
- `GET /api/group-photo/activities`
- `GET /api/group-photo/activities/<activity_id>`
- `GET /api/group-photo/activities/<activity_id>/participants`
- `GET /api/group-photo/activities/<activity_id>/export`
- `GET /api/group-photo/stats`
- `GET /api/group-photo/export/stats`

#### 情绪分析

- `GET /api/emotion/records`
- `GET /api/emotion/stats`
- `GET /api/emotion/class-stats`
- `GET /api/emotion/export`

---

## 14. 关键配置项

以下配置最值得关注：

### 安全与活体

- `REQUIRED_MOUTHS`：要求张嘴次数
- `LIVENESS_SESSION_MINUTES`：活体会话有效期
- `ANTI_SPOOF_ENABLED`：是否启用反重放
- `ANTI_SPOOF_STRICT`：是否严格模式
- `ANTI_SPOOF_MIN_SCORE`：反重放最低通过分

### 基础考勤

- `ATTENDANCE_FRAME_WIDTH`：签到检测缩图宽度
- `ATTENDANCE_MATCH_TOLERANCE`：基础考勤距离阈值
- `ATTENDANCE_MIN_CONFIDENCE`：最低业务置信度

### 合照识别

- `GROUP_INPUT_MAX_SIDE`：合照最大边限制
- `GROUP_DETECT_PRIMARY_SIDE`：整图检测尺寸
- `GROUP_DETECT_TILE_SIZE`：tile 检测尺寸
- `GROUP_MIN_EXPECTED_FACES`：整图检测结果少于此值时才补 tile
- `GROUP_FACE_TOLERANCE`：合照匹配距离阈值
- `GROUP_FACE_CROP_SIZE`：人脸编码裁剪尺寸

### 时间与业务逻辑

- `TIMEZONE_OFFSET_HOURS`：业务时区
- `ALLOW_MULTI_CHECKIN_PER_DAY`：是否允许一天多次签到

---

## 15. 使用建议

### 注册照采集建议

- 使用单人正脸照片
- 避免逆光、强反光和严重模糊
- 照片中不要出现第二张脸
- 统一背景和拍摄距离会显著提升识别稳定性

### 签到环境建议

- 摄像头固定
- 光照均匀
- 学生逐个面对镜头
- 活体动作提示要足够清晰

### 合照拍摄建议

- 尽量保证前后排都能看到完整正脸
- 避免极强背光
- 合照尽量不要过度压缩
- 人脸最短边过小时会显著影响识别效果

---

## 16. 已实现的展示亮点

如果你需要给老师、答辩组或协作者展示，这个项目的亮点建议从下面几方面讲：

1. 不是简单的人脸比对，而是完整的“活体挑战 + 反重放 + 识别 + 记录 + 统计”系统
2. 合照识别支持自动生成人员名单，而不是只能单人签到
3. 情绪识别与考勤/合照流程打通，能形成二次数据价值
4. 架构上明确分为接口层、服务层、模型层、前端层，便于团队协作
5. 合照流程已经做过速度优化，具备工程上的性能意识

---

## 17. 当前局限与后续可优化方向

### 当前局限

- `face_recognition` 在大规模底库下会逐渐变慢
- 光照极差、遮挡严重时，识别率会下降
- 情绪识别属于辅助统计能力，不能等同于心理状态判断
- 反重放仍属于工程规则系统，不是专门训练的工业级活体模型

### 后续优化方向

- 引入 ANN 检索或向量数据库提升大底库匹配速度
- 用更稳定的人脸检测/识别模型替换现有组合
- 将反重放升级为专用活体模型
- 将合照识别改造成异步任务队列
- 增加更细粒度的班级、课程、活动统计维度

---

## 18. 启动顺序总结

如果你第一次部署，推荐按这个顺序操作：

1. 安装 Python 依赖
2. 配置 MySQL
3. 设置环境变量
4. 运行 `python init_db.py`
5. 启动 `python app.py`
6. 使用 `admin / admin123` 登录
7. 在用户管理页导入学生与底库图片
8. 测试基础考勤、合照识别、情绪统计

---

## 19. 版权与数据说明

- `images_db/` 中存放的是人脸底库图片，属于敏感数据
- 请勿将真实学生人脸数据直接上传到公开仓库
- 若用于课程演示，建议使用经过授权的数据或匿名化测试样本

---

## 20. 结语

这是一个偏“工程整合型”的视觉项目：  
它把人脸识别、活体检测、反重放、合照识别、情绪统计、权限控制、Excel 导出全部放进了一个统一平台。

如果你的目标是：

- 做课程设计
- 做毕业设计原型
- 做课堂展示系统
- 做协作开发样板

那么这个项目已经具备较完整的结构基础，并且核心算法路径清晰、可讲解、可演示、可继续扩展。
