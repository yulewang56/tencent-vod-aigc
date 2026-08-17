"""腾讯云 VOD AIGC（MiniMax Hailuo H3 生视频）ComfyUI 自定义节点。

协议：腾讯云 API v3（TC3-HMAC-SHA256 签名），接口 CreateAigcVideoTask / DescribeTaskDetail。
仅依赖 Python 标准库 + ComfyUI 自带的 numpy/Pillow/torch，无需额外 pip 安装。

对应《VOD AIGC服务接入指南》3.17 节：ModelName=Hailuo, ModelVersion=H3。
"""

import base64
import functools
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

# folder_paths 在不同 ComfyUI 版本位置不同：经典版是仓库根目录的顶层模块，新版在 comfy 包内
try:
    from comfy import folder_paths
except ImportError:
    import folder_paths

# ---------------------------------------------------------------- 常量

SERVICE = "vod"
API_VERSION = "2018-07-17"
DEFAULT_ENDPOINT = "vod.tencentcloudapi.com"
DEFAULT_REGION = "ap-guangzhou"

# MPS（媒体处理）AIGC 音乐生成：CreateAigcAudioTask / DescribeAigcAudioTask
MPS_SERVICE = "mps"
MPS_API_VERSION = "2019-06-12"
MPS_ENDPOINT = "mps.tencentcloudapi.com"

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


