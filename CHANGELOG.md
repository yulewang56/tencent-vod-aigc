# Changelog

## [v1.24.0] - 2026-08-20

### Added
- **图片转可编辑 3D 白模节点**：输入同一空间 1-3 张图片，经腾讯混元视觉输出保守的房间、
  参考机位和对象包围盒，再由本地纯 Python 打包器生成独立节点 GLB、碰撞 GLB 与场景 Manifest
- **导演台原生直连**：重建节点输出 V3 `scene_json / camera_json`，可直接接入现有白模预演台，
  每个房间构件和物体都进入原生层级，可独立选择、移动、旋转、缩放、配色和参与镜头编排
- **语义与交互元数据**：Manifest 保存对象分类、置信度、观测/推断等级、可移动标志、碰撞代理，
  并为座椅、门、桌面、道具生成 seat/passage/surface/approach Anchor
- **显式尺度与费用控制**：支持已知房间宽度标定；调用混元视觉前必须开启付费确认，单图尺度与
  遮挡不确定性会写入重建报告，不将包围盒白模描述为测量级扫描

### Documented
- 明确腾讯 VOD `3d_scene` 仍是 SPZ/3DGS 视觉背景；腾讯云 AI3D 的 Geometry GLB 能力适用于
  单个物体，没有公开的整房间语义 Mesh 场景、Collider 或 NavMesh API

## [v1.23.0] - 2026-08-20

### Added
- **可编辑时间线时长**：时间线左上角新增时长和 FPS 输入，自动换算并同步节点的
  `frame_count / fps`；帧数上限由 120 扩展至 240，24 FPS 下可编辑最长 10 秒
- **关键帧直接拖动**：人物和摄影机空间轨迹的菱形关键帧可在时间线上横向拖动，轨道空白和
  播放头区域可直接定位时间，并提供常驻操作提示
- 云端 3D 世界生成面板明确区分主参考图与同一空间补充视图，并说明 3DGS/SPZ 属于生成式
  世界补全、不是单图 CAD/可编辑网格精确重建

### Fixed
- 点击时间线关键帧、运镜片段或轨道时保留轨道垂直/水平滚动位置，不再跳回顶部
- 右侧属性面板按场景、对象和机位分别保留滚动位置，时间线选择不会打断正在查看的参数区域

## [v1.22.0] - 2026-08-20

### Added
- **场景级撤销与重做**：预演台新增最多 100 步历史记录，覆盖对象增删、路径与控制点、摄影机
  关键帧、CUT、背景变换和视觉样式；连续拖拽与连续输入合并为一次操作，离散按钮操作分别记录
- **跨平台快捷键**：Windows / Linux 使用 `Ctrl+Z` 撤销、`Ctrl+Shift+Z` 或 `Ctrl+Y`
  重做；macOS 使用 `Command+Z` 撤销、`Command+Shift+Z` 重做
- **快速删除关键点**：焦点不在输入框时，`Delete` / `Backspace` 可删除当前路径点或摄影机
  关键帧；仍保留 `Esc` 取消绘制、`Enter` 完成逐点轨迹和空格播放/暂停
- 顶部工具栏新增撤销、重做和快捷键提示按钮，并根据历史栈状态自动禁用不可用操作

### Fixed
- 快速撤销/重做不同背景资产时使用加载代次保护，较早的 GLB/GLTF/OBJ/PLY/SPZ 异步请求
  不再覆盖较新的历史状态或留下错误场景
- 输入框获得焦点时保留浏览器原生文本撤销，不会误触发整个 3D 场景回退

## [v1.21.0] - 2026-08-20

### Added
- **预演视觉样式与语义配色**：场景面板新增导演柔和、高对比语义和中性白模预设，可独立配置
  天空、地面、网格、人物及道具默认色，并为多人物稳定分配高对比颜色
- **对象级外观覆盖**：每个人物、体块和球体可继承场景语义色或设置自定义颜色及不透明度；
  对象覆盖同时应用于主视窗、摄影机观察窗、WebGL 镜头输出和 Python 兜底渲染
