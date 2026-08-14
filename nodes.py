"""腾讯云 VOD AIGC（MiniMax Hailuo H3 生视频）ComfyUI 自定义节点。

协议：腾讯云 API v3（TC3-HMAC-SHA256 签名），接口 CreateAigcVideoTask / DescribeTaskDetail。
仅依赖 Python 标准库 + ComfyUI 自带的 numpy/Pillow/torch，无需额外 pip 安装。

对应《VOD AIGC服务接入指南》3.17 节：ModelName=Hailuo, ModelVersion=H3。
"""

import base64
import hashlib
import hmac
import io
import json
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request

import numpy as np
from PIL import Image

# ---------------------------------------------------------------- 常量

SERVICE = "vod"
API_VERSION = "2018-07-17"
DEFAULT_ENDPOINT = "vod.tencentcloudapi.com"
DEFAULT_REGION = "ap-guangzhou"

RESOLUTIONS = ["768P", "1080P", "2K", "4K"]
ASPECT_RATIOS = ["16:9", "9:16", "1:1", "4:3", "3:4", "21:9"]
ON_OFF = ["Enabled", "Disabled"]
STORAGE_MODES = ["Temporary", "Permanent"]

_MAX_IMAGE_BYTES = 30 * 1024 * 1024      # 单张图片 ≤30MB（文档限制）
_MAX_VIDEO_BYTES = 50 * 1024 * 1024      # 单个视频 ≤50MB（文档限制）
_MAX_AUDIO_BYTES = 15 * 1024 * 1024      # 单个音频 ≤15MB（文档限制）
_MAX_BASE64_TOTAL = 70 * 1024 * 1024     # Base64 传参总大小 ≤70MB（文档限制）

# ---------------------------------------------------------------- TC3 签名

def _hmac_sha256(key: bytes, msg: str) -> bytes:
    return hmac.new(key, msg.encode("utf-8"), hashlib.sha256).digest()


def _canonical_headers(headers: dict, action: str) -> str:
    """TC3 规范：canonical headers 中 x-tc-action 的值必须为小写（HTTP 头保持原样）。"""
    parts = []
    for key in sorted(headers.keys()):
        value = action.lower() if key.lower() == "x-tc-action" else str(headers[key]).strip()
        parts.append(f"{key.lower()}:{value}\n")
    return "".join(parts)


def _sign_request(secret_id: str, secret_key: str, region: str, endpoint: str, action: str, payload: dict):
    """构造腾讯云 TC3-HMAC-SHA256 签名，返回 (headers, body_bytes)。"""
    ts = int(time.time())
    date = time.strftime("%Y-%m-%d", time.gmtime(ts))
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")

    headers = {
        "Host": endpoint,
        "Content-Type": "application/json; charset=utf-8",
        "X-TC-Action": action,
        "X-TC-Version": API_VERSION,
        "X-TC-Timestamp": str(ts),
    }
    if region:
        headers["X-TC-Region"] = region

    signed_headers = ";".join(k.lower() for k in sorted(headers.keys()))
    canonical_headers = _canonical_headers(headers, action)

    canonical_request = "\n".join([
        "POST", "/", "",
        canonical_headers,
        signed_headers,
        hashlib.sha256(body).hexdigest(),
    ])
    credential_scope = f"{date}/{SERVICE}/tc3_request"
    string_to_sign = "\n".join([
        "TC3-HMAC-SHA256",
        str(ts),
        credential_scope,
        hashlib.sha256(canonical_request.encode("utf-8")).hexdigest(),
    ])

    secret_date = _hmac_sha256(("TC3" + secret_key).encode("utf-8"), date)
    secret_service = _hmac_sha256(secret_date, SERVICE)
    secret_signing = _hmac_sha256(secret_service, "tc3_request")
    signature = hmac.new(secret_signing, string_to_sign.encode("utf-8"), hashlib.sha256).hexdigest()

    headers["Authorization"] = (
        f"TC3-HMAC-SHA256 Credential={secret_id}/{credential_scope}, "
        f"SignedHeaders={signed_headers}, Signature={signature}"
    )
    return headers, body