def _sign_request(secret_id: str, secret_key: str, region: str, endpoint: str, action: str, payload: dict,
                  version=API_VERSION, service=SERVICE):
    """构造腾讯云 TC3-HMAC-SHA256 签名，返回 (headers, body_bytes)。

    version / service 供 MPS 等其它产品使用（默认 VOD 的 2018-07-17 / vod）。
    """
    ts = int(time.time())
    date = time.strftime("%Y-%m-%d", time.gmtime(ts))
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")

    headers = {
        "Host": endpoint,
        "Content-Type": "application/json; charset=utf-8",
        "X-TC-Action": action,
        "X-TC-Version": version,
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
    credential_scope = f"{date}/{service}/tc3_request"
    string_to_sign = "\n".join([
        "TC3-HMAC-SHA256",
        str(ts),
        credential_scope,
        hashlib.sha256(canonical_request.encode("utf-8")).hexdigest(),
    ])

    secret_date = _hmac_sha256(("TC3" + secret_key).encode("utf-8"), date)
    secret_service = _hmac_sha256(secret_date, service)
    secret_signing = _hmac_sha256(secret_service, "tc3_request")
    signature = hmac.new(secret_signing, string_to_sign.encode("utf-8"), hashlib.sha256).hexdigest()

    headers["Authorization"] = (
        f"TC3-HMAC-SHA256 Credential={secret_id}/{credential_scope}, "
        f"SignedHeaders={signed_headers}, Signature={signature}"
    )
    return headers, body


def _call_api(secret_id: str, secret_key: str, region: str, endpoint: str, action: str, payload: dict,
              version=API_VERSION, service=SERVICE) -> dict:
    """调用腾讯云接口，返回 Response 对象；业务错误抛 RuntimeError。"""
    endpoint = (endpoint or DEFAULT_ENDPOINT).strip()
    endpoint = endpoint.replace("https://", "").replace("http://", "").rstrip("/")
    headers, body = _sign_request(secret_id, secret_key, region, endpoint, action, payload,
                                  version=version, service=service)
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
        raise RuntimeError(f"腾讯云接口错误 {err.get('Code')}: {err.get('Message')}")
    return response


_CONFIG_FILE = "tencent-vod-config.json"
_CRED_FILE_HINT = ("custom_nodes/tencent-vod-aigc/tencent-vod-config.json"
                   "（模板见同目录 tencent-vod-config.example.json）")


def _load_config_file(dir_path=None):
    """读取统一配置文件 tencent-vod-config.json。

    返回 {"secret_id", "secret_key", "sub_app_id", "prices": {分辨率: 单价}}，
    文件缺失/损坏返回空结构。
    """
    base = dir_path or os.path.dirname(os.path.abspath(__file__))
    try:
        with open(os.path.join(base, _CONFIG_FILE), "r", encoding="utf-8") as f:
            data = json.load(f)
    except FileNotFoundError:
        data = {}
    except Exception as e:
        print(f"[tencent-vod-aigc] {_CONFIG_FILE} 读取失败（忽略）: {e}")
        data = {}
    result = {k: str(data.get(k) or "").strip() for k in ("secret_id", "secret_key", "sub_app_id")}
    result["prices"] = {}
    prices = data.get("prices")
    if isinstance(prices, dict):
        for k, v in prices.items():
            try:
                result["prices"][k] = float(v)
            except (TypeError, ValueError):
                pass
    result["image_prices"] = {}
    img_prices = data.get("image_prices")
    if isinstance(img_prices, dict):
        for k, v in img_prices.items():
            try:
                result["image_prices"][k] = float(v)
            except (TypeError, ValueError):
                pass
    return result


def _resolve_credentials(secret_id, secret_key, sub_app_id):
    """凭据解析优先级：节点输入 > tencent-vod-config.json。"""
    file_creds = _load_config_file()
    sid = (secret_id or "").strip() or file_creds.get("secret_id")
    skey = (secret_key or "").strip() or file_creds.get("secret_key")
    sub = (sub_app_id or "").strip() or file_creds.get("sub_app_id")
    if not sid or not skey:
        raise ValueError("缺少腾讯云密钥：请在节点填写 SecretId / SecretKey，或配置 "
                         + _CRED_FILE_HINT)
    if not sub:
        raise ValueError("缺少 SubAppId：请在节点填写，或配置 "
                         + _CRED_FILE_HINT)
    if not sub.isdigit():
        raise ValueError(f"SubAppId 必须为纯数字，当前值: {sub}")
    return sid, skey, sub


def _resolve_secret_pair(secret_id, secret_key):
    """凭据解析（仅密钥对）：供 MPS 等不使用 SubAppId 的服务（如音乐生成）。"""
    file_creds = _load_config_file()
    sid = (secret_id or "").strip() or file_creds.get("secret_id")
    skey = (secret_key or "").strip() or file_creds.get("secret_key")
    if not sid or not skey:
        raise ValueError("缺少腾讯云密钥：请在节点填写 SecretId / SecretKey，或配置 "
                         + _CRED_FILE_HINT)
    return sid, skey


def _credentials_configured() -> bool:
    """凭据是否已配置（tencent-vod-config.json 三项齐全），供前端弹窗判断。"""
    file_creds = _load_config_file()
    return bool(file_creds.get("secret_id") and file_creds.get("secret_key") and file_creds.get("sub_app_id"))


def _save_config_file(secret_id, secret_key, sub_app_id, prices=None, image_prices=None, path=None) -> str:
    """校验并写入统一配置文件（凭据必填；单价选填，仅合并非空数值项），返回文件路径。

    prices = 视频单价（元/秒，按分辨率）；image_prices = 生图单价（元/张，按模型）。
    """
    sid, skey, sub = (secret_id or "").strip(), (secret_key or "").strip(), (sub_app_id or "").strip()
    if not sid or not skey:
        raise ValueError("SecretId 与 SecretKey 不能为空")
    if not sub.isdigit():
        raise ValueError(f"SubAppId 必须为纯数字，当前值: {sub}")
    path = path or os.path.join(os.path.dirname(os.path.abspath(__file__)), _CONFIG_FILE)
    existing = _load_config_file(os.path.dirname(path)) if os.path.exists(path) else {}

    merged = dict(existing.get("prices", {}))
    for res, val in (prices or {}).items():
        if val is None or val == "":
            continue
        try:
            merged[str(res)] = float(val)
        except (TypeError, ValueError):
            raise ValueError(f"视频单价 {res} 必须是数字，当前值: {val}")

    merged_img = dict(existing.get("image_prices", {}))
    for model, val in (image_prices or {}).items():
        if val is None or val == "":
            continue
        try:
            merged_img[str(model)] = float(val)
        except (TypeError, ValueError):
            raise ValueError(f"生图单价 {model} 必须是数字，当前值: {val}")

    with open(path, "w", encoding="utf-8") as f:
        json.dump({"secret_id": sid, "secret_key": skey, "sub_app_id": sub,
                   "prices": merged, "image_prices": merged_img},
                  f, indent=2, ensure_ascii=False)
    return path


def _register_http_routes():
    """注册配置状态查询 / 保存接口，供前端首次使用弹窗调用（非 ComfyUI 环境自动跳过）。"""
    try:
        from aiohttp import web
        from server import PromptServer
        routes = PromptServer.instance.routes  # ComfyUI 进程外导入（如脚本）时 instance 不存在
    except Exception:
        return

    async def config_status(_request):
        cfg = _load_config_file()
        return web.json_response({"configured": _credentials_configured(),
                                  "prices": cfg.get("prices", {}),
                                  "image_prices": cfg.get("image_prices", {})})

    async def config_save(request):
        try:
            body = await request.json()
        except Exception:
            return web.json_response({"ok": False, "error": "请求体不是合法 JSON"}, status=400)
        try:
            path = _save_config_file(body.get("secret_id", ""), body.get("secret_key", ""),
                                     body.get("sub_app_id", ""), body.get("prices"),
                                     body.get("image_prices"))
        except ValueError as e:
            return web.json_response({"ok": False, "error": str(e)}, status=400)
        return web.json_response({"ok": True, "path": path})

    routes.get("/tencent-vod-aigc/config")(config_status)
    routes.post("/tencent-vod-aigc/config")(config_save)


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


def _resolve_media_path(path: str) -> str:
    """素材路径解析：~ 展开 + 绝对路径原样；input/、output/ 前缀解析到 ComfyUI 对应目录；其余按进程工作目录。"""
    p = (path or "").strip()
    if not p:
        return p
    p = os.path.expanduser(p)  # 支持 ~/Downloads/xxx 这类 shell 习惯写法
    if os.path.isabs(p):
        return p
    for prefix, getter in (("input/", folder_paths.get_input_directory),
                           ("output/", folder_paths.get_output_directory)):
        if p.startswith(prefix):
            return os.path.join(getter(), p[len(prefix):])
    return p  # 兼容旧行为：相对进程 cwd


def _file_to_base64(path: str, max_bytes: int, what: str, allowed_exts=None) -> str:
    resolved = _resolve_media_path(path)
    if not os.path.isfile(resolved):
        raise ValueError(f"文件不存在: {path}（支持 ~/、input/xxx、output/xxx 或绝对路径）")
    if allowed_exts:
        ext = os.path.splitext(resolved)[1].lower()
        if ext and ext not in allowed_exts:
            raise ValueError(f"{what} 扩展名 \"{ext}\" 不支持，允许: {', '.join(allowed_exts)}（路径: {path[:80]}）")
    data = open(resolved, "rb").read()
    if len(data) > max_bytes:
        raise ValueError(f"{what} 超过 {max_bytes // (1024*1024)}MB 上限: {path}")
    return base64.b64encode(data).decode("ascii")


_ALLOWED_VIDEO_EXTS = (".mp4", ".mov")
_ALLOWED_IMAGE_EXTS = (".jpg", ".jpeg", ".png", ".webp", ".bmp")
_ALLOWED_AUDIO_EXTS = (".mp3", ".wav", ".m4a", ".aac")


def _validate_media_url(url: str, allowed: tuple, what: str):
    """提交前校验素材 URL 扩展名：明确不在允许列表时本地报错（避免任务跑完才失败）。

    无扩展名/无法解析的 URL 不拦截（交由服务端判断）。
    """
    path = urllib.parse.urlparse(url.strip()).path
    ext = os.path.splitext(path)[1].lower()
    if ext and ext not in allowed:
        raise ValueError(f"{what} 扩展名 \"{ext}\" 不支持，允许: {', '.join(allowed)}（URL: {url[:80]}）")


def _parse_multiline(text):
    """多行 STRING 输入 → 去空行列表。"""
    if not text:
        return []
    return [line.strip() for line in text.splitlines() if line.strip()]


class TaskError(RuntimeError):
    """任务级错误：携带 task_id，供台账记录与后续排查。"""

    def __init__(self, task_id, message):
        super().__init__(message)
        self.task_id = task_id


def _extract_task_result(detail: dict):
    """从任务查询响应中提取 AIGC 任务状态、错误信息与输出文件 URL。

    兼容两种结构：
    - VOD DescribeTaskDetail：详情嵌在 AigcVideoTask / AigcImageTask 子对象（正则 Aigc|SceneAigc\\w*Task），
      输出在 Output.FileInfos[].FileUrl（Input 子树必须忽略）；
    - MPS DescribeAigcAudioTask：状态与 AudioInfos[].Url 平铺在顶层（无 Task 子对象）。
    """
    task_dict = None
    for key, value in detail.items():
        if isinstance(value, dict) and re.search(r"(Aigc|SceneAigc)\w*Task$", key):
            task_dict = value
            break
    if task_dict is None:
        task_dict = detail

    status = task_dict.get("Status") or detail.get("Status")
    err_code = task_dict.get("ErrCode")
    err_code_ext = task_dict.get("ErrCodeExt")
    message = task_dict.get("Message")

    urls = []

    def _walk(node):
        if isinstance(node, dict):
            if isinstance(node.get("FileUrl"), str):
                urls.append(node["FileUrl"])
            if isinstance(node.get("Url"), str):
                urls.append(node["Url"])
            for v in node.values():
                _walk(v)
        elif isinstance(node, list):
            for item in node:
                _walk(item)

    output = task_dict.get("Output")
    if output:
        _walk(output)      # VOD 结构：只走 Output 子树，忽略 Input
    else:
        _walk(task_dict)   # MPS 平铺结构：AudioInfos[].Url 在顶层
    return (status or "").upper(), err_code, err_code_ext, message, urls


def _wait_for_task(secret_id, secret_key, region, endpoint, sub_app_id, task_id,
                   poll_interval, timeout, on_progress=None, task_label="H3 生成中",
                   action="DescribeTaskDetail", err_label="H3",
                   version=API_VERSION, service=SERVICE) -> dict:
    """轮询任务直到完成，返回 {"status", "urls", "detail"}。

    action 默认 VOD DescribeTaskDetail；MPS 音乐任务传 DescribeAigcAudioTask
    （查询仅 TaskId 一个参数，状态 DONE=成功 / FAIL=失败，错误文案前缀用 err_label）。
    """
    deadline = time.time() + timeout
    started = time.time()
    while True:
        if action == "DescribeAigcAudioTask":
            query = {"TaskId": task_id}  # MPS 查询接口无 SubAppId 参数
        else:
            query = {"SubAppId": int(sub_app_id), "TaskId": task_id}
        response = _call_api(secret_id, secret_key, region, endpoint, action, query,
                             version=version, service=service)
        # 真实响应把任务详情平铺在 Response 顶层（部分文档描述为嵌套在 TaskDetail，两者都兼容）
        detail = response.get("TaskDetail") or response
        status, err_code, err_code_ext, message, urls = _extract_task_result(detail)
        if status in ("SUCCESS", "FINISH", "DONE"):
            if not urls:
                if err_code or err_code_ext or message:
                    hint = "（提示词或素材触发内容安全审核，请修改后重试）" if err_code_ext and "Violation" in err_code_ext else ""
                    raise TaskError(task_id, f"{err_label} 任务被拒绝（ErrCode={err_code} ErrCodeExt={err_code_ext or '-'} Message={message or '-'}）TaskId: {task_id} {hint}".rstrip())
                raise TaskError(task_id, f"任务成功但未找到输出文件 URL（原始响应: {json.dumps(detail, ensure_ascii=False)[:400]}）")
            return {"status": status, "urls": urls, "detail": detail}
        if status in ("FAIL", "FAILED", "ERROR"):
            raise TaskError(task_id, f"{err_label} 任务失败 (ErrCode={err_code}): {message or '未知错误'}（TaskId: {task_id}）")
        if time.time() > deadline:
            raise TaskError(task_id, f"任务超时（{timeout}s 未完成）。TaskId: {task_id}，可用「VOD AIGC - 查询任务」节点手动查询")
        elapsed = int(time.time() - started)
        if on_progress:
            on_progress(f"{task_label}… {elapsed}s | TaskId: {task_id[-16:]}")
        time.sleep(max(1, int(poll_interval)))


def _resolve_save_name(url: str, task_id: str, name_hint: str = "", out_dir=None) -> str:
    """本地保存文件名：name_hint 优先（自动补扩展名、重名加序号），否则 task_id 尾号 + URL 文件名。"""
    original = urllib.parse.unquote(urllib.parse.urlparse(url).path.split("/")[-1]) or "aigcGenFile.mp4"
    ext = os.path.splitext(original)[1]
    if name_hint:
        # 命名组合：<filename>_<taskId尾8位> —— 文件与台账/任务可追溯，且 taskId 尾号天然唯一
        base = f"{name_hint}_{task_id[-8:]}"
        if ext and not base.lower().endswith(ext.lower()):
            base += ext
    else:
        base = f"{task_id[-8:]}_{original}"
    out_dir = out_dir or os.path.join(folder_paths.get_output_directory(), "vod_aigc")
    name = base
    counter = 1
    while os.path.exists(os.path.join(out_dir, name)):  # 重名去重（多图同 hint 场景）
        stem, e = os.path.splitext(base)
        name = f"{stem}_{counter}{e}"
        counter += 1
    return name


def _paths_to_image_tensor(paths):
    """把本地图片列表转成 ComfyUI IMAGE 张量（B,H,W,C float 0-1）；失败返回 None 不阻塞主流程。"""
    try:
        import torch
        frames = []
        for p in paths:
            with Image.open(p) as im:
                arr = np.asarray(im.convert("RGB"), dtype=np.float32) / 255.0
            frames.append(torch.from_numpy(arr))
        return torch.stack(frames) if frames else None
    except Exception:
        return None


def _download_video(url: str, task_id: str, on_progress=None, name_hint=None) -> str:
    """把生成的视频下载到 ComfyUI output/vod_aigc/ 目录。

    流式下载 + 60s 超时 + 进度回调；失败时抛出包含可手动下载 URL 的错误，
    避免阻塞线程导致整个 ComfyUI 无法中断。
    """

    out_dir = os.path.join(folder_paths.get_output_directory(), "vod_aigc")
    os.makedirs(out_dir, exist_ok=True)
    name = _resolve_save_name(url, task_id, name_hint or "", out_dir)
    path = os.path.join(out_dir, name)

    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    try:
        with urllib.request.urlopen(req, timeout=60) as resp, open(path, "wb") as f:
            total = int(resp.headers.get("Content-Length") or 0)
            downloaded = 0
            while True:
                chunk = resp.read(256 * 1024)
                if not chunk:
                    break
                f.write(chunk)
                downloaded += len(chunk)
                if on_progress:
                    pct = f"{downloaded / total * 100:.0f}%" if total else f"{downloaded // (1024 * 1024)}MB"
                    on_progress(f"下载中… {pct}")
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"文件下载失败 HTTP {e.code}。可手动下载: {url}")
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        raise RuntimeError(f"文件下载失败（{e}）。可手动下载: {url}")
    return path