- **预览与导出模式分离**：编辑器可在带光照材质、无光照语义色和线框模式间切换，镜头输出可
  独立选择导演预览或纯色 AI 语义参考，避免编辑视图选择意外改变下游参考视频用途

### Changed
- 视觉样式写入向后兼容的 V3 `scene.appearance`，并参与镜头缓存签名；修改颜色后旧缓存会被
  明确判定过期，旧工作流会自动补齐默认样式

## [v1.20.1] - 2026-08-20

### Fixed
- **导演视角机位观察窗对比度**：摄影机观察场景中的白模不再继承过强的默认自发光，人物和体块
  现在以正确材质明暗渲染，避免与浅色背景融为一体而看起来没有输出

## [v1.20.0] - 2026-08-20

### Added
- **导演工作台布局**：新增场景/导演视角/机位视角三种模式、左侧导演工具栏、主视窗右上角
  可切换摄影机的观察窗，以及独立的场景和对象/机位属性页
- **直接绘制人物与摄影机轨迹**：支持在 3D 视窗内手绘曲线或逐点点击创建轨迹，自动按路径长度
  生成时间关键帧；人物和摄影机均可继续拖动控制点或使用原有高级插值与速度设置
- **从当前视角创建机位**：可将导演视角直接保存为新摄影机，或用当前视角更新所选关键帧；
  摄影机可把 Look At 持续绑定到场景人物或物体
- **分层导演时间线**：人物动作、对象空间轨迹、摄影机运镜片段、Look At 约束和 CUT 分层显示，
  默认使用秒数与语义片段，高级关键帧仍可直接选择和编辑

### Changed
- 场景来源从拥挤的混合属性区拆分为独立标签；机位观察窗不再占用右侧属性栏高度
- 时间线左右栏滚动同步，轨迹绘制锁定起始目标，删除对象时同步清理摄影机 Look At 引用

## [v1.19.1] - 2026-08-20

### Fixed
- **本地 3D 场景选择与反馈**：`Local Asset` 现在可直接选择并上传 GLB/GLTF/OBJ/PLY/SPZ，
  自动保存到 ComfyUI input、回填路径并加载；手工路径为空、越界或加载失败时会显示明确错误，
  不再出现点击按钮无反馈

## [v1.19.0] - 2026-08-20

### Added
- **一体化 3D 场景生成**：白模预演台内新增场景来源面板，可直接上传 1-3 张参考图、填写
  Prompt、选择临时/永久存储，并在明确确认付费后提交混元 3D 世界任务；任务进度、失败原因和
  下载结果均留在同一个编辑界面，密钥只由本地 Python 后端读取
- **SPZ 同屏渲染**：本地打包 Spark 2.1.0，在主编辑视窗与右上角摄影机观察窗中直接加载
  混元常见的 SPZ/3D Gaussian Splatting 结果；与人物白模、轨迹、多机位和切镜共享坐标空间
- **背景坐标校准**：场景支持位置、欧拉角和统一缩放调整，以及按边界居中落地；参数持久化到
  节点工作流，重新打开编辑器后恢复
- **确定性 WebGL 镜头输出**：按 frame_count/fps 和 cuts 逐帧渲染观察摄影机，将包含 SPZ、
  白模和相机运动的 PNG 帧缓存到 output；节点执行时校验场景/摄影机快照并输出原生 IMAGE/VIDEO

### Changed
- `VOD AIGC - 3D 白模预演台` 从纯本地编辑节点升级为本地优先的混合工作台：空白场景和
  本地资产编辑仍不调用云端，只有主动选择 Tencent VOD 生成并确认后才创建付费任务

## [v1.18.0] - 2026-08-20

### Added
- **WebGL 3D 预演台 V3**：主视窗升级为本地打包的 Three.js 真 3D 编辑器，使用立体 humanoid
  简模、体块和球体；右上角提供独立镜头观察窗与摄影机标签，底部提供对象/摄影机/cuts 时间线