def _call_api(secret_id: str, secret_key: str, region: str, endpoint: str, action: str, payload: dict) -> dict:
    """调用腾讯云 VOD 接口，返回 Response 对象；业务错误抛 RuntimeError。"""
    endpoint = (endpoint or DEFAULT_ENDPOINT).strip()
    endpoint = endpoint.replace("https://", "").replace("http://", "").rstrip("/")
    headers, body = _sign_request(secret_id, secret_key, region, endpoint, action, payload)
    req = urllib.request.Request(f"https://{endpoint}/", data=body, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            raw = resp.read().decode("utf-8")
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"HTTP {e.code} — {endpoint} 返回: {e.read().decode('utf-8', 'ignore')[:500]}")
    except urllib.error.URLError as e:
        raise RuntimeError(f"无法连接 {endpoint}: {e.reason}（检查网络/代理）")

    data = json.loads(raw)
    response = data.get("Response", {})
    if response.get("Error"):
        err = response["Error"]
        raise RuntimeError(f"腾讯云 VOD 接口错误 {err.get('Code')}: {err.get('Message')}")
    return response


def _resolve_credentials(secret_id, secret_key, sub_app_id):
    """节点输入优先，回退到环境变量。"""
    sid = (secret_id or "").strip() or os.environ.get("TENCENTCLOUD_SECRET_ID", "").strip()
    skey = (secret_key or "").strip() or os.environ.get("TENCENTCLOUD_SECRET_KEY", "").strip()
    sub = (sub_app_id or "").strip() or os.environ.get("VOD_SUB_APP_ID", "").strip()
    if not sid or not skey:
        raise ValueError("缺少腾讯云密钥：请在节点填写 SecretId / SecretKey，或设置环境变量 TENCENTCLOUD_SECRET_ID / TENCENTCLOUD_SECRET_KEY")
    if not sub:
        raise ValueError("缺少 SubAppId：请在节点填写，或设置环境变量 VOD_SUB_APP_ID")
    if not sub.isdigit():
        raise ValueError(f"SubAppId 必须为纯数字，当前值: {sub}")
    return sid, skey, sub


# ---------------------------------------------------------------- 工具函数

def _set_status(node, text: str):
    """向前端显示节点运行状态（旧版本忽略）。"""
    try:
        node.display_string = text
    except Exception:
        pass


def _image_tensor_to_base64(image_tensor, frame_index: int = 0) -> str:
    """ComfyUI IMAGE tensor（B,H,W,C float 0-1）→ PNG Base64。"""
    img = image_tensor[frame_index].cpu().numpy()
    img = np.clip(img * 255.0, 0, 255).astype(np.uint8)
    buf = io.BytesIO()
    Image.fromarray(img).save(buf, format="PNG")
    data = buf.getvalue()
    if len(data) > _MAX_IMAGE_BYTES:
        raise ValueError(f"图片超过 30MB 上限，请压缩后再试（{len(data) // (1024*1024)}MB）")
    return base64.b64encode(data).decode("ascii")


def _file_to_base64(path: str, max_bytes: int, what: str) -> str:
    if not os.path.isfile(path):
        raise ValueError(f"文件不存在: {path}")
    data = open(path, "rb").read()
    if len(data) > max_bytes:
        raise ValueError(f"{what} 超过 {max_bytes // (1024*1024)}MB 上限: {path}")
    return base64.b64encode(data).decode("ascii")


def _parse_multiline(text):
    """多行 STRING 输入 → 去空行列表。"""
    if not text:
        return []
    return [line.strip() for line in text.splitlines() if line.strip()]


def _extract_task_result(detail: dict):
    """从 DescribeTaskDetail 响应中提取 AIGC 任务状态与输出文件 URL（只取 Output 子树）。"""
    task_dict = None
    for key, value in detail.items():
        if isinstance(value, dict) and re.search(r"(Aigc|SceneAigc)\w*Task$", key):
            task_dict = value
            break
    if task_dict is None:
        task_dict = detail

    status = task_dict.get("Status") or detail.get("Status")
    err_code = task_dict.get("ErrCode")
    message = task_dict.get("Message")

    urls = []

    def _walk(node):
        if isinstance(node, dict):
            if isinstance(node.get("FileUrl"), str):
                urls.append(node["FileUrl"])
            for v in node.values():
                _walk(v)
        elif isinstance(node, list):
            for item in node:
                _walk(item)

    _walk(task_dict.get("Output") or {})
    return (status or "").upper(), err_code, message, urls