# ---------------------------------------------------------------- 执行台账

def _append_history(record: dict):
    """把一条执行记录追加到 output/vod_aigc/execution_history.jsonl（成功/失败都记）。"""
    try:
        out_dir = os.path.join(folder_paths.get_output_directory(), "vod_aigc")
        os.makedirs(out_dir, exist_ok=True)
        path = os.path.join(out_dir, "execution_history.jsonl")
        record.setdefault("time", time.strftime("%Y-%m-%dT%H:%M:%S%z"))
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
    except Exception as e:  # 台账写入失败不影响生成主流程
        print(f"[tencent-vod-aigc] 执行台账写入失败: {e}")


_MIN_BILLED_SECONDS = 5  # 每次任务不足 5 秒按 5 秒计费


def _price_for(resolution: str) -> float:
    """单价（元/秒）解析：tencent-vod-config.json prices，未配置返回 0。"""
    try:
        return float(_load_config_file().get("prices", {}).get(resolution, 0.0))
    except (TypeError, ValueError):
        return 0.0


def _estimate_cost(resolution: str, duration: int) -> tuple:
    """按计费规则估算费用：秒数 = max(时长, 5)，费用 = 秒数 × 单价（元）。"""
    seconds_billed = max(int(duration or 0), _MIN_BILLED_SECONDS)
    return seconds_billed, round(seconds_billed * _price_for(resolution), 4)


def _image_price_for(model: str) -> float:
    """生图单价（元/张）：tencent-vod-config.json image_prices，按模型区分，未配置返回 0。

    不同模型对应不同计费项（如即梦→SI、OG→GPT-Image2 计费），键为模型下拉全名。
    """
    try:
        return float(_load_config_file().get("image_prices", {}).get(model or "", 0.0))
    except (TypeError, ValueError):
        return 0.0


def _view_url_for(path: str) -> str:
    """把输出目录下的文件转成 ComfyUI /view 链接（浏览器可直接播放）。"""
    try:
        rel = os.path.relpath(path, folder_paths.get_output_directory())
        sub, name = os.path.split(rel)
        return f"/view?filename={urllib.parse.quote(name)}&subfolder={urllib.parse.quote(sub)}&type=output"
    except Exception:
        return ""


