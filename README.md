# tencent-vod-aigc

ComfyUI 自定义节点：通过**腾讯云 VOD AIGC** 聚合服务调用 **MiniMax Hailuo H3**、**VS**
生视频模型与**混元 3D 世界生成**，另含本地 **3D 白模预演台**、生图、素材注册和
**MPS AI 音乐生成**（GL / MiniMaxMusic）。

协议为腾讯云 API v3（TC3-HMAC-SHA256 签名，`CreateAigcVideoTask` / `DescribeTaskDetail`，
音乐为 MPS `CreateAigcAudioTask` / `DescribeAigcAudioTask`），对应《VOD AIGC服务接入指南》3.17 节。
**纯标准库实现，无需额外 pip 安装**。

## 节点列表（菜单分类：Tencent VOD AIGC）

| 节点 | 功能 |
|---|---|
| `VOD AIGC - H3 文生视频` | 仅提示词生成视频 |
| `VOD AIGC - H3 图生视频（首/尾帧）` | 首帧 / 尾帧 / 首尾帧生视频，支持 ComfyUI IMAGE、图片 URL 或本地图片路径 |
| `VOD AIGC - H3 多模态参考生视频` | ≤9 图 + ≤3 视频 + ≤3 音频（总数 ≤12），支持本地文件或 URL |
| `VOD AIGC - VS 视频生成` | 腾讯云 VS 模型（2.0 / 2.0-fast / 2.0-mini / 2.5）四模式合一：文生视频 / 首帧 / 首尾帧 / 多模态参考（≤30 图 + ≤10 视频 + ≤10 音频，总数 ≤50），支持种子、高码率、尾帧图输出；含人脸素材需先经「创建素材」注册为 asset:// 引用 |
| `VOD AIGC - 创建素材` | CreateAigcMaterial 素材注册：URL → 异步任务 → 输出 `asset://asset-xxx`（VS 人脸素材前置），真人素材需 GroupId（活体认证） |
| `VOD AIGC - 文生图/图生图` | 生图：GEM / Jimeng（3.3.2）+ GPT-Image2（3.14，OG image2_low/medium/high），支持多图输出（1-8 张）、输出格式，可接 ComfyUI 图像、本地图片路径或参考图 URL；`preview_image` 输出 IMAGE 张量（原生预览 + 可接下游） |
| `VOD AIGC - 音乐生成 (MPS)` | AI 音乐生成：GL / MiniMaxMusic，支持歌词与纯音乐、参考音频（路径/URL）、mp3/wav 输出 |
| `VOD AIGC - 混元 3D 世界生成` | VOD `Hunyuan / 3d_2.0 / 3d_scene`：文本或 1-3 张参考图生成可漫游 3D 世界，输出本地路径和原生 `FILE_3D`（当前文档示例通常为 `.spz`） |
| `VOD AIGC - 3D 白模预演台` | 一体化 WebGL 工作台：空白/本地/SPZ 云端生成场景、立体人物、曲线速度、多摄影机/切镜及确定性镜头输出；只有主动确认生成场景时才调用 VOD |
| `VOD AIGC - 查询任务` | 按 TaskId 查状态（超时/失败排查用） |
| `VOD AIGC - 下载视频` | 按 URL 重新下载视频 |
| `VOD AIGC - 查看执行台账` | 显示 `output/vod_aigc/execution_history.jsonl` 中的历史记录（右下角浮窗） |

所有云端生成节点运行（成功或失败）都会自动写入执行台账
`output/vod_aigc/execution_history.jsonl`（时间、TaskId、提示词、计费要素、产物路径），
可用 `jq` / Excel 查询：

```bash
jq -r '[.time, .status, .resolution, .duration, .estimated_cost] | @tsv' output/vod_aigc/execution_history.jsonl
```

## 安装

```bash
cd custom_nodes
git clone https://github.com/yulewang56/tencent-vod-aigc.git
```

重启 ComfyUI（或前端左下角 Restart），右键画布 → 搜索 `VOD AIGC` 即可看到节点。
更新：`cd custom_nodes/tencent-vod-aigc && git pull`

> 也可以下载 zip 解压后放进 `custom_nodes/`（文件夹名随意，不影响加载）。