def _wait_for_task(secret_id, secret_key, region, endpoint, sub_app_id, task_id,
                   poll_interval, timeout, on_progress=None) -> dict:
    """轮询 DescribeTaskDetail 直到任务完成，返回 {"status", "urls", "detail"}。"""
    deadline = time.time() + timeout
    started = time.time()
    while True:
        response = _call_api(secret_id, secret_key, region, endpoint, "DescribeTaskDetail",
                             {"SubAppId": int(sub_app_id), "TaskId": task_id})
        detail = response.get("TaskDetail") or {}
        status, err_code, message, urls = _extract_task_result(detail)
        if status in ("SUCCESS", "FINISH"):
            if not urls:
                raise RuntimeError(f"任务成功但未找到输出文件 URL（原始响应: {json.dumps(detail, ensure_ascii=False)[:400]}）")
            return {"status": status, "urls": urls, "detail": detail}
        if status in ("FAIL", "FAILED", "ERROR"):
            raise RuntimeError(f"H3 任务失败 (ErrCode={err_code}): {message or '未知错误'}（TaskId: {task_id}）")
        if time.time() > deadline:
            raise RuntimeError(f"任务超时（{timeout}s 未完成）。TaskId: {task_id}，可用「VOD AIGC - 查询任务」节点手动查询")
        elapsed = int(time.time() - started)
        if on_progress:
            on_progress(f"H3 生成中… {elapsed}s | TaskId: {task_id[-16:]}")
        time.sleep(max(1, int(poll_interval)))


def _download_video(url: str, task_id: str) -> str:
    """把生成的视频下载到 ComfyUI output/vod_aigc/ 目录。"""
    from comfy import folder_paths

    out_dir = os.path.join(folder_paths.get_output_directory(), "vod_aigc")
    os.makedirs(out_dir, exist_ok=True)
    original = os.path.basename(urllib.parse.urlparse(url).path) or "aigcVideoGenFile.mp4"
    name = f"{task_id[-8:]}_{original}"
    path = os.path.join(out_dir, name)
    urllib.request.urlretrieve(url, path)
    return path


# ---------------------------------------------------------------- 输入模板

def _cred_inputs():
    return {
        "secret_id": ("STRING", {"default": "", "tooltip": "腾讯云 CAM SecretId（留空则读环境变量 TENCENTCLOUD_SECRET_ID）"}),
        "secret_key": ("STRING", {"default": "", "tooltip": "腾讯云 CAM SecretKey（留空则读环境变量 TENCENTCLOUD_SECRET_KEY）"}),
        "sub_app_id": ("STRING", {"default": "", "tooltip": "VOD 应用 ID（留空则读环境变量 VOD_SUB_APP_ID）"}),
    }


def _output_config_inputs():
    return {
        "duration": ("INT", {"default": 5, "min": 4, "max": 15, "step": 1, "tooltip": "生成时长（秒），范围 4-15"}),
        "resolution": (RESOLUTIONS, {"default": "1080P", "tooltip": "768P / 1080P(超分) / 2K / 4K(超分)，越高越贵"}),
        "aspect_ratio": (ASPECT_RATIOS, {"default": "16:9"}),
        "audio_generation": (ON_OFF, {"default": "Enabled", "tooltip": "是否生成音频"}),
        "storage_mode": (STORAGE_MODES, {"default": "Temporary", "tooltip": "Temporary=临时存储(URL 限时有效) / Permanent=永久存储(可后续超分处理)"}),
        "enhance_prompt": (ON_OFF, {"default": "Disabled", "tooltip": "是否启用提示词增强（H3-Context-IR）"}),
        "media_name": ("STRING", {"default": "", "tooltip": "可选，输出文件名/备注"}),
        "region": ("STRING", {"default": DEFAULT_REGION, "tooltip": "腾讯云地域，如 ap-guangzhou"}),
        "endpoint": ("STRING", {"default": "", "tooltip": "API 地址，留空用 vod.tencentcloudapi.com（新版可用 gateway.vod-qcloud.com）"}),
        "input_region": ("STRING", {"default": "", "tooltip": "可选 InputRegion，素材在海外时填 oversea"}),
        "poll_interval": ("INT", {"default": 10, "min": 3, "max": 120, "step": 1, "tooltip": "任务轮询间隔（秒）"}),
        "timeout": ("INT", {"default": 1800, "min": 60, "max": 7200, "step": 60, "tooltip": "任务超时时间（秒），视频生成通常需数分钟"}),
    }