- **曲线与速度轨迹**：对象 Position、摄影机 Position / Look At 支持 Linear、Catmull-Rom、
  Cubic Bezier；支持按关键帧、匀速、缓入、缓出、缓入缓出和自定义速度曲线。匀速及 easing
  模式使用弧长查找表，避免曲线参数速度不均
- **原生 3D / 视频类型**：混元节点新增 `FILE_3D` 输出；预演节点新增 ComfyUI 原生 `VIDEO`
  输出、FPS 和可选 MP4 导出路径，可直接连接下游视频节点
- **最多三张场景参考图**：混元 3D 世界节点支持 IMAGE batch 或多行路径/URL 的 1-3 张参考图，
  并限制总数与 Base64 请求体大小；多视图 `FileInfos` 属实验能力，真实付费任务需按账号能力验证
- **本地场景资产读取**：新增仅允许 ComfyUI input/output/temp 路径的 3D 资产端点，供编辑器加载
  GLB/GLTF/OBJ/PLY 等资产；SPZ/3DGS 明确引导连接 ComfyUI 原生 Preview Splat

### Changed
- 场景与摄影机方案统一升级为 V3 track schema；旧 `position/end/path`、顶层单摄影机
  `{keyframes:[...]}` 和 v2 多摄影机 JSON 自动迁移，并继续保留兼容字段

## [v1.17.0] - 2026-08-20

### Added
- **混元 3D 世界生成节点**：接入 VOD `Hunyuan / 3d_2.0 / 3d_scene`，支持文本或单图
  （IMAGE / 本地路径 / URL）生成 3D 世界，轮询并下载服务返回的 `.spz` 等场景资产；
  结果缓存与台账使用 `t23d/i23d`，不误套视频按秒计费
- **3D 白模预演台节点**：本地软件渲染人物、体块和球体，支持对象起终点、简易步行动作、
  最多 8 台摄影机的 Position / Look At / FOV 多关键帧插值与时间轴切镜；输出 `IMAGE`
  帧序列和运镜参考提示词，可接 VideoHelperSuite 合成参考视频
- **可视化轨迹编辑器**：ComfyUI 节点内打开全屏 Canvas 编辑器，在俯视图直接拖拽人物/
  物体多段轨迹点或整条路径、摄影机机位和目标点；支持多摄影机方案、摄影机路径、切镜，以及当前画面和
  所选摄影机运镜起点双观察窗；无需腾讯云凭据

## [v1.16.9] - 2026-08-19

### 修复
- **素材引用解析重构**（评审修复）：单一 `normalize_prompt_refs()` 入口替代两段不一致正则——
  - 邮箱/社交账号不再误报（`@` 仅在词边界处视为引用；普通文本 `@` 用 `\@` 转义）
  - `foo@1.com` 不再被错误替换为 `foo图1.com`（`@N` 转换增加左右词边界）
  - Unicode 名称（`@Élodie` / `@キャラ` / `@퀸`）现在会被拒绝（原正则漏过）
  - `@1皇后` 粘连、`@图片A` / `@图片 1` / `@图片1abc` 格式错误均报明确错误
- **校验覆盖补齐**：H3 图生视频节点与 SDK `run_video_task()`（i2v/r2v）提交前归一化
  ——发布声明「提交前拒绝 @名称」现在对全部公开路径成立
- README 能力矩阵按接入路径拆分（腾讯 VOD H3/VS/PixVerse/Kling/Vidu vs MiniMax 直连），
  VS 行明确「接入指南未文档化名称绑定」；`\@` 转义用法已文档化

## [v1.16.8] - 2026-08-19

### Added
- **`@名称` 引用校验**：H3 / VS 节点提交前拒绝 prompt 中残留的 `@名称` 引用
  （如 `@皇后`）——腾讯接入层的 `FileInfos.Text` + `@名称` 绑定是 **PixVerse 模型专属**
  能力，H3 / VS 不支持，原样透传会被模型当普通文本忽略造成错绑；现在报错并提示改用
  `@N` 序号引用（`@1=皇后`）或「图1：皇后」描述