## 首次使用（30 秒）

**方式一（推荐）：首次使用弹窗**

在画布上添加任一云端生成/查询节点，若凭据未配置会自动弹出设置框——填写
SecretId / SecretKey / SubAppId（单价为选填，用于台账费用预估）→ 保存。
密钥只写入本地配置文件，不进入工作流 JSON。

`VOD AIGC - 3D 白模预演台` 的白模编辑、本地资产加载和预演渲染完全在本地运行；只有在
编辑器内选择「Tencent VOD Generated」、勾选费用确认并点击生成时，才需要凭据并创建云端任务。

**方式二：手动创建配置文件**

```bash
cd custom_nodes/tencent-vod-aigc
cp tencent-vod-config.example.json tencent-vod-config.json
# 编辑 tencent-vod-config.json，填入你的密钥（该文件已被 .gitignore 排除，永不入库）
```

配置结构（完整版，`tencent-vod-config.example.json` 已含全量 VS 价格表）：

```json
{
  "secret_id": "AKIDxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx",
  "secret_key": "xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx",
  "sub_app_id": "1500000000",
  "currency": "cny",
  "prices": { "768P": 0.1, "1080P": 0.2, "2K": 0.3, "4K": 0.5 },
  "image_prices": { "Jimeng 4.0": 0.1, "GEM 3.0": 0.1,
                    "OG image2_low": 0.05, "OG image2_medium": 0.1, "OG image2_high": 0.2 },
  "model_price_tables": {
    "VS": {
      "versions": {
        "2.5": {
          "cny": {
            "no_video_ref": { "480P": 0.673, "720P": 1.512, "1080P": 3.742, "2K": 4.538, "4K": 7.638 },
            "with_video_ref": {
              "input":  { "480P": 0.404, "720P": 0.908, "1080P": 2.238, "2K": 2.238, "4K": 2.238 },
              "output": { "480P": 0.404, "720P": 0.938, "1080P": 2.236, "2K": 2.715, "4K": 4.644 }
            }
          },
          "usd": { "no_video_ref": { "...": 0.0 }, "with_video_ref": { "input": {...}, "output": {...} } }
        },
        "2.0": { "...": "..." },
        "2.0-fast": { "...": "..." },
        "2.0-mini": { "...": "..." }
      }
    }
  }
}
```

> 密钥来源：腾讯云控制台 CAM（https://console.cloud.tencent.com/cam/capi）；
> SubAppId 在云点播控制台「应用管理」获取。

## 配置详解

**凭据解析优先级**：节点输入（填了就用）> `tencent-vod-config.json`

| 配置项 | 配置文件字段 |
|---|---|
| SecretId | `secret_id` |
| SecretKey | `secret_key` |
| SubAppId | `sub_app_id` |
| 币种 | `currency`：`cny`（元/秒，默认）/ `usd`（美元/秒），决定 VS 价格表取哪套 |
| 旧视频单价（元/秒） | `prices.768P / 1080P / 2K / 4K`（H3 等旧模型仍用此项） |
| 生图单价（元/张） | `image_prices.<模型全名>`（Jimeng 4.0 / GEM 3.0 / OG image2_*） |
| VS 计价表 | `model_price_tables.VS.versions.<版本>.<currency>`，见下 |

**VS 计费规则**（`model_price_tables.VS.versions.<2.5/2.0/2.0-fast/2.0-mini>.<cny/usd>`）：

- **无视频参考素材**：单价 = `no_video_ref[分辨率]`（单段）
- **有视频参考素材**：单价 = `with_video_ref.input[分辨率] + with_video_ref.output[分辨率]`（输入/输出**两段求和**）
- 费用 = 计费秒数（时长，不足 5 秒按 5 秒）× 单价；币种严格按 `currency` 取一套，
  版本 / 分辨率 / 币种缺失一律记 0（显示「¥未配置单价」），不回退、不跨币种混用
- example 中的 VS 价格为**人工转写 + 二次勘误**（`table_20260819 2.csv` 为准，单位元/秒；
  此前两个待复核值已在勘误版修正：2.0 输出 1080P/2K = 1.520/1.824，2.0-mini usd 输出梯度单调）