def _build_payload(sub_app_id, prompt, enhance_prompt, oc_values, file_infos=None, input_region=""):
    """构造 CreateAigcVideoTask 请求体（Hailuo / H3）。"""
    payload = {
        "SubAppId": int(sub_app_id),
        "ModelName": "Hailuo",
        "ModelVersion": "H3",
        "Prompt": prompt,
        "EnhancePrompt": enhance_prompt,
        "OutputConfig": {
            "StorageMode": oc_values["storage_mode"],
            "Duration": int(oc_values["duration"]),
            "Resolution": oc_values["resolution"],
            "AspectRatio": oc_values["aspect_ratio"],
            "AudioGeneration": oc_values["audio_generation"],
        },
    }
    if oc_values.get("media_name"):
        payload["OutputConfig"]["MediaName"] = oc_values["media_name"]
    if file_infos:
        payload["FileInfos"] = file_infos
    if input_region:
        payload["InputRegion"] = input_region
    return payload


# ---------------------------------------------------------------- 节点类

class TencentVODH3TextToVideo:
    """文生视频：仅提示词，无素材输入。"""

    @classmethod
    def INPUT_TYPES(cls):
        required = dict(_cred_inputs())
        required["prompt"] = ("STRING", {"multiline": True, "default": "", "tooltip": "提示词，上限 7000 字符"})
        return {"required": required, "optional": _output_config_inputs()}

    RETURN_TYPES = ("STRING", "STRING", "STRING")
    RETURN_NAMES = ("task_id", "video_url", "video_path")
    FUNCTION = "generate"
    CATEGORY = "Tencent VOD AIGC"
    OUTPUT_NODE = True

    @classmethod
    def IS_CHANGED(cls, **kwargs):
        return float("NaN")  # 每次都提交新任务，不缓存

    def generate(self, prompt, **kwargs):
        if not prompt.strip():
            raise ValueError("Prompt 不能为空")
        secret_id, secret_key, sub_app_id = _resolve_credentials(
            kwargs.get("secret_id"), kwargs.get("secret_key"), kwargs.get("sub_app_id"))
        region = kwargs.get("region") or ""
        endpoint = kwargs.get("endpoint") or ""
        payload = _build_payload(sub_app_id, prompt, kwargs.get("enhance_prompt", "Disabled"),
                                 kwargs, input_region=kwargs.get("input_region") or "")
        _set_status(self, "提交 H3 文生视频任务…")
        response = _call_api(secret_id, secret_key, region, endpoint, "CreateAigcVideoTask", payload)
        task_id = response.get("TaskId")
        if not task_id:
            raise RuntimeError(f"未返回 TaskId（原始响应: {json.dumps(response, ensure_ascii=False)[:400]}）")
        result = _wait_for_task(secret_id, secret_key, region, endpoint, sub_app_id, task_id,
                                kwargs.get("poll_interval", 10), kwargs.get("timeout", 1800),
                                on_progress=lambda t: _set_status(self, t))
        url = result["urls"][0]
        _set_status(self, "下载视频…")
        path = _download_video(url, task_id)
        _set_status(self, "完成")
        return (task_id, url, path)