- tooltip 与 README 新增**素材引用能力矩阵**：H3/VS=顺序引用「图N」（`@N` 为本节点
  易用语法，非模型原生协议）；PixVerse=`@名称` 绑定；Kling/Vidu/MiniMax 直连各有
  原生语法——不把接入层能力误称为模型原生能力

## [v1.16.7] - 2026-08-19

### Added
- **任务拒绝报错可读性**：VS 视频生成节点被任务级拒绝（版权/真人检测等）时，
  把腾讯报错里的 `content[N]`（0 基素材索引）映射回素材名（文件名 / URL 前缀 /
  帧标签），如 `content[2](图片3.jpg)`——可直接定位是哪张素材被拦；越界索引保留原样

## [v1.16.6] - 2026-08-19

### 修复
- **参考图压缩（请求体超限）**：腾讯云网关对 `CreateAigcVideoTask` 请求体有 10MB 硬限制
  （实测 `RequestSizeLimitExceeded 10485760B`，与文档声称的 70MB 不符）——多张高分辨率
  参考图（PNG 原样 Base64）会提交失败。现在图片素材（IMAGE tensor / 本地路径）提交前
  本地压缩：RGB 白底合成 + 缩放（最长边 ≤2048）+ JPEG 迭代降质至单张 ≤1.2MB
  （参数序列固定、确定性，结果缓存键不受影响）；`_MAX_BASE64_TOTAL` 对齐网关实测 10MB。
  实测 5 张 2048×2048 高细节图压缩后 Base64 总量 6.4MB，可正常提交。

## [v1.16.5] - 2026-08-19

### Added
- **`@N` 提示弹层显示文件名**：候选从 `@N（第 N 张参考图）` 升级为 `@N（文件名）`——
  上游为 LoadImage 时直接读文件名；BatchImagesNode / ImageBatch 逐端口追溯上游文件名，
  推断不出回退「第 N 张参考图」。已用真实 ComfyUI 实例验证（5 图 batch 显示对应文件名）

## [v1.16.4] - 2026-08-19

### Added
- **`@N` 引用提示弹层**（前端扩展）：在 `VOD AIGC - VS 视频生成` / `VOD AIGC - H3 多模态参考生视频`
  的 prompt 输入框输入 `@` 时弹出可引用候选（`@1`..`@N`），N 从图上 `ref_images` 上游推断
  （LoadImage=1 张；BatchImagesNode / ImageBatch = 已连接端口数）；点击即插入到光标位置，
  输入非 `@` 内容自动关闭。已用真实 ComfyUI 实例验证（单图、5 图 batch、点击插入、关闭逻辑）

## [v1.16.3] - 2026-08-19

### Added
- **prompt `@N` 引用语法**：提示词可用 `@N`（或 `@图片N`）引用参考图，N 从 1 开始
  （BatchImagesNode 的 `image0` = 第 1 张 = `@1`），提交前自动展开为 API 多图格式「图N」；
  N 越界（大于参考图数）提交前本地报错。适用于 `VOD AIGC - VS 视频生成` 与
  `VOD AIGC - H3 多模态参考生视频`

## [v1.16.2] - 2026-08-19

### 修复
- **节点包加载修复补完**（v1.16.1 的绝对导入修复不完整）：ComfyUI 主程序本身就是
  `nodes.py`，已占用顶层模块名 `nodes`，`from nodes import ...` 会静默解析到
  ComfyUI **全局节点注册表**而非本包节点——加载显示成功（0.0s 无报错）但 VOD 节点仍缺失。
  改为用独立模块名 `tencent_vod_aigc_nodes` 经 `spec_from_file_location` 显式加载本包
  `nodes.py`；已用完整 ComfyUI 实例验证 **10 个节点全部注册**（含 VS 视频生成/创建素材）

## [v1.16.1] - 2026-08-19

### 修复
- **节点包加载失败**（v1.15.0 SDK 化引入）：`__init__.py` 的相对导入在 ComfyUI 的
  `spec_from_file_location` 加载机制下失败（模块名为目录名 `tencent-vod-aigc`，无点非法包名），
  导致整个包被跳过、全部节点从 UI 消失；改为绝对导入 + 显式 sys.path（10 节点注册恢复）
