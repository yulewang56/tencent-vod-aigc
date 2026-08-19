# tencent-vod-aigc

ComfyUI 自定义节点：通过**腾讯云 VOD AIGC** 聚合服务调用 **MiniMax Hailuo H3** 生视频模型，
另含 **MPS AI 音乐生成**（GL / MiniMaxMusic）。

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
| `VOD AIGC - 查询任务` | 按 TaskId 查状态（超时/失败排查用） |
| `VOD AIGC - 下载视频` | 按 URL 重新下载视频 |
| `VOD AIGC - 查看执行台账` | 显示 `output/vod_aigc/execution_history.jsonl` 中的历史记录（右下角浮窗） |

所有生成节点运行（成功或失败）都会自动写入执行台账
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

在画布上添加任一生成/查询节点，若凭据未配置会自动弹出设置框——填写
SecretId / SecretKey / SubAppId（单价为选填，用于台账费用预估）→ 保存。
密钥只写入本地配置文件，不进入工作流 JSON。

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
- example 中的 VS 价格为**待校准占位**（人工转写，两个值待复核，见下）
- **旧字段兼容**：`model_price_tables` 缺失或模型名非 VS（H3 等）时，单价自动回退
  `prices[分辨率]`，旧配置无需迁移

**两个待复核点**（VS 价格表人工转写自截图，原图分辨率低，example 已按上表原值落盘）：

1. 国内站 2.0 有参考视频·输出：**1080P = 1.820 与 2K = 1.820 相同**（疑似 2K 应为更高值）
2. 国际站 2.0-mini 有参考视频·输出：**2K = 0.0521 < 1080P = 0.0584**（疑似抄写颠倒）

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