- **旧字段兼容**：`model_price_tables` 缺失或模型名非 VS（H3 等）时，单价自动回退
  `prices[分辨率]`，旧配置无需迁移

单价用于台账**费用预估**：视频按秒（不足 5 秒按 5 秒），生图按张（张数 × 模型单价）。
示例值不是真实价格——不同模型对应不同计费项（即梦→SI、OG→GPT-Image2 等），
请按《AIGC价格指南（客户）》填写；未配置时显示「¥未配置单价」。配置入口只有两处：节点输入框与配置文件。

## 参数说明（对应文档 3.17）

- **duration**：4–15 秒，计费按生成秒数（不足 5 秒按 5 秒）
- **resolution**：768P / 1080P（超分）/ 2K / 4K（超分），分辨率越高越贵
- **aspect_ratio**：文生/参考生支持 21:9、16:9、4:3、1:1、3:4、9:16；图生视频由输入图决定，此项会被忽略
- **audio_generation**：是否生成原生音频
- **storage_mode**：Temporary（URL 限时有效）/ Permanent（永久存储，可后续做超分增强，推荐生产用）
- **enhance_prompt**：是否启用 H3-Context-IR 提示词增强（未开源、仅 API 的模块）
- **use_cache**：结果缓存开关（默认 Enabled）。命中条件：提示词、分辨率、时长、参考素材等全部参数与历史**已成功**任务完全一致，且本地产物文件仍存在 → 直接复用本地产物（零 API 调用、零费用），台账记 `cached: true`；失败任务永不参与命中，产物丢失自动失效。需要新结果时改为 Disabled 或修改任一参数
- **input_region**：素材 URL 在海外时填 `oversea`（避免拉取失败）
- **endpoint**：默认 `vod.tencentcloudapi.com`；如已切换新版网关可填 `gateway.vod-qcloud.com`

### 音乐生成节点（VOD AIGC - 音乐生成 (MPS)）

- **model**：GL 2.0 / GL 3.0-clip / GL 3.0-pro，或 MiniMaxMusic 2.0 / 2.5 / 2.6
- **prompt**：音乐风格 / 演唱要求描述，≤2000 字符
- **lyrics**（可选）：歌词文本，换行分段（`AdditionalParameters.lyric`）；**与 is_instrumental 互斥**
- **is_instrumental**：纯音乐模式（`AdditionalParameters.is_instrumental=true`），与歌词互斥
- **ref_audio_paths / ref_audio_urls**（可选）：参考音频（生成带参考旋律的音乐），本地路径每行一个或 URL 直链
- **output_format**：mp3 / wav（留空跟随模型默认）；输出下载到 `output/vod_aigc/`，URL 约 12 小时有效期
- 接口：MPS `CreateAigcAudioTask` / `DescribeAigcAudioTask`（API 版本 2019-06-12，域名 `mps.tencentcloudapi.com`）；
  MPS 无 SubAppId，凭据仅需 SecretId / SecretKey；台账 mode 记 `t2a`（不计秒不计费）

### 3D 世界与白模预演

**混元 3D 世界生成**：

- 固定调用 `ModelName=Hunyuan`、`ModelVersion=3d_2.0`、`SceneType=3d_scene`
- 支持纯文本或 1-3 张参考图（ComfyUI IMAGE batch / 多行本地路径 / 多行 URL 三选一）
- 多图以多个 `FileInfos` 提交，属于实验能力；VOD 文档只明确确认图生 3D，账号未验证前不要假设
  三张图都会参与多视图重建。执行节点会创建真实付费任务
- 输出 `task_id / scene_url / scene_path / scene_3d`；`scene_3d` 为 ComfyUI 原生 `FILE_3D`，
  可直接连接 Load 3D / Preview 3D / Preview Splat；扩展名沿用服务实际返回值
- 3D 场景按次计费，节点台账记 `t23d/i23d`，不套用视频按秒单价；实际单次价格以商务配置为准

**3D 白模预演台**：

- 同一个编辑器内提供 `Blank / Local Asset / Tencent VOD Generated` 三种场景来源；云端模式
  可上传 1-3 张参考图并输入 Prompt，只有勾选付费确认后才能提交，前端持续显示任务状态，完成后
  自动下载并进入当前编辑空间