class TencentVODH3ImageToVideo:
    """图生视频：首帧 / 尾帧 / 首尾帧。支持 IMAGE tensor 或图片 URL。"""

    @classmethod
    def INPUT_TYPES(cls):
        required = dict(_cred_inputs())
        required["prompt"] = ("STRING", {"multiline": True, "default": "", "tooltip": "提示词，上限 7000 字符"})
        optional = _output_config_inputs()
        optional["first_frame"] = ("IMAGE", {"tooltip": "首帧图（ComfyUI 图像，转 Base64 上传）"})
        optional["last_frame"] = ("IMAGE", {"tooltip": "尾帧图"})
        optional["first_frame_url"] = ("STRING", {"default": "", "tooltip": "首帧图 URL（可访问的公网地址，与 first_frame 二选一）"})
        optional["last_frame_url"] = ("STRING", {"default": "", "tooltip": "尾帧图 URL"})
        return {"required": required, "optional": optional}

    RETURN_TYPES = ("STRING", "STRING", "STRING")
    RETURN_NAMES = ("task_id", "video_url", "video_path")
    FUNCTION = "generate"
    CATEGORY = "Tencent VOD AIGC"
    OUTPUT_NODE = True

    @classmethod
    def IS_CHANGED(cls, **kwargs):
        return float("NaN")

    def generate(self, prompt, **kwargs):
        if not prompt.strip():
            raise ValueError("Prompt 不能为空")
        file_infos = []
        base64_total = 0

        for key, usage in (("first_frame", "FirstFrame"), ("last_frame", "LastFrame")):
            tensor = kwargs.get(key)
            url = (kwargs.get(f"{key}_url") or "").strip()
            if tensor is not None and url:
                raise ValueError(f"{usage} 同时提供了 IMAGE 和 URL，请只保留一种")
            if tensor is not None:
                data = _image_tensor_to_base64(tensor, 0 if key == "first_frame" else -1)
                base64_total += len(data)
                if base64_total > _MAX_BASE64_TOTAL:
                    raise ValueError("Base64 素材总大小超过 70MB 上限")
                file_infos.append({"Type": "Base64", "Category": "Image", "Base64": data, "Usage": usage})
            elif url:
                file_infos.append({"Type": "Url", "Category": "Image", "Url": url, "Usage": usage})

        if not file_infos:
            raise ValueError("至少提供一张首帧/尾帧图（IMAGE 或 URL）")

        secret_id, secret_key, sub_app_id = _resolve_credentials(
            kwargs.get("secret_id"), kwargs.get("secret_key"), kwargs.get("sub_app_id"))
        region = kwargs.get("region") or ""
        endpoint = kwargs.get("endpoint") or ""
        payload = _build_payload(sub_app_id, prompt, kwargs.get("enhance_prompt", "Disabled"),
                                 kwargs, file_infos=file_infos, input_region=kwargs.get("input_region") or "")
        _set_status(self, "提交 H3 图生视频任务…")
        response = _call_api(secret_id, secret_key, region, endpoint, "CreateAigcVideoTask", payload)
        task_id = response.get("TaskId")
        if not task_id:
            raise RuntimeError(f"未返回 TaskId（原始响应: {json.dumps(response, ensure_ascii=False)[:400]}）")
        result = _wait_for_task(secret_id, secret_key, region, endpoint, sub_app_id, task_id,
                                kwargs.get("poll_interval", 10), kwargs.get("timeout", 1800),
                                on_progress=lambda t: _set_status(self, t))
        url = result["urls"][0]
        _set_status(self, "下载视频…")
        path = _download_video(url, task_id)
        _set_status(self, "完成")
        return (task_id, url, path)