- **VS 价格表勘误**：按二次勘误（`table_20260819 2.csv`，单位元/秒）修正 example 配置与测试锁定值；
  此前两个待复核点已修正（2.0 输出 1080P/2K = 1.520/1.824；2.0-mini usd 输出梯度单调）

## [v1.16.0] - 2026-08-19

### Added
- **VS 视频生成节点**（`VOD AIGC - VS 视频生成`）：腾讯云 VS 模型（2.0 / 2.0-fast / 2.0-mini / 2.5）
  四模式合一（文生视频 / 首帧 / 首尾帧 / 多模态参考），素材上限 30 图 + 10 视频 + 10 音频（总数 ≤50），
  支持 Seed / LogoAdd / 高码率（ExtInfo `bitrate_mode=high`）/ 返回尾帧图（`return_last_frame`，双产物下载）、
  adaptive 宽高比、2.5 版 4-30 秒与 2K/4K 超分直出；含人脸素材支持 `asset://` 引用
- **创建素材节点**（`VOD AIGC - 创建素材`）：`CreateAigcMaterial` 素材注册（URL → 异步任务 → 输出
  `asset://asset-xxx`），VS 人脸素材前置；真人素材需 GroupId（活体认证走 core API）
- **素材与活体 API**（core）：`create_material` / `describe_material` / `delete_material` /
  `create_liveness_validate` / `describe_liveness_validate_result`
- **价格配置重构**：新增 `model_price_tables` 多维价格表（模型 × 版本 × 有无视频参考 × 输入/输出两段计费 ×
  分辨率 × 国内/国际站币种），`currency` 顶层语义；VS 台账按「无参考单段 / 有参考 input+output 两段求和」计费；
  旧 `prices` / `image_prices` 字段完全兼容（H3 / 生图零变化）

### 变更
- `build_video_payload` 参数化（`model_name` / `model_version` / `seed` / `logo_add` / `ext_info`），
  默认参数下 H3 payload 逐字节一致（测试锁定）
- `check_media_quota` 配额参数化（默认 9/3/3/12 保持 H3 契约，VS 用 30/10/10/50）

## [v1.15.0] - 2026-08-19

### Added
- **SDK 化**：抽出纯标准库 `vod_aigc_core.py`（无头可跑，零 ComfyUI/numpy/PIL 依赖），
  `nodes.py` 变薄壳仅保留节点定义；脚本侧可直接 `import vod_aigc_core` 走完整链路
  （TC3 签名 → 提交任务 → 轮询 → 下载 → 结果缓存 → 台账）
- core 公开 API：`call_api` / `build_video_payload` / `build_image_payload` / `build_music_payload` /
  `wait_for_task` / `download_file` / `cache_key` / `base_record` / `append_history` /
  `run_image_task` / `run_video_task` 等

### 变更
- 重构（行为零变化）：节点逻辑全部委托 core，UI 参数 / 默认值 / 显示名 / 台账格式均不变；
  `scripts/e2e_music.py` 改为直接 import core（移除 comfy stub）

## [v1.14.1] - 2026-08-17

### 修复
- 音乐生成节点显示名统一为 `VOD AIGC - 音乐生成 (MPS)`（与包内其他节点命名风格一致；接口仍为 MPS `CreateAigcAudioTask`）

## [v1.14.0] - 2026-08-17

### Added
- **图片本地路径输入**：多模态参考生视频（`ref_image_paths`）、首/尾帧图生视频（`first_frame_path` / `last_frame_path`）、
  生图节点（`ref_image_paths`）新增本地图片路径输入（每行一个，最多 9 张；与 IMAGE/URL 输入可并存，
  总数上限校验不变）。路径经 `~/`、`input/`、`output/` 解析后 Base64 上传（30MB 上限），
  并按白名单校验扩展名（.jpg/.jpeg/.png/.webp/.bmp，视频/音频路径同样补上校验）；
  路径素材参与结果缓存键（同参数复用产物）
