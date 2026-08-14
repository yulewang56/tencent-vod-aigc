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

## 安装（git clone）

```bash
cd custom_nodes
git clone https://github.com/yulewang56/tencent-vod-aigc.git
```

重启 ComfyUI（或前端左下角 Restart），右键画布 → 搜索 `VOD AIGC` 即可看到节点。

更新：`cd custom_nodes/tencent-vod-aigc && git pull`

> 也可以下载 zip 解压后放进 `custom_nodes/`（文件夹名随意，不影响加载）。

## 密钥配置（二选一）

**方式 A：节点里直接填**（SecretId / SecretKey / SubAppId 三个输入框）

**方式 B：环境变量**（节点留空即可）
```bash
export TENCENTCLOUD_SECRET_ID="你的SecretId"
export TENCENTCLOUD_SECRET_KEY="你的SecretKey"
export VOD_SUB_APP_ID="1500044236"
```

> 密钥来源：腾讯云控制台 CAM（https://console.cloud.tencent.com/cam/capi）；SubAppId 在云点播控制台「应用管理」获取。

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
| `接口错误 InvalidParameter.VoilationContent` | Prompt 或素材命中内容合规拦截，修改提示词 |
| `任务失败 (ErrCode=...)` | 查看 message；70000 类错误结合 message 判断 |
| `无法连接 ... (检查网络/代理)` | 本地网络/代理问题 |
| `任务成功但未找到输出文件 URL` | 响应结构异常，用「查询任务」节点看 raw_json，截图反馈 |
| 生成很慢 | 视频生成需数分钟，轮询间隔默认 10s；错峰可省成本 |