class TencentVODH3ReferenceToVideo:
    """多模态参考生视频：最多 9 图 + 3 视频 + 3 音频（总数 ≤12），音频不能单独输入。"""

    @classmethod
    def INPUT_TYPES(cls):
        required = dict(_cred_inputs())
        required["prompt"] = ("STRING", {"multiline": True, "default": "", "tooltip": "提示词；多图时可用「图1…图2…」描述"})
        optional = _output_config_inputs()
        optional["ref_images"] = ("IMAGE", {"tooltip": "参考图（每帧一张，最多 9 张）"})
        optional["ref_image_urls"] = ("STRING", {"multiline": True, "default": "", "tooltip": "参考图 URL，每行一个，最多 9 个"})
        optional["ref_video_paths"] = ("STRING", {"multiline": True, "default": "", "tooltip": "本地参考视频路径，每行一个（2-15 秒/段，最多 3 段，共 ≤15 秒）"})
        optional["ref_video_urls"] = ("STRING", {"multiline": True, "default": "", "tooltip": "参考视频 URL，每行一个"})
        optional["ref_audio_paths"] = ("STRING", {"multiline": True, "default": "", "tooltip": "本地参考音频路径，每行一个（2-15 秒/段，最多 3 段，不能单独输入）"})
        optional["ref_audio_urls"] = ("STRING", {"multiline": True, "default": "", "tooltip": "参考音频 URL，每行一个"})
        return {"required": required, "optional": optional}

    RETURN_TYPES = ("STRING", "STRING", "STRING")
    RETURN_NAMES = ("task_id", "video_url", "video_path")
    FUNCTION = "generate"
    CATEGORY = "Tencent VOD AIGC"
    OUTPUT_NODE = True

    @classmethod
    def IS_CHANGED(cls, **kwargs):
        return float("NaN")

    def generate(self, prompt, **kwargs):
        if not prompt.strip():
            raise ValueError("Prompt 不能为空")

        file_infos = []
        base64_total = 0

        # 参考图：IMAGE tensor（每帧一张）
        ref_images = kwargs.get("ref_images")
        if ref_images is not None:
            n = ref_images.shape[0]
            if n > 9:
                raise ValueError(f"参考图最多 9 张，当前 IMAGE 有 {n} 帧")
            for i in range(n):
                data = _image_tensor_to_base64(ref_images, i)
                base64_total += len(data)
                file_infos.append({"Type": "Base64", "Category": "Image", "Base64": data, "Usage": "Reference"})

        # 参考图：URL
        for url in _parse_multiline(kwargs.get("ref_image_urls")):
            file_infos.append({"Type": "Url", "Category": "Image", "Url": url, "Usage": "Reference"})

        # 参考视频
        for path in _parse_multiline(kwargs.get("ref_video_paths")):
            data = _file_to_base64(path, _MAX_VIDEO_BYTES, "参考视频")
            base64_total += len(data)
            file_infos.append({"Type": "Base64", "Category": "Video", "Base64": data, "Usage": "Reference"})
        for url in _parse_multiline(kwargs.get("ref_video_urls")):
            file_infos.append({"Type": "Url", "Category": "Video", "Url": url, "Usage": "Reference"})

        # 参考音频
        for path in _parse_multiline(kwargs.get("ref_audio_paths")):
            data = _file_to_base64(path, _MAX_AUDIO_BYTES, "参考音频")
            base64_total += len(data)
            file_infos.append({"Type": "Base64", "Category": "Audio", "Base64": data, "Usage": "Reference"})
        for url in _parse_multiline(kwargs.get("ref_audio_urls")):
            file_infos.append({"Type": "Url", "Category": "Audio", "Url": url, "Usage": "Reference"})

        if not file_infos:
            raise ValueError("参考生视频至少需要一个素材（图/视频/音频）")

        n_images = sum(1 for f in file_infos if f["Category"] == "Image")
        n_videos = sum(1 for f in file_infos if f["Category"] == "Video")
        n_audios = sum(1 for f in file_infos if f["Category"] == "Audio")
        if n_images > 9:
            raise ValueError(f"参考图最多 9 张，当前 {n_images} 张")
        if n_videos > 3:
            raise ValueError(f"参考视频最多 3 段，当前 {n_videos} 段")
        if n_audios > 3:
            raise ValueError(f"参考音频最多 3 段，当前 {n_audios} 段")
        if n_images + n_videos + n_audios > 12:
            raise ValueError(f"混合素材总数上限 12 个，当前 {n_images + n_videos + n_audios} 个")
        if n_audios > 0 and n_images == 0 and n_videos == 0:
            raise ValueError("音频不能单独作为参考输入，必须配图片或视频")
        if base64_total > _MAX_BASE64_TOTAL:
            raise ValueError("Base64 素材总大小超过 70MB 上限")

        secret_id, secret_key, sub_app_id = _resolve_credentials(
            kwargs.get("secret_id"), kwargs.get("secret_key"), kwargs.get("sub_app_id"))
        region = kwargs.get("region") or ""
        endpoint = kwargs.get("endpoint") or ""
        payload = _build_payload(sub_app_id, prompt, kwargs.get("enhance_prompt", "Disabled"),
                                 kwargs, file_infos=file_infos, input_region=kwargs.get("input_region") or "")
        _set_status(self, "提交 H3 参考生视频任务…")
        response = _call_api(secret_id, secret_key, region, endpoint, "CreateAigcVideoTask", payload)
        task_id = response.get("TaskId")
        if not task_id:
            raise RuntimeError(f"未返回 TaskId（原始响应: {json.dumps(response, ensure_ascii=False)[:400]}）")
        result = _wait_for_task(secret_id, secret_key, region, endpoint, sub_app_id, task_id,
                                kwargs.get("poll_interval", 10), kwargs.get("timeout", 1800),
                                on_progress=lambda t: _set_status(self, t))
        url = result["urls"][0]
        _set_status(self, "下载视频…")
        path = _download_video(url, task_id)
        _set_status(self, "完成")
        return (task_id, url, path)