- **AI 音乐生成节点**（`VOD AIGC - 音乐生成 (MPS)`）：MPS `CreateAigcAudioTask` / `DescribeAigcAudioTask`
  （API 版本 2019-06-12，域名 mps.tencentcloudapi.com）。模型 GL 2.0 / 3.0-clip / 3.0-pro 与
  MiniMaxMusic 2.0 / 2.5 / 2.6；支持歌词（`AdditionalParameters.lyric`）与纯音乐（`is_instrumental`）、
  参考音频（路径/URL）、mp3/wav 输出、结果缓存；MPS 无 SubAppId，凭据仅需 SecretId/SecretKey。
  台账新增 `t2a` 模式（不计秒不计费，查看节点显示「音乐」行）；`_sign_request` / `_call_api` /
  `_wait_for_task` 支持 version / service / action 参数（默认保持 VOD 配置，向后兼容）；
  附端到端验证脚本 `e2e_music.py`（真实调用需已配置 tencent-vod-config.json）

## [v1.13.0] - 2026-08-16

### Added
- **结果缓存**：生成节点新增 `use_cache` 参数（默认 Enabled）。同参数（提示词/分辨率/时长/参考素材指纹）已成功且产物仍在的任务，直接复用本地文件，零 API 调用、零费用
- 台账记录新增 `cache_key`（内容指纹）与 `cached`（命中标记）字段，缓存命中可审计

### Notes
- SessionId 未暴露：腾讯文档仅 PixVerse（3.12）请求参数支持 SessionId，H3/生图接口请求参数无此字段，遵循"只暴露文档化参数"原则不发明参数