- `Local Asset` 可直接选择单个 GLB/GLTF/OBJ/PLY/SPZ 文件；文件会保存到
  `ComfyUI/input/vod_aigc/previs_assets`，路径自动回填并加载。也可手工填写
  ComfyUI `input/output/temp` 内的绝对路径；GLTF/OBJ 如依赖外部贴图或二进制文件，建议先打包为 GLB
- 点击节点上的「打开 3D 预演编辑器」进入 Three.js WebGL 主视窗；人物使用球体/胶囊/
  圆柱组合的立体 humanoid 简模，体块和球体也是实际 3D mesh
- 编辑器使用导演工作台布局：顶部切换场景/导演视角/机位视角，左侧集中放置选择、变换、人物、
  体块、机位和轨迹工具；机位观察窗悬浮在主视窗右上角，右侧全高区域专门用于场景或所选对象属性
- SPZ 使用本地打包的 Spark 2.1.0 在主视窗和摄影机观察窗原生渲染；GLB/GLTF/OBJ/PLY
  继续使用 Three.js loader。背景支持 Position、Rotation（度）、统一 Scale 和按边界居中落地
- 主视窗支持透视/顶/前/侧视图、Orbit 浏览、TransformControls 和轨迹控制点拖拽；人物或摄影机
  可使用「手绘轨迹」连续绘制曲线，或使用「逐点轨迹」精确设置转折点，完成后自动生成时间关键帧
- 场景「预演视觉样式」提供导演柔和、高对比语义和中性白模预设；天空、地面、网格、人物与道具
  可分别配色，多人物可自动获得跨帧、跨机位稳定的高对比颜色，每个对象也可覆盖颜色和不透明度
- 编辑器显示可在带光照材质、无光照语义色和线框检查间切换；镜头输出另行选择导演预览或
  `Object-ID` 风格的 AI 语义参考，因此导演查看方式不会意外改变导出用途
- 摄影机可从当前导演视角直接创建或更新，并可将 Look At 持续绑定到某个人物/物体；右上角
  镜头观察窗可独立选择摄影机或跟随当前 CUT
- 对象和摄影机 Position / Look At 轨迹支持 `linear`、`catmull_rom`、`bezier`，速度支持
  `keyframed`、`constant`、`ease_in`、`ease_out`、`ease_in_out`、`custom`；
  曲线匀速和 easing 使用弧长采样，`speed_description` 会写入下游参考提示词
- 支持最多 8 台摄影机方案：新增、复制、重命名、切换和删除；每台摄影机拥有独立的
  Position / Look At / FOV / Roll 关键帧，并可在底部时间线上添加切镜点
- 底部导演时间线按人物动作、空间轨迹、摄影机运镜、Look At 与 CUT 分层显示，使用秒数和语义
  片段作为默认视图；可直接点击轨道定位播放头、横向拖动菱形关键帧修改时间，重绘时会保留轨道
  与当前对象属性面板的滚动位置
- 时间线左上角可直接修改时长和 FPS，并实时换算输出帧数；最长受 240 帧安全上限约束，例如
  24 FPS 可编辑至 10 秒。更长镜头可降低 FPS 或拆分镜头，避免 ComfyUI `IMAGE` batch 占用
  过多内存；保存到节点时会同步更新 `frame_count / fps`
- 预演台提供最多 100 步场景级撤销/重做，覆盖路径点、对象、摄影机、CUT、背景变换和视觉样式；
  macOS 使用 `Command+Z / Command+Shift+Z`，Windows / Linux 使用
  `Ctrl+Z / Ctrl+Shift+Z`（也支持 `Ctrl+Y`）。焦点不在输入框时，`Delete / Backspace`
  可删除当前路径点或摄影机关键帧；工具栏中的「快捷键」可随时查看操作提示
- 旧版单摄影机 `{keyframes:[...]}`、v2 多摄影机和对象 `position/end/path` 会自动迁移为 V3，
  保存时仍同步兼容字段
