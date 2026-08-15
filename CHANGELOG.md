# Changelog

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
