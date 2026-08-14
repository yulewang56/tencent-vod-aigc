# tencent-vod-aigc

ComfyUI 自定义节点：通过**腾讯云 VOD AIGC** 聚合服务调用 **MiniMax Hailuo H3** 生视频模型。

协议为腾讯云 API v3（TC3-HMAC-SHA256 签名，`CreateAigcVideoTask` / `DescribeTaskDetail`），
对应《VOD AIGC服务接入指南》3.17 节。**纯标准库实现，无需额外 pip 安装**。

## 节点列表（菜单分类：Tencent VOD AIGC）

| 节点 | 功能 |
|---|---|
| `VOD AIGC - H3 文生视频` | 仅提示词生成视频 |
| `VOD AIGC - H3 图生视频（首/尾帧）` | 首帧 / 尾帧 / 首尾帧生视频，支持 ComfyUI IMAGE 或图片 URL |
| `VOD AIGC - H3 多模态参考生视频` | ≤9 图 + ≤3 视频 + ≤3 音频（总数 ≤12），支持本地文件或 URL |
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

配置结构：

```json
{
  "secret_id": "AKIDxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx",
  "secret_key": "xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx",
  "sub_app_id": "1500000000",
  "prices": { "768P": 0.1, "1080P": 0.2, "2K": 0.3, "4K": 0.5 }
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
| 单价（元/秒） | `prices.768P / 1080P / 2K / 4K` |

单价用于台账**费用预估**（不足 5 秒按 5 秒计费），示例值 `0.1/0.2/0.3/0.5` 不是真实价格——
请按《AIGC价格指南（客户）》填写；未配置时显示「¥未配置单价」。配置入口只有两处：节点输入框与配置文件。

## 参数说明（对应文档 3.17）

- **duration**：4–15 秒，计费按生成秒数（不足 5 秒按 5 秒）
- **resolution**：768P / 1080P（超分）/ 2K / 4K（超分），分辨率越高越贵
- **aspect_ratio**：文生/参考生支持 21:9、16:9、4:3、1:1、3:4、9:16；图生视频由输入图决定，此项会被忽略
- **audio_generation**：是否生成原生音频
- **storage_mode**：Temporary（URL 限时有效）/ Permanent（永久存储，可后续做超分增强，推荐生产用）
- **enhance_prompt**：是否启用 H3-Context-IR 提示词增强（未开源、仅 API 的模块）
- **input_region**：素材 URL 在海外时填 `oversea`（避免拉取失败）
- **endpoint**：默认 `vod.tencentcloudapi.com`；如已切换新版网关可填 `gateway.vod-qcloud.com`

## 素材限制（来自文档，超限节点会直接报错）

- 图片：单张 ≤30MB，宽高 [256, 5760]，比例 5:2~2:5
- 参考视频：单段 ≤50MB、2–15 秒、总时长 ≤15 秒
- 参考音频：单段 ≤15MB、2–15 秒，**不能单独输入**，必须配图/视频
- Base64 传参总大小 ≤70MB；混合输入总数 ≤12 个文件
- Prompt ≤7000 字符

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
| `任务失败 (ErrCode=...)` | 查看 message；错误信息均携带 TaskId，可去控制台核对 |
| `无法连接 ... (检查网络/代理)` | 本地网络/代理问题 |
| 生成很慢 | 视频生成需数分钟，轮询间隔默认 10s；错峰可省成本 |

## 测试

```bash
python tests/test_nodes.py        # 70 项自包含测试，无需安装任何依赖（自带 ComfyUI/numpy/PIL stub）
```

## 更新日志

见 [CHANGELOG.md](CHANGELOG.md)。