- 节点输出 `IMAGE` batch 和原生 `VIDEO`；`fps` 控制帧率，启用 `export_video` 后同时写出 MP4，
  可直接连接 ComfyUI `Save Video` 或支持视频输入的下游节点，不再依赖 VideoHelperSuite
- 点击编辑器底部「渲染镜头输出」会严格按 frame_count/fps 和 cuts 逐帧渲染右上角摄影机；
  缓存包含 SPZ、人物、其他场景物体和当前导出视觉样式。节点执行时校验场景/相机/外观快照，
  修改后未重新渲染会明确报错，防止静默输出旧视频；没有浏览器缓存时，Python 兜底渲染也会应用
  天空、地面、网格、人物、道具和对象覆盖色
- 混元 `3d_scene` 属于生成式 3D 世界补全，常见 SPZ/3DGS 输出并不是单图摄影测量、CAD 或可编辑
  mesh 精确重建：单图通常只在参考视角附近稳定，遮挡区和侧后方由模型推断。需要空间一致性时，
  建议上传同一场景 2-3 个方向明确、光照一致的视图，并在 Prompt 写明布局、尺寸关系、门窗位置
  与相机方向
- `scene_path` 仍可写入 `background_asset_path`，`scene_3d` 仍可连接 `background_asset` 供节点
  执行使用；由于 ComfyUI 前端无法在打开编辑器时读取尚未执行的上游值，交互预览推荐在场景来源
  面板中直接生成，或填写已经存在的本地路径
- `reference_prompt` 输出会概括镜头和人物运动，可与预演 MP4 一起提供给支持参考视频的生成节点；
  生成模型把它作为运动/运镜参考，不保证逐帧复现摄影机轨迹

## 素材限制（来自文档，超限节点会直接报错）

**H3 节点**：

- 图片：单张 ≤30MB，宽高 [256, 5760]，比例 5:2~2:5
- 参考视频：单段 ≤50MB、2–15 秒、总时长 ≤15 秒
- 参考音频：单段 ≤15MB、2–15 秒，**不能单独输入**，必须配图/视频
- Base64 传参总大小 ≤70MB；混合输入总数 ≤12 个文件（图 ≤9 / 视频 ≤3 / 音频 ≤3）

**VS 节点**：

- 参考素材上限：**30 图 + 10 视频 + 10 音频，总数 ≤50**；音频不能单独输入
- 时长：2.0 / 2.0-fast / 2.0-mini 为 4–15 秒，2.5 为 4–30 秒（节点按版本校验兜底）
- 分辨率：2.0 系 480P/720P/1080P/2K/4K；2.5 按参数表 480P/720P/2K/4K
- 宽高比：21:9 / 16:9 / 4:3 / 1:1 / 3:4 / 9:16 / **adaptive**（由模型决定）
- **含人脸素材不能直接引用 URL**（服务端 ret:-4 拒绝），需先经「VOD AIGC - 创建素材」
  注册为 asset，URL 填 `asset://asset-xxx`（asset 引用不校验扩展名）

**通用**：

- 本地素材路径支持 `input/`、`output/` 相对前缀（如 `input/ref.mp4`），或绝对路径；
  路径素材与 URL 素材同样做**扩展名白名单校验**（图片 .jpg/.jpeg/.png/.webp/.bmp，视频 .mp4/.mov，音频 .mp3/.wav/.m4a/.aac），
  提交前本地报错；路径素材会参与结果缓存键（同参数复用产物）
- 视频 Prompt ≤7000 字符；音乐 Prompt ≤2000 字符

## 典型用法

**本地生图 → H3 生视频 → 超分**（专业管线雏形）：

```
Load Image（SD/Flux 生图）
    ↓
VOD AIGC - H3 图生视频（first_frame ← Load Image 输出）
    ↓ video_path
VideoHelperSuite / 其他视频节点 → 后处理
```

**一体化混元场景 → 白模运镜 → 参考视频**：

```
VOD AIGC - 3D 白模预演台
    打开编辑器
      → 场景来源：Tencent VOD Generated
      → 上传 1-3 图 + Prompt + 费用确认
      → SPZ 自动进入主视窗和摄影机观察窗
      → 编辑人物、曲线速度、多摄影机与切镜
      → 点击「渲染镜头输出」
    ├─ video      → Save Video 或下游视频输入
    └─ video_path → 启用 export_video 时直接得到预演 MP4
    ↓
支持参考视频的 VS / Kling 工作流 → 最终视频
```