def _base_record(mode: str, prompt: str, kwargs: dict, task_id="", url="", path="", error="", cache_key=""):
    """构造台账记录：含计费要素（时长/分辨率/模型/张数），便于成本审计。

    视频按秒计费（元/秒 × 计费秒数）；生图按张计费（元/张 × 张数，按模型）；
    音乐生成（t2a）不计秒不计费，费用恒为 0。
    """
    if mode in ("t2i", "i2i"):
        model = kwargs.get("model") or ""
        image_count = int(kwargs.get("output_image_count") or 1)
        seconds_billed, estimated_cost = 0, round(image_count * _image_price_for(model), 4)
    elif mode == "t2a":
        model, image_count = kwargs.get("model") or "", 0
        seconds_billed, estimated_cost = 0, 0.0  # 音乐生成不计秒不计费
    else:
        model, image_count = "", 0
        seconds_billed, estimated_cost = _estimate_cost(kwargs.get("resolution") or "",
                                                        kwargs.get("duration") or 0)
    return {
        "mode": mode,
        "task_id": task_id,
        "status": "failure" if error else "success",
        "prompt": (prompt or "")[:200],
        "duration": int(kwargs.get("duration") or 0),
        "resolution": kwargs.get("resolution") or "",
        "aspect_ratio": kwargs.get("aspect_ratio") or "",
        "audio_generation": kwargs.get("audio_generation") or "",
        "storage_mode": kwargs.get("storage_mode") or "",
        "enhance_prompt": kwargs.get("enhance_prompt") or "",
        "model": model,
        "image_count": image_count,
        "video_url": url,
        "video_path": path,
        "view_url": _view_url_for(path) if path else "",
        "seconds_billed": seconds_billed,
        "estimated_cost": estimated_cost,
        "cache_key": cache_key,
        "cached": False,
        "error": (error or "")[:500],
    }