格式：[语义化版本](https://semver.org/lang/zh-CN/)，全部提交记录见 [GitHub Releases](https://github.com/yulewang56/tencent-vod-aigc/releases)。

## [1.12.7] - 2026-08-16

### 文档
- 常见错误表补充：参考视频带音轨可能触发 `ContentModerationFailed`（实测 meme 音乐/人声命中），
  先去音轨再试（`ffmpeg -i x.mp4 -an 无声.mp4`）

## [1.12.6] - 2026-08-16

### 修复
- 素材路径支持 `~` 波浪号展开（如 `~/Downloads/xxx.mp4`）——此前 `os.path.isfile` 不识别
  shell 习惯写法导致"文件不存在"误报

## [1.12.5] - 2026-08-16

### 新增
- **URL 扩展名提交前校验**：参考视频仅允许 `.mp4` / `.mov`（服务端限制），图片 `.jpg/.jpeg/.png/.webp/.bmp`，
  音频 `.mp3/.wav/.m4a/.aac`——明确不支持的格式（如 B 站 `.m4s` 分片）在提交前本地报错，
  不再等任务跑完 22 秒才失败；无扩展名 URL 不拦截（交服务端判断）

## [1.12.4] - 2026-08-15

### 改进
- URL 类素材参数（ref_*_urls / first_frame_url 等）tooltip 明确"必须为可匿名下载的直链
  （.mp4/.jpg 等），不支持网页地址"——修复 B 站/抖音等页面 URL 导致的
  `media download failed (HTTP 412)`（网页地址返回 HTML，腾讯云下载器拿不到视频文件）

## [1.12.3] - 2026-08-15

### 新增
- **素材路径支持 input/ output/ 相对前缀**：`ref_video_paths` / `ref_audio_paths` 等本地素材参数
  填 `input/xxx.mp4` 自动解析到 ComfyUI 输入目录（生态惯例），`output/...` 解析到输出目录；
  绝对路径照常，纯相对路径兼容旧行为（按进程工作目录）

## [1.12.2] - 2026-08-15

### 变更
- 本地文件名命名组合：填 `filename` 时改为 `<filename>_<taskId尾8位>.<扩展名>`
  （如 `我的视频_3f9c2ab1.mp4`）——文件与台账/任务可追溯，taskId 尾号天然唯一，
  多次生成同名 hint 不会撞名；重名兜底仍加序号（`_1`）

## [1.12.1] - 2026-08-15

### 修复
- **图片节点预览协议**：改用 SaveImage 同款 `{"ui": {"images": [...]}, "result": (...)}` 返回——
  裸张量不会触发服务端预览；节点上现在直接显示生成图（`preview_image` 张量同时保留，可接下游）
- **凭据输入移入 optional**：4 个生成节点 + 查询节点的凭据字段从 required 移到 optional
  （与 `(optional)` 显示名一致），API 调用可完全省略凭据、纯走配置文件
- 台账装饰器保留字典返回的 ui 部分；路由注册防御 ComfyUI 进程外导入

## [1.12.0] - 2026-08-15

### 新增
- **图片节点 `preview_image` 输出**：下载的图片转成 IMAGE 张量（多图合成 batch），
  获得 ComfyUI 原生预览并可直连下游节点；转换失败返回 None 不阻塞主流程
- **生成节点本地文件名参数**：4 个生成节点新增选填 `filename`（不含扩展名，自动补全；
  多图自动加序号去重；留空保持 task_id 尾号 + URL 文件名）

## [1.11.0] - 2026-08-15

### 新增
- **生图计费预估**：配置文件新增 `image_prices`（元/张，按模型区分——不同模型对应不同计费项，
  如即梦→SI、OG→GPT-Image2）；台账生图记录新增 `model` / `image_count` 字段，
  费用 = 张数 × 模型单价；查看器显示「≈¥X.XX/N张」
- 首次使用弹窗新增**可折叠**的「生图单价」区块（5 个模型输入，选填，预填并随配置保存）

## [1.10.0] - 2026-08-15

### 新增
- **GPT-Image2 模型**（文档 3.14）：生图节点模型下拉新增 `OG image2_low / medium / high` 三档质量
- **多图输出**：`OutputImageCount`（OG 支持 1-8，仅 >1 时传接口），多图时全部下载，
  `image_url` / `image_path` 按行拼接输出
- **输出格式**：`OutputFormat`（png / jpeg，留空跟随模型默认）；分辨率档位新增 1K / 2K

## [1.9.1] - 2026-08-15

### 修复
- 生图节点 FileInfos 移除 `Category` 字段（生图结构与生视频不同，仅 `Type` + `Base64`/`Url`），
  修复 `UnknownParameter: FileInfos.0.Category is not recognized`

## [1.9.0] - 2026-08-15

### 新增
- **文生图/图生图节点**（`VOD AIGC - 文生图/图生图`，文档 3.3.2 `CreateAigcImageTask`）：
  模型 GEM / Jimeng，文生图不传素材，图生图可接 ComfyUI IMAGE（批量，每帧一张，≤9）或参考图 URL
- 台账 mode 自动推断 `t2i` / `i2i`（生图按张计费，秒数/费用记 0）

### 改进
- `_wait_for_task` 进度文案参数化（生图显示「生图生成中…」）；下载报错文案泛化（图片/视频通用）
- 参考生视频 `ref_images` 工具提示补充 batch 用法说明（多张图先合成 batch 或用 `ref_image_urls`）

### 修复
- 测试文件中途残留的提前退出块（此前测试只跑前 7 节）；全量 81 项自包含测试通过

## [1.8.0] - 2026-08-15

### 移除
- **环境变量配置通道**：`TENCENTCLOUD_SECRET_ID / TENCENTCLOUD_SECRET_KEY / VOD_SUB_APP_ID / VOD_PRICE_*`
  全部移除——配置入口收敛为「节点输入 + tencent-vod-config.json」两处，避免配置来源混淆
- **旧版 `credentials.json` 兼容读取**：仅保留 `tencent-vod-config.json` 一种配置文件
- `/tencent-vod-aigc/credentials/*` 接口别名：前端统一走 `/tencent-vod-aigc/config`

## [1.7.0] - 2026-08-15

### 变更
- **统一配置文件**：凭据与单价合并到 `tencent-vod-config.json`（模板 `tencent-vod-config.example.json`）；
  旧 `credentials.json` 仍可读取（仅兼容），新配置请写入新文件
- **单价可选配置**：`prices` 字段（元/秒）替代纯环境变量方式，解析优先级
  环境变量 `VOD_PRICE_<分辨率>` > 配置文件 > 0；`_estimate_cost` 改为运行时解析
- **首次使用弹窗升级**：新增 4 个选填单价输入框（标签带 `(optional)`），保存时合并写入配置文件；
  接口规范路径改为 `/tencent-vod-aigc/config`，旧 `/credentials/*` 保留为别名
- **README 重构**：精简为「节点列表 / 安装 / 首次使用 / 配置详解 / 参数 / 素材限制 / 常见错误」；
  历史版本记录移入本文件
- 节点列表补全：新增「VOD AIGC - 查看执行台账」

## [1.6.1] - 2026-08-15

### 变更
- 凭据输入框显示名改为 `secret_id (optional)` 等（通过前端 `display_name` 机制，
  键名不变，代码零改动）

## [1.6.0] - 2026-08-15

### 新增
- **首次使用弹窗**：添加生成/查询节点且凭据未配置时弹出设置框，一键写入凭据文件
  （本地 HTTP 接口，密钥不进入工作流 JSON；每会话只弹一次）
- **测试入库**：`tests/test_nodes.py` 随仓库分发（65 项自包含测试，已脱敏）
- 凭据字段工具提示标注「选填」，说明默认读取配置文件

### 修复
- 凭据文件回填：`credentials.json` 被 .gitignore 排除，密钥不随工作流传播

## [1.5.0] - 2026-08-15

### 新增
- **凭据一次配置**：`credentials.json` 回退机制，解析优先级
  节点输入 > 环境变量 > 配置文件；密钥从此不进入工作流 JSON

## [1.4.1] - 2026-08-15

### 修复
- **拒绝原因透传**：FINISH 但无输出文件时优先透传腾讯云拒绝原因
  （ErrCode / ErrCodeExt / Message，如 `InvalidParameter.ViolationContent`），
  不再误报「任务成功但未找到输出文件 URL」
- 所有任务级错误（拒绝/失败/超时）携带 TaskId（新增 `TaskError`）
- 台账失败记录回填 task_id

## [1.4.0] - 2026-08-15

### 新增
- **费用预估**：台账记录新增 `seconds_billed`（不足 5 秒按 5 秒）与 `estimated_cost`（元），
  单价经环境变量 `VOD_PRICE_*` 配置
- **可点击链接**：台账浮窗中「视频URL」打开在线视频，「本地文件」经 ComfyUI `/view` 接口播放

## [1.3.1] - 2026-08-15

### 修复
- 注册 `web/` 前端目录（pyproject `[tool.comfy] web` + `WEB_DIRECTORY`），浮窗扩展可被加载

## [1.3.0] - 2026-08-15

### 新增
- **台账查看节点**（`VOD AIGC - 查看执行台账`）+ 前端浮窗扩展：
  执行后右下角弹出历史记录（前端 1.48 不渲染纯文本输出，浮窗为社区标准做法）

## [1.2.x] - 2026-08-15

### 修复
- `folder_paths` 导入跨版本兼容（ComfyUI 0.33 中位于仓库根目录，非 `comfy` 包内）
- 查看节点输出改用 ui 显示协议（`{"ui": {...}, "result": (...)}`）

## [1.2.0] - 2026-08-15

### 新增
- **台账查看节点**：读取执行台账并在画布输出（初版，后续版本转为浮窗显示）

## [1.1.0] - 2026-08-15

### 新增
- **执行台账**：三个生成节点每次运行（成功/失败）自动追加 JSONL 记录到
  `output/vod_aigc/execution_history.jsonl`，含计费要素（时长/分辨率/音频/存储方式）

## [1.0.0] - 2026-08-14

### 新增
- 初始发布：5 个节点（文生 / 图生 / 参考生 / 查询 / 下载），TC3 签名 + 腾讯云 VOD 协议
- 修复（初始版内）：`x-tc-action` 小写签名、响应平铺结构解析、下载超时加固