独立的「混元 3D 世界生成」节点仍保留，适合批量工作流、资产复用和单独连接 Preview Splat。

## 素材引用语法（@N）

视频生成节点（VS / H3 多模态参考）的 prompt 支持 **`@N` 序号引用**（N 从 1 开始，
对应 `ref_images` 的第 N 帧；BatchImagesNode 的 `image0` = 第 1 张 = `@1`），
提交时自动转换为 API 的顺序引用格式「图N」。`@图片N` 为兼容写法。

```
人物：@1=皇后、@2=祺贵人
场景：@1 最里面坐着穿橙色衣服的是皇后
```

**能力边界（重要）**：`@名称` 绑定（如 `@皇后`，对应 `FileInfos.Text` 字段）是
腾讯接入层 **PixVerse 模型专属**能力——H3 / VS 模型不支持，写了会提交前报错
（提示改为「图1：皇后」式描述）。各模型在腾讯接入层的引用方式：

| 接入路径 / 模型 | 文档化协议 | 本插件适配 |
|---|---|---|
| 腾讯 VOD · H3 (Hailuo) | 「图1/视频1/音频1」顺序引用（指南 3.17；`FileInfos.Text` 仅 PixVerse 生效） | `@N` → 图N |
| 腾讯 VOD · VS (SeeDance) | 接入指南**未文档化** `FileInfos.Text` 名称绑定，也未确认透传火山 `@Image1` | 按参考图上传顺序将 `@N` → 图N；这是兼容策略，不代表模型原生语法 |
| 腾讯 VOD · PixVerse | `FileInfos.Text` + `@名称` 绑定 | （无节点） |
| 腾讯 VOD · Kling | `<<<image_1>>>` 等 element 语法 | （无节点） |
| 腾讯 VOD · Vidu | `@主体Id` 与 `[@name]` | （无节点） |
| MiniMax 官方直连 | `content[]` + `role`，按「图1/视频1/音频1」顺序描述 | （无节点，勿与本插件 `@N` 混用） |

`@N` 是本插件为腾讯 VOD 接入层提供的易用语法，不是模型官方原生协议。**普通文本中的
`@`（如邮箱）请写 `\@` 转义**（`邮件 \@qq.com` 原样保留 `@qq.com`）；`@N` 后请加
空格或标点（`@1皇后` 会被拒绝，`@1=皇后` 合法）。

## 常见错误

| 现象 | 原因 / 处理 |
|---|---|
| `H3 任务被拒绝（ErrCode=70000 ErrCodeExt=InvalidParameter.ViolationContent ...）` | Prompt 或素材命中内容合规拦截，修改提示词后重试 |
| `扩展名 ".m4s" 不支持, 允许: .mp4, .mov` | 参考视频 URL 必须是 .mp4/.mov 直链（B 站/抖音分片如 .m4s 不被接受）；网页视频请先下载合并成 .mp4 走 `ref_video_paths` |
| `ContentModerationFailed: content blocked by moderation` | 内容审核拦截。**实测参考视频带音轨（meme 音乐/人声）可能触发**——先去音轨再试：`ffmpeg -i 片段.mp4 -an 无声.mp4`；仍被拒则换片段/素材 |
| `任务失败 (ErrCode=...)` | 查看 message；错误信息均携带 TaskId，可去控制台核对 |
| `无法连接 ... (检查网络/代理)` | 本地网络/代理问题 |
| 生成很慢 | 视频生成需数分钟，轮询间隔默认 10s；错峰可省成本 |

## 测试

```bash
python tests/test_nodes.py        # H3/生图/音乐等既有功能（176 项）
python tests/test_vs.py           # VS 接入（76 项）
python tests/test_pricing.py      # VS 计价表（39 项）
# 全部为自包含测试，无需安装任何依赖（自带 ComfyUI/numpy/PIL stub，不联网）
```

## 更新日志

见 [CHANGELOG.md](CHANGELOG.md)。