def _cache_key(mode: str, prompt: str, kwargs: dict) -> str:
    """结果缓存键：mode + prompt + 全部影响输出的参数 + 参考素材指纹。

    同键且产物仍在 = 命中，直接复用本地文件（不调腾讯云 API）。
    """
    key = {"mode": mode, "prompt": prompt or ""}
    for k in ("duration", "resolution", "aspect_ratio", "audio_generation", "storage_mode",
              "enhance_prompt", "media_name", "model", "output_image_count", "output_format",
              "lyrics", "is_instrumental"):
        if k in kwargs:
            key[k] = kwargs[k]
    refs = []
    for k in ("ref_image_urls", "ref_image_paths", "ref_video_paths", "ref_video_urls",
              "ref_audio_paths", "ref_audio_urls",
              "first_frame_url", "first_frame_path", "last_frame_url", "last_frame_path",
              "filename"):
        v = kwargs.get(k)
        if v:
            refs.append(f"{k}={v}")
    img = kwargs.get("ref_images")
    if img is not None:
        try:
            refs.append(f"ref_images={hashlib.sha256(img.cpu().numpy().tobytes()).hexdigest()[:16]}")
        except Exception:
            refs.append(f"ref_images=<unhashable>")
    key["refs"] = "|".join(refs)
    return hashlib.sha256(json.dumps(key, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()


def _find_cached_record(cache_key: str):
    """台账查重：返回同缓存键最近一次成功且产物文件仍在的记录，否则 None。"""
    try:
        out_dir = os.path.join(folder_paths.get_output_directory(), "vod_aigc")
        ledger_path = os.path.join(out_dir, "execution_history.jsonl")
        if not os.path.isfile(ledger_path):
            return None
        hit = None
        with open(ledger_path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if rec.get("cache_key") != cache_key or rec.get("status") != "success":
                    continue
                paths = [p for p in (rec.get("video_path") or "").splitlines() if p]
                if not paths or not all(os.path.isfile(p) for p in paths):
                    continue  # 产物已丢失 → 不命中，允许重新生成
                hit = rec  # 台账按时间追加，后写的覆盖
        return hit
    except Exception:
        return None


def _ledger(mode):
    """生成节点装饰器：成功/失败都写执行台账；失败原样抛出。

    mode 为 None 时按输入自动推断：有参考图/参考 URL 记 i2i，否则 t2i。
    """
    def deco(fn):
        @functools.wraps(fn)
        def wrapper(self, prompt, **kwargs):
            m = mode or ("i2i" if (kwargs.get("ref_image") is not None
                                   or (kwargs.get("ref_image_urls") or "").strip()
                                   or (kwargs.get("ref_image_paths") or "").strip()) else "t2i")
            ck = _cache_key(m, prompt, kwargs)
            # 结果缓存：同参数已成功且产物仍在 → 直接复用，零 API 调用
            if kwargs.get("use_cache", "Enabled") != "Disabled":
                hit = _find_cached_record(ck)
                if hit is not None:
                    task_id, url, path = hit.get("task_id", ""), hit.get("video_url", ""), hit.get("video_path", "")
                    rec = _base_record(m, prompt, kwargs, task_id, url, path, cache_key=ck)
                    rec["cached"] = True
                    if m in ("t2i", "i2i"):
                        paths = [p for p in (path or "").splitlines() if p]
                        tensor = _paths_to_image_tensor(paths)
                        ui_images = [{"filename": os.path.basename(p), "subfolder": "vod_aigc",
                                      "type": "output", "format": os.path.splitext(p)[1].lstrip(".") or "png"}
                                     for p in paths]
                        _append_history(rec)
                        return {"ui": {"images": ui_images}, "result": (task_id, url, path, tensor)}
                    _append_history(rec)
                    return (task_id, url, path)
            try:
                original = fn(self, prompt, **kwargs)  # 元组，或 {"ui": ..., "result": ...} 字典（生图预览）
                result = original.get("result") if isinstance(original, dict) else original
                task_id, url, path = result[0], result[1], result[2]
                _append_history(_base_record(m, prompt, kwargs, task_id, url, path, cache_key=ck))
                return original  # 字典返回需原样保留 ui（预览协议）
            except Exception as e:
                _append_history(_base_record(m, prompt, kwargs,
                                             task_id=getattr(e, "task_id", ""), error=str(e), cache_key=ck))
                raise
        return wrapper
    return deco


# ---------------------------------------------------------------- 输入模板

def _cred_inputs():
    """凭据输入模板：display_name 为前端显示名（可选标记），键名保持 secret_id 不变。"""
    return {
        "secret_id": ("STRING", {"default": "", "display_name": "secret_id (optional)",
                                 "tooltip": "（选填）腾讯云 CAM SecretId。留空则自动读取节点包内 tencent-vod-config.json，建议留空以免密钥写入工作流 JSON"}),
        "secret_key": ("STRING", {"default": "", "display_name": "secret_key (optional)",
                                  "tooltip": "（选填）腾讯云 CAM SecretKey。留空则自动读取节点包内 tencent-vod-config.json"}),
        "sub_app_id": ("STRING", {"default": "", "display_name": "sub_app_id (optional)",
                                  "tooltip": "（选填）VOD 应用 ID。留空则自动读取节点包内 tencent-vod-config.json"}),
    }


def _output_config_inputs():
    return {
        "duration": ("INT", {"default": 5, "min": 4, "max": 15, "step": 1, "tooltip": "生成时长（秒），范围 4-15"}),
        "resolution": (RESOLUTIONS, {"default": "1080P", "tooltip": "768P / 1080P(超分) / 2K / 4K(超分)，越高越贵"}),
        "aspect_ratio": (ASPECT_RATIOS, {"default": "16:9"}),
        "audio_generation": (ON_OFF, {"default": "Enabled", "tooltip": "是否生成音频"}),
        "storage_mode": (STORAGE_MODES, {"default": "Temporary", "tooltip": "Temporary=临时存储(URL 限时有效) / Permanent=永久存储(可后续超分处理)"}),
        "enhance_prompt": (ON_OFF, {"default": "Disabled", "tooltip": "是否启用提示词增强（H3-Context-IR）"}),
        "use_cache": (ON_OFF, {"default": "Enabled",
                               "tooltip": "结果缓存：同参数（提示词/分辨率/参考素材等）已成功过的任务直接复用本地产物，不调用腾讯云 API。需要新结果时改 Disabled 或修改任一参数"}),
        "media_name": ("STRING", {"default": "", "tooltip": "可选，输出文件名/备注"}),
        "filename": ("STRING", {"default": "", "tooltip": "可选，本地保存文件名（不含扩展名，留空自动命名）"}),
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


def _build_music_payload(prompt, model, oc_values, file_infos=None):
    """构造 MPS CreateAigcAudioTask 请求体（GL / MiniMaxMusic 音乐生成）。

    注意：MPS 无 SubAppId 参数；AdditionalParameters 为 JSON 字符串
    （歌词 {"lyric":"..."} 或纯音乐 {"is_instrumental":true}，由调用方拼好传入）。
    """
    name, _, version = (model or "MiniMaxMusic 2.6").partition(" ")
    payload = {
        "ModelName": name,
        "ModelVersion": version,
        "SceneType": "music",
        "Prompt": prompt,
    }
    ap = (oc_values.get("additional_parameters") or "").strip()
    if ap:
        payload["AdditionalParameters"] = ap
    fmt = (oc_values.get("output_format") or "").strip()
    if fmt:
        payload["OutputAudioFormat"] = fmt
    if file_infos:
        payload["AudioInfos"] = file_infos
    return payload


def _build_image_payload(sub_app_id, prompt, model, oc_values, file_infos=None):
    """构造 CreateAigcImageTask 请求体（3.3.2 GEM/Jimeng、3.14 GPT-Image2）。"""
    name, _, version = (model or "Jimeng 4.0").partition(" ")
    cfg = {
        "StorageMode": oc_values["storage_mode"],
        "Resolution": oc_values["resolution"],
        "AspectRatio": oc_values["aspect_ratio"],
    }
    count = int(oc_values.get("output_image_count") or 1)
    if count > 1:
        cfg["OutputImageCount"] = count  # OG 支持 1-8
    fmt = (oc_values.get("output_format") or "").strip()
    if fmt:
        cfg["OutputFormat"] = fmt        # OG 支持 png/jpeg
    payload = {
        "SubAppId": int(sub_app_id),
        "ModelName": name,
        "ModelVersion": version,
        "Prompt": prompt,
        "OutputConfig": cfg,
    }
    if file_infos:
        payload["FileInfos"] = file_infos
    return payload


# ---------------------------------------------------------------- 节点类

class TencentVODH3TextToVideo:
    """文生视频：仅提示词，无素材输入。"""

    @classmethod
    def INPUT_TYPES(cls):
        required = {"prompt": ("STRING", {"multiline": True, "default": "", "tooltip": "提示词，上限 7000 字符"})}
        optional = dict(_cred_inputs())          # 凭据选填：留空读 tencent-vod-config.json
        optional.update(_output_config_inputs())
        return {"required": required, "optional": optional}

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
        path = _download_video(url, task_id, on_progress=lambda t: _set_status(self, t),
                               name_hint=kwargs.get("filename"))
        _set_status(self, "完成")
        return (task_id, url, path)


class TencentVODH3ImageToVideo:
    """图生视频：首帧 / 尾帧 / 首尾帧。支持 IMAGE tensor 或图片 URL。"""

    @classmethod
    def INPUT_TYPES(cls):
        required = {"prompt": ("STRING", {"multiline": True, "default": "", "tooltip": "提示词，上限 7000 字符"})}
        optional = dict(_cred_inputs())          # 凭据选填：留空读 tencent-vod-config.json
        optional.update(_output_config_inputs())
        optional["first_frame"] = ("IMAGE", {"tooltip": "首帧图（ComfyUI 图像，转 Base64 上传）"})
        optional["last_frame"] = ("IMAGE", {"tooltip": "尾帧图"})
        optional["first_frame_path"] = ("STRING", {"default": "", "tooltip": "首帧图本地路径（支持 ~/、input/xxx、output/xxx 或绝对路径；与 first_frame / first_frame_url 三选一）"})
        optional["last_frame_path"] = ("STRING", {"default": "", "tooltip": "尾帧图本地路径（与 last_frame / last_frame_url 三选一）"})
        optional["first_frame_url"] = ("STRING", {"default": "", "tooltip": "首帧图 URL（可匿名下载的图片直链，与 first_frame 二选一）"})
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
            path = (kwargs.get(f"{key}_path") or "").strip()
            if sum(x is not None and x != "" for x in (tensor, url, path)) > 1:
                raise ValueError(f"{usage} 同时提供了 IMAGE / URL / 本地路径，请只保留一种")
            if tensor is not None:
                data = _image_tensor_to_base64(tensor, 0 if key == "first_frame" else -1)
                base64_total += len(data)
                if base64_total > _MAX_BASE64_TOTAL:
                    raise ValueError("Base64 素材总大小超过 70MB 上限")
                file_infos.append({"Type": "Base64", "Category": "Image", "Base64": data, "Usage": usage})
            elif url:
                _validate_media_url(url, _ALLOWED_IMAGE_EXTS, f"{usage}图")
                file_infos.append({"Type": "Url", "Category": "Image", "Url": url, "Usage": usage})
            elif path:
                data = _file_to_base64(path, _MAX_IMAGE_BYTES, f"{usage}图", _ALLOWED_IMAGE_EXTS)
                base64_total += len(data)
                if base64_total > _MAX_BASE64_TOTAL:
                    raise ValueError("Base64 素材总大小超过 70MB 上限")
                file_infos.append({"Type": "Base64", "Category": "Image", "Base64": data, "Usage": usage})

        if not file_infos:
            raise ValueError("至少提供一张首帧/尾帧图（IMAGE / URL / 本地路径）")

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
        path = _download_video(url, task_id, on_progress=lambda t: _set_status(self, t),
                               name_hint=kwargs.get("filename"))
        _set_status(self, "完成")
        return (task_id, url, path)


class TencentVODH3ReferenceToVideo:
    """多模态参考生视频：最多 9 图 + 3 视频 + 3 音频（总数 ≤12），音频不能单独输入。"""

    @classmethod
    def INPUT_TYPES(cls):
        required = {"prompt": ("STRING", {"multiline": True, "default": "", "tooltip": "提示词；多图时可用「图1…图2…」描述"})}
        optional = dict(_cred_inputs())          # 凭据选填：留空读 tencent-vod-config.json
        optional.update(_output_config_inputs())
        optional["ref_images"] = ("IMAGE", {"tooltip": "参考图，支持批量（batch）：多张图请先合成 batch（如 Load Images / ImageBatch 节点），每帧一张，最多 9 张；也可用 ref_image_urls 传多个 URL"})
        optional["ref_image_paths"] = ("STRING", {"multiline": True, "default": "", "tooltip": "本地参考图路径，每行一个（最多 9 张），支持 ~/、input/xxx、output/xxx 或绝对路径"})
        optional["ref_image_urls"] = ("STRING", {"multiline": True, "default": "", "tooltip": "参考图 URL，每行一个，最多 9 个。必须为可匿名下载的图片直链（.jpg/.png 等），不支持网页地址"})
        optional["ref_video_paths"] = ("STRING", {"multiline": True, "default": "", "tooltip": "本地参考视频路径，每行一个（2-15 秒/段，最多 3 段，共 ≤15 秒）"})
        optional["ref_video_urls"] = ("STRING", {"multiline": True, "default": "", "tooltip": "参考视频 URL，每行一个。必须为可匿名下载的视频直链（.mp4/.mov 等），不支持网页地址（如 B 站/抖音页面）；网页视频请先下载到本地用 ref_video_paths"})
        optional["ref_audio_paths"] = ("STRING", {"multiline": True, "default": "", "tooltip": "本地参考音频路径，每行一个（2-15 秒/段，最多 3 段，不能单独输入）"})
        optional["ref_audio_urls"] = ("STRING", {"multiline": True, "default": "", "tooltip": "参考音频 URL，每行一个。必须为可匿名下载的音频直链（.mp3/.wav 等），不支持网页地址"})
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

        # 参考图：本地路径
        for path in _parse_multiline(kwargs.get("ref_image_paths")):
            data = _file_to_base64(path, _MAX_IMAGE_BYTES, "参考图", _ALLOWED_IMAGE_EXTS)
            base64_total += len(data)
            file_infos.append({"Type": "Base64", "Category": "Image", "Base64": data, "Usage": "Reference"})

        # 参考图：URL
        for url in _parse_multiline(kwargs.get("ref_image_urls")):
            _validate_media_url(url, _ALLOWED_IMAGE_EXTS, "参考图")
            file_infos.append({"Type": "Url", "Category": "Image", "Url": url, "Usage": "Reference"})

        # 参考视频
        for path in _parse_multiline(kwargs.get("ref_video_paths")):
            data = _file_to_base64(path, _MAX_VIDEO_BYTES, "参考视频", _ALLOWED_VIDEO_EXTS)
            base64_total += len(data)
            file_infos.append({"Type": "Base64", "Category": "Video", "Base64": data, "Usage": "Reference"})
        for url in _parse_multiline(kwargs.get("ref_video_urls")):
            _validate_media_url(url, _ALLOWED_VIDEO_EXTS, "参考视频")
            file_infos.append({"Type": "Url", "Category": "Video", "Url": url, "Usage": "Reference"})

        # 参考音频
        for path in _parse_multiline(kwargs.get("ref_audio_paths")):
            data = _file_to_base64(path, _MAX_AUDIO_BYTES, "参考音频", _ALLOWED_AUDIO_EXTS)
            base64_total += len(data)
            file_infos.append({"Type": "Base64", "Category": "Audio", "Base64": data, "Usage": "Reference"})
        for url in _parse_multiline(kwargs.get("ref_audio_urls")):
            _validate_media_url(url, _ALLOWED_AUDIO_EXTS, "参考音频")
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
        path = _download_video(url, task_id, on_progress=lambda t: _set_status(self, t),
                               name_hint=kwargs.get("filename"))
        _set_status(self, "完成")
        return (task_id, url, path)


class TencentVODAIGCImageTask:
    """文生图 / 图生图：CreateAigcImageTask（文档 3.3.2，模型 GEM / Jimeng）。

    文生图不传 FileInfos；图生图可接 ComfyUI IMAGE（批量，每帧一张）或参考图 URL。
    输出下载到 output/vod_aigc/，台账 mode 记 t2i / i2i（生图按张计费，费用留 0）。
    """

    @classmethod
    def INPUT_TYPES(cls):
        required = {"prompt": ("STRING", {"multiline": True, "default": "",
                                           "tooltip": "提示词；图生图时描述参考图中的主体"})}
        optional = dict(_cred_inputs())          # 凭据选填：留空读 tencent-vod-config.json
        optional.update({
            "model": (["Jimeng 4.0", "GEM 3.0", "OG image2_low", "OG image2_medium", "OG image2_high"],
                     {"default": "Jimeng 4.0",
                      "tooltip": "生图模型：Jimeng/GEM（3.3.2 示例）；OG = GPT-Image2（3.14 指南），low/medium/high 为质量档位"}),
            "output_image_count": ("INT", {"default": 1, "min": 1, "max": 8, "step": 1,
                                           "tooltip": "生成张数（OG 支持 1-8；仅 >1 时传给接口）"}),
            "output_format": (["", "png", "jpeg"], {"default": "",
                                                    "tooltip": "输出格式（OG 支持 png/jpeg；留空跟随模型默认）"}),
            "filename": ("STRING", {"default": "", "tooltip": "可选，本地保存文件名（不含扩展名，留空自动命名；多图自动加序号）"}),
            "ref_image": ("IMAGE", {"tooltip": "参考图（图生图）。多张请先合成 batch，每帧一张，最多 9 张；或用 ref_image_paths / ref_image_urls"}),
            "ref_image_paths": ("STRING", {"multiline": True, "default": "",
                                           "tooltip": "参考图本地路径（图生图），每行一个，最多 9 个。支持 ~/、input/xxx、output/xxx 或绝对路径"}),
            "ref_image_urls": ("STRING", {"multiline": True, "default": "",
                                          "tooltip": "参考图 URL（图生图），每行一个，最多 9 个。必须为可匿名下载的图片直链，不支持网页地址"}),
            "resolution": (["768P", "1080P", "1K", "2K"], {"default": "1080P",
                                                             "tooltip": "按模型支持选择（OG 常用 1K/2K）"}),
            "aspect_ratio": (ASPECT_RATIOS, {"default": "16:9"}),
            "storage_mode": (STORAGE_MODES, {"default": "Temporary"}),
            "region": ("STRING", {"default": DEFAULT_REGION}),
            "endpoint": ("STRING", {"default": ""}),
            "poll_interval": ("INT", {"default": 10, "min": 3, "max": 120, "step": 1}),
            "timeout": ("INT", {"default": 600, "min": 60, "max": 7200, "step": 60}),
            "use_cache": (ON_OFF, {"default": "Enabled",
                                   "tooltip": "结果缓存：同参数（提示词/模型/参考素材等）已成功过的任务直接复用本地产物，不调用腾讯云 API。需要新结果时改 Disabled 或修改任一参数"}),
        })
        return {"required": required, "optional": optional}

    RETURN_TYPES = ("STRING", "STRING", "STRING", "IMAGE")
    RETURN_NAMES = ("task_id", "image_url", "image_path", "preview_image")
    FUNCTION = "generate"
    CATEGORY = "Tencent VOD AIGC"
    OUTPUT_NODE = True

    @classmethod
    def IS_CHANGED(cls, **kwargs):
        return float("NaN")  # 每次都提交新任务，不缓存

    @_ledger(None)  # mode 按是否有参考图自动推断 t2i / i2i
    def generate(self, prompt, **kwargs):
        if not prompt.strip():
            raise ValueError("Prompt 不能为空")

        file_infos = []
        ref_image = kwargs.get("ref_image")
        if ref_image is not None:
            n = ref_image.shape[0]
            if n > 9:
                raise ValueError(f"参考图最多 9 张，当前 {n} 张")
            for i in range(n):
                data = _image_tensor_to_base64(ref_image, i)
                # 生图 FileInfos 仅 Type + Base64/Url（与生视频不同，无 Category/Usage）
                file_infos.append({"Type": "Base64", "Base64": data})
        for path in _parse_multiline(kwargs.get("ref_image_paths")):
            data = _file_to_base64(path, _MAX_IMAGE_BYTES, "参考图", _ALLOWED_IMAGE_EXTS)
            file_infos.append({"Type": "Base64", "Base64": data})
        for url in _parse_multiline(kwargs.get("ref_image_urls")):
            _validate_media_url(url, _ALLOWED_IMAGE_EXTS, "参考图")
            file_infos.append({"Type": "Url", "Url": url})

        secret_id, secret_key, sub_app_id = _resolve_credentials(
            kwargs.get("secret_id"), kwargs.get("secret_key"), kwargs.get("sub_app_id"))
        region = kwargs.get("region") or ""
        endpoint = kwargs.get("endpoint") or ""
        payload = _build_image_payload(sub_app_id, prompt, kwargs.get("model"), kwargs,
                                       file_infos or None)
        _set_status(self, "提交生图任务…")
        response = _call_api(secret_id, secret_key, region, endpoint, "CreateAigcImageTask", payload)
        task_id = response.get("TaskId")
        if not task_id:
            raise RuntimeError(f"未返回 TaskId（原始响应: {json.dumps(response, ensure_ascii=False)[:400]}）")
        result = _wait_for_task(secret_id, secret_key, region, endpoint, sub_app_id, task_id,
                                kwargs.get("poll_interval", 10), kwargs.get("timeout", 600),
                                on_progress=lambda t: _set_status(self, t), task_label="生图生成中")
        urls = result["urls"]
        if not urls:
            raise RuntimeError("生图任务未返回输出文件 URL")
        _set_status(self, f"下载图片…（{len(urls)} 张）")
        paths = [_download_video(u, task_id, on_progress=lambda t: _set_status(self, t),
                                 name_hint=kwargs.get("filename")) for u in urls]
        _set_status(self, "完成")
        # preview_image：本地图片 → IMAGE 张量（可接下游）；失败返回 None 不阻塞
        # ui.images：SaveImage 同款预览协议，让节点上直接显示生成图
        tensor = _paths_to_image_tensor(paths)
        ui_images = [{"filename": os.path.basename(p), "subfolder": "vod_aigc", "type": "output",
                      "format": os.path.splitext(p)[1].lstrip(".") or "png"} for p in paths]
        return {"ui": {"images": ui_images},
                "result": (task_id, "\n".join(urls), "\n".join(paths), tensor)}


class TencentVODAIGCMusicTask:
    """AI 音乐生成：MPS CreateAigcAudioTask / DescribeAigcAudioTask（GL / MiniMaxMusic）。

    支持歌词（AdditionalParameters.lyric）与纯音乐（is_instrumental）两种模式；
    可传参考音频（AudioInfos，路径或 URL）；输出 mp3/wav 下载到 output/vod_aigc/。
    注意：MPS 接口无 SubAppId，凭据仅需 SecretId/SecretKey（节点输入或配置文件）。
    """

    _MUSIC_MODELS = ["GL 2.0", "GL 3.0-clip", "GL 3.0-pro",
                     "MiniMaxMusic 2.0", "MiniMaxMusic 2.5", "MiniMaxMusic 2.6"]

    @classmethod
    def INPUT_TYPES(cls):
        required = {"prompt": ("STRING", {"multiline": True, "default": "",
                                           "tooltip": "音乐风格 / 演唱要求描述，上限 2000 字符"})}
        optional = dict(_cred_inputs())          # 凭据选填：留空读 tencent-vod-config.json
        optional.update({
            "model": (cls._MUSIC_MODELS, {"default": "MiniMaxMusic 2.6",
                                          "tooltip": "音乐模型：GL 2.0 / 3.0-clip / 3.0-pro；MiniMaxMusic 2.0 / 2.5 / 2.6"}),
            "lyrics": ("STRING", {"multiline": True, "default": "",
                                  "tooltip": "歌词（可选），换行分段；与 is_instrumental 互斥"}),
            "is_instrumental": (ON_OFF, {"default": "Disabled",
                                         "tooltip": "纯音乐模式（无歌词演唱）；与歌词互斥"}),
            "output_format": (["", "mp3", "wav"], {"default": "",
                                                   "tooltip": "输出格式（mp3/wav，留空跟随模型默认）"}),
            "ref_audio_paths": ("STRING", {"multiline": True, "default": "",
                                           "tooltip": "本地参考音频路径，每行一个（最多 3 个），支持 ~/、input/xxx、output/xxx 或绝对路径"}),
            "ref_audio_urls": ("STRING", {"multiline": True, "default": "",
                                          "tooltip": "参考音频 URL，每行一个。必须为可匿名下载的音频直链（.mp3/.wav 等），不支持网页地址"}),
            "filename": ("STRING", {"default": "", "tooltip": "可选，本地保存文件名（不含扩展名，留空自动命名）"}),
            "use_cache": (ON_OFF, {"default": "Enabled",
                                   "tooltip": "结果缓存：同参数（提示词/模型/歌词/参考音频等）已成功过的任务直接复用本地产物，不调用腾讯云 API。需要新结果时改 Disabled 或修改任一参数"}),
            "region": ("STRING", {"default": DEFAULT_REGION}),
            "endpoint": ("STRING", {"default": MPS_ENDPOINT, "tooltip": "API 地址，留空用 mps.tencentcloudapi.com"}),
            "poll_interval": ("INT", {"default": 10, "min": 3, "max": 120, "step": 1}),
            "timeout": ("INT", {"default": 600, "min": 60, "max": 7200, "step": 60}),
        })
        return {"required": required, "optional": optional}

    RETURN_TYPES = ("STRING", "STRING", "STRING")
    RETURN_NAMES = ("task_id", "audio_url", "audio_path")
    FUNCTION = "generate"
    CATEGORY = "Tencent VOD AIGC"
    OUTPUT_NODE = True

    @classmethod
    def IS_CHANGED(cls, **kwargs):
        return float("NaN")  # 每次都提交新任务，不缓存

    @_ledger("t2a")  # 音乐生成：台账不计秒不计费（estimated_cost=0）
    def generate(self, prompt, **kwargs):
        prompt = (prompt or "").strip()
        if not prompt:
            raise ValueError("Prompt 不能为空")
        if len(prompt) > 2000:
            raise ValueError(f"Prompt 超过 2000 字符上限（当前 {len(prompt)} 字符）")

        lyrics = (kwargs.get("lyrics") or "").strip()
        instrumental = kwargs.get("is_instrumental", "Disabled") == "Enabled"
        if lyrics and instrumental:
            raise ValueError("已填写歌词又开启纯音乐（is_instrumental），两种模式互斥，请只保留一种")

        secret_id, secret_key = _resolve_secret_pair(kwargs.get("secret_id"), kwargs.get("secret_key"))
        region = kwargs.get("region") or ""
        endpoint = kwargs.get("endpoint") or MPS_ENDPOINT

        file_infos = []
        base64_total = 0
        for path in _parse_multiline(kwargs.get("ref_audio_paths")):
            data = _file_to_base64(path, _MAX_AUDIO_BYTES, "参考音频", _ALLOWED_AUDIO_EXTS)
            base64_total += len(data)
            file_infos.append({"Type": "Base64", "Base64": data})
        for url in _parse_multiline(kwargs.get("ref_audio_urls")):
            _validate_media_url(url, _ALLOWED_AUDIO_EXTS, "参考音频")
            file_infos.append({"Type": "Url", "Url": url})
        if base64_total > _MAX_BASE64_TOTAL:
            raise ValueError("Base64 素材总大小超过 70MB 上限")

        additional = {}
        if lyrics:
            additional["lyric"] = lyrics
        if instrumental:
            additional["is_instrumental"] = True
        kwargs = dict(kwargs)
        kwargs["additional_parameters"] = json.dumps(additional, ensure_ascii=False) if additional else ""
        payload = _build_music_payload(prompt, kwargs.get("model"), kwargs, file_infos or None)
        _set_status(self, "提交音乐生成任务…")
        response = _call_api(secret_id, secret_key, region, endpoint, "CreateAigcAudioTask", payload,
                             version=MPS_API_VERSION, service=MPS_SERVICE)
        task_id = response.get("TaskId")
        if not task_id:
            raise RuntimeError(f"未返回 TaskId（原始响应: {json.dumps(response, ensure_ascii=False)[:400]}）")
        result = _wait_for_task(secret_id, secret_key, region, endpoint, None, task_id,
                                kwargs.get("poll_interval", 10), kwargs.get("timeout", 600),
                                on_progress=lambda t: _set_status(self, t),
                                task_label="音乐生成中", action="DescribeAigcAudioTask",
                                err_label="音乐", version=MPS_API_VERSION, service=MPS_SERVICE)
        url = result["urls"][0]
        _set_status(self, "下载音频…")
        path = _download_video(url, task_id, on_progress=lambda t: _set_status(self, t),
                               name_hint=kwargs.get("filename"))
        _set_status(self, "完成")
        return (task_id, url, path)


class TencentVODAIGCQueryTask:
    """查询任务状态：输入 TaskId，输出状态与输出文件 URL（任务超时/失败排查用）。"""

    @classmethod
    def INPUT_TYPES(cls):
        required = {"task_id": ("STRING", {"default": "", "tooltip": "CreateAigcVideoTask 返回的 TaskId"})}
        optional = dict(_cred_inputs())          # 凭据选填：留空读 tencent-vod-config.json
        optional["region"] = ("STRING", {"default": DEFAULT_REGION})
        optional["endpoint"] = ("STRING", {"default": ""})
        return {"required": required, "optional": optional}

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
        # 真实响应把任务详情平铺在 Response 顶层（部分文档描述为嵌套在 TaskDetail，两者都兼容）
        detail = response.get("TaskDetail") or response
        status, _, _, _, urls = _extract_task_result(detail)
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
        out_dir = os.path.join(folder_paths.get_output_directory(), "vod_aigc")
        os.makedirs(out_dir, exist_ok=True)
        name = name_hint or os.path.basename(urllib.parse.urlparse(url.strip()).path) or "aigcVideoGenFile.mp4"
        path = os.path.join(out_dir, name)
        _set_status(self, "下载中…")
        urllib.request.urlretrieve(url.strip(), path)
        _set_status(self, "完成")
        return (path,)


class TencentVODAIGCViewHistory:
    """读取执行台账（execution_history.jsonl），把历史记录以文本显示在节点输出上。"""

    @classmethod
    def INPUT_TYPES(cls):
        return {"required": {}}

    RETURN_TYPES = ("STRING", "STRING")
    RETURN_NAMES = ("history_text", "ledger_path")
    FUNCTION = "view"
    CATEGORY = "Tencent VOD AIGC"
    OUTPUT_NODE = True

    @classmethod
    def IS_CHANGED(cls, **kwargs):
        return float("NaN")  # 每次运行都重新读取台账

    def view(self):
        ledger = os.path.join(folder_paths.get_output_directory(), "vod_aigc", "execution_history.jsonl")
        if not os.path.isfile(ledger):
            return ("（台账不存在，尚无执行记录）", "")
        lines = []
        with open(ledger, encoding="utf-8") as f:
            for i, line in enumerate(f, 1):
                line = line.strip()
                if not line:
                    continue
                try:
                    r = json.loads(line)
                except Exception:
                    lines.append(f"[{i}] （无法解析的记录）")
                    continue
                marker = "✅" if r.get("status") == "success" else "❌"
                err = f" | 错误: {str(r.get('error', ''))[:80]}" if r.get("error") else ""
                asset = os.path.basename(r.get("video_path") or r.get("video_url") or "")
                cost = r.get("estimated_cost") or 0
                billed = r.get("seconds_billed") or 0
                if r.get("mode") in ("t2i", "i2i"):
                    img_n = r.get("image_count") or 1
                    cost_txt = f"≈¥{cost:.2f}/{img_n}张" if cost > 0 else "¥未配置单价"
                elif r.get("mode") == "t2a":
                    cost_txt = f"≈¥{cost:.2f}" if cost > 0 else "¥未配置单价"
                else:
                    cost_txt = f"≈¥{cost:.2f}/{billed}s" if cost > 0 else "¥未配置单价"
                spec = "音乐" if r.get("mode") == "t2a" else f"{r.get('resolution', '')}/{r.get('duration', '')}s"
                url_or_asset = r.get("video_url") or asset
                view = r.get("view_url") or ""
                lines.append(
                    f"[{i}] {r.get('time', '')} {marker} {r.get('mode', '')} "
                    f"{spec} {cost_txt}"
                    f" | {str(r.get('prompt', ''))[:40]} | {str(r.get('task_id', ''))[-16:]}"
                    f" | {url_or_asset}{(' | ' + view) if view else ''}{err}"
                )
        text = "\n".join(lines) if lines else "（台账为空）"
        # ui 协议：让文本显示在节点输出区与历史面板；result 提供实际输出值
        return {"ui": {"text": [text]}, "result": (text, ledger)}


# 视频类生成节点接入执行台账：成功/失败都落盘一条 JSONL 记录（生图/音乐节点在类内装饰）
TencentVODH3TextToVideo.generate = _ledger("t2v")(TencentVODH3TextToVideo.generate)
TencentVODH3ImageToVideo.generate = _ledger("i2v")(TencentVODH3ImageToVideo.generate)
TencentVODH3ReferenceToVideo.generate = _ledger("r2v")(TencentVODH3ReferenceToVideo.generate)

NODE_CLASS_MAPPINGS = {
    "TencentVODH3TextToVideo": TencentVODH3TextToVideo,
    "TencentVODH3ImageToVideo": TencentVODH3ImageToVideo,
    "TencentVODH3ReferenceToVideo": TencentVODH3ReferenceToVideo,
    "TencentVODAIGCImageTask": TencentVODAIGCImageTask,
    "TencentVODAIGCMusicTask": TencentVODAIGCMusicTask,
    "TencentVODAIGCQueryTask": TencentVODAIGCQueryTask,
    "TencentVODAIGCDownloadVideo": TencentVODAIGCDownloadVideo,
    "TencentVODAIGCViewHistory": TencentVODAIGCViewHistory,
}

NODE_DISPLAY_NAME_MAPPINGS = {
    "TencentVODH3TextToVideo": "VOD AIGC - H3 文生视频",
    "TencentVODH3ImageToVideo": "VOD AIGC - H3 图生视频（首/尾帧）",
    "TencentVODH3ReferenceToVideo": "VOD AIGC - H3 多模态参考生视频",
    "TencentVODAIGCImageTask": "VOD AIGC - 文生图/图生图",
    "TencentVODAIGCMusicTask": "VOD AIGC - 音乐生成 (MPS)",
    "TencentVODAIGCQueryTask": "VOD AIGC - 查询任务",
    "TencentVODAIGCDownloadVideo": "VOD AIGC - 下载视频",
    "TencentVODAIGCViewHistory": "VOD AIGC - 查看执行台账",
}


_register_http_routes()  # 首次使用弹窗的凭据状态/保存接口（非 ComfyUI 环境自动跳过）