class TencentVODAIGCQueryTask:
    """查询任务状态：输入 TaskId，输出状态与输出文件 URL（任务超时/失败排查用）。"""

    @classmethod
    def INPUT_TYPES(cls):
        required = dict(_cred_inputs())
        required["task_id"] = ("STRING", {"default": "", "tooltip": "CreateAigcVideoTask 返回的 TaskId"})
        return {"required": required,
                "optional": {"region": ("STRING", {"default": DEFAULT_REGION}),
                             "endpoint": ("STRING", {"default": ""})}}

    RETURN_TYPES = ("STRING", "STRING", "STRING")
    RETURN_NAMES = ("status", "video_urls", "raw_json")
    FUNCTION = "query"
    CATEGORY = "Tencent VOD AIGC"
    OUTPUT_NODE = True

    @classmethod
    def IS_CHANGED(cls, **kwargs):
        return float("NaN")

    def query(self, task_id, **kwargs):
        if not task_id.strip():
            raise ValueError("TaskId 不能为空")
        secret_id, secret_key, sub_app_id = _resolve_credentials(
            kwargs.get("secret_id"), kwargs.get("secret_key"), kwargs.get("sub_app_id"))
        region = kwargs.get("region") or ""
        endpoint = kwargs.get("endpoint") or ""
        response = _call_api(secret_id, secret_key, region, endpoint, "DescribeTaskDetail",
                             {"SubAppId": int(sub_app_id), "TaskId": task_id.strip()})
        detail = response.get("TaskDetail") or {}
        status, _, _, urls = _extract_task_result(detail)
        return (status or "UNKNOWN", "\n".join(urls), json.dumps(detail, ensure_ascii=False, indent=2))


class TencentVODAIGCDownloadVideo:
    """按 URL 下载视频到 ComfyUI output/vod_aigc/（可对已完成的旧任务重新下载）。"""

    @classmethod
    def INPUT_TYPES(cls):
        return {"required": {
            "url": ("STRING", {"default": ""}),
            "filename": ("STRING", {"default": "", "tooltip": "可选，输出文件名（不含扩展名）"}),
        }}

    RETURN_TYPES = ("STRING",)
    RETURN_NAMES = ("file_path",)
    FUNCTION = "download"
    CATEGORY = "Tencent VOD AIGC"
    OUTPUT_NODE = True

    @classmethod
    def IS_CHANGED(cls, **kwargs):
        return float("NaN")

    def download(self, url, filename=""):
        if not url.strip():
            raise ValueError("URL 不能为空")
        name_hint = filename.strip() or None
        if name_hint:
            original = urllib.parse.unquote(urllib.parse.urlparse(url.strip()).path.split("/")[-1])
            ext = os.path.splitext(original)[1] or ".mp4"
            if not name_hint.lower().endswith((".mp4", ".mov", ".webm")):
                name_hint += ext
        from comfy import folder_paths
        out_dir = os.path.join(folder_paths.get_output_directory(), "vod_aigc")
        os.makedirs(out_dir, exist_ok=True)
        name = name_hint or os.path.basename(urllib.parse.urlparse(url.strip()).path) or "aigcVideoGenFile.mp4"
        path = os.path.join(out_dir, name)
        _set_status(self, "下载中…")
        urllib.request.urlretrieve(url.strip(), path)
        _set_status(self, "完成")
        return (path,)


NODE_CLASS_MAPPINGS = {
    "TencentVODH3TextToVideo": TencentVODH3TextToVideo,
    "TencentVODH3ImageToVideo": TencentVODH3ImageToVideo,
    "TencentVODH3ReferenceToVideo": TencentVODH3ReferenceToVideo,
    "TencentVODAIGCQueryTask": TencentVODAIGCQueryTask,
    "TencentVODAIGCDownloadVideo": TencentVODAIGCDownloadVideo,
}

NODE_DISPLAY_NAME_MAPPINGS = {
    "TencentVODH3TextToVideo": "VOD AIGC - H3 文生视频",
    "TencentVODH3ImageToVideo": "VOD AIGC - H3 图生视频（首/尾帧）",
    "TencentVODH3ReferenceToVideo": "VOD AIGC - H3 多模态参考生视频",
    "TencentVODAIGCQueryTask": "VOD AIGC - 查询任务",
    "TencentVODAIGCDownloadVideo": "VOD AIGC - 下载视频",
}
