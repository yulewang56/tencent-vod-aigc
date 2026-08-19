"""腾讯云 VOD AIGC（MiniMax Hailuo H3 / 腾讯云 VS 生视频）ComfyUI 自定义节点（薄壳）。

全部纯逻辑（TC3 签名、API 调用、payload 构造、轮询、下载、缓存、计价、台账）
在 vod_aigc_core.py（纯标准库，无头可跑）。本文件仅保留：
- 节点类定义 / INPUT_TYPES / RETURN_* / FUNCTION / CATEGORY / 显示名 / tooltip / UI 默认值
- IMAGE tensor 转换与 folder_paths 相关路径处理
- 模块级委托函数（_call_api / _wait_for_task / _download_video 等）：运行时解析本模块
  命名空间，供测试打桩（monkey-patch）与节点类调用

协议：腾讯云 API v3（TC3-HMAC-SHA256 签名），接口 CreateAigcVideoTask / DescribeTaskDetail。
仅依赖 Python 标准库 + ComfyUI 自带的 numpy/Pillow/torch，无需额外 pip 安装。

对应《VOD AIGC服务接入指南》3.17 节（ModelName=Hailuo, ModelVersion=H3）与
《VS模型接入使用指南》（ModelName=VS，ModelVersion=2.0/2.0-fast/2.0-mini/2.5，
四模式合一：文生视频 / 首帧 / 首尾帧 / 多模态参考，含素材注册与活体认证 core API）。
"""

import base64
import functools
import io
import json
import os
import urllib.parse
import urllib.request

import numpy as np
from PIL import Image

import vod_aigc_core as core
from vod_aigc_core import (
    SERVICE, API_VERSION, DEFAULT_ENDPOINT, DEFAULT_REGION,
    MPS_SERVICE, MPS_API_VERSION, MPS_ENDPOINT,
    RESOLUTIONS, ASPECT_RATIOS, ON_OFF, STORAGE_MODES, MUSIC_MODELS,
    _MAX_IMAGE_BYTES, _MAX_VIDEO_BYTES, _MAX_AUDIO_BYTES, _MAX_BASE64_TOTAL,
    _ALLOWED_VIDEO_EXTS, _ALLOWED_IMAGE_EXTS, _ALLOWED_AUDIO_EXTS,
    _CONFIG_FILE,
    TaskError,
    _hmac_sha256, _canonical_headers, _sign_request,
    build_video_payload as _build_payload,
    build_image_payload as _build_image_payload,
    build_music_payload as _build_music_payload,
    extract_task_result as _extract_task_result,
    extract_video_and_lastframe as _extract_video_and_lastframe,
    extract_asset_id as _extract_asset_id,
    parse_multiline as _parse_multiline,
    expand_prompt_refs as _expand_prompt_refs,
    validate_prompt_refs as _validate_prompt_refs,
    annotate_content_refs as _annotate_content_refs,
    validate_media_url as _validate_media_url,
    check_media_quota as _check_media_quota,
    save_config_file as _save_config_file,
    cache_key as _cache_key,
    build_ext_info as _build_ext_info,
    validate_vs_options as _validate_vs_options,
)

# folder_paths 在不同 ComfyUI 版本位置不同：经典版是仓库根目录的顶层模块，新版在 comfy 包内
try:
    from comfy import folder_paths
except ImportError:
    import folder_paths


# ---------------------------------------------------------------- 模块级委托（测试可打桩）

def _call_api(secret_id: str, secret_key: str, region: str, endpoint: str, action: str,
              payload: dict, version=API_VERSION, service=SERVICE) -> dict:
    """调用腾讯云接口，返回 Response 对象；业务错误抛 RuntimeError（委托 core.call_api）。"""
    return core.call_api(secret_id, secret_key, region, endpoint, action, payload,
                         version=version, service=service)


def _wait_for_task(secret_id, secret_key, region, endpoint, sub_app_id, task_id,
                   poll_interval, timeout, on_progress=None, task_label="H3 生成中",
                   action="DescribeTaskDetail", err_label="H3",
                   version=API_VERSION, service=SERVICE, require_urls=True) -> dict:
    """轮询任务直到完成（委托 core.wait_for_task；注入本模块 _call_api 供测试打桩）。

    require_urls=False 用于素材注册任务（成功无 FileUrl 属正常，AssetId 另行提取）。
    """
    return core.wait_for_task(secret_id, secret_key, region, endpoint, sub_app_id, task_id,
                              poll_interval, timeout, on_progress=on_progress, task_label=task_label,
                              action=action, err_label=err_label, version=version, service=service,
                              call_api_fn=_call_api, require_urls=require_urls)


def _download_video(url: str, task_id: str, on_progress=None, name_hint=None) -> str:
    """把生成的视频/图片下载到 ComfyUI output/vod_aigc/ 目录（委托 core.download_file）。"""
    out_dir = os.path.join(folder_paths.get_output_directory(), "vod_aigc")
    return core.download_file(url, task_id, out_dir, name_hint or "", on_progress=on_progress)


def _create_material(secret_id: str, secret_key: str, region: str, endpoint: str,
                     sub_app_id, file_url: str, asset_type: str, asset_name: str,
                     is_real_person, group_id="", group_name="", group_description=""):
    """提交素材注册任务，返回 TaskId（委托 core.create_material；注入 _call_api 供测试打桩）。"""
    return core.create_material(secret_id, secret_key, region, endpoint, sub_app_id, file_url,
                                asset_type, asset_name, is_real_person, group_id=group_id,
                                group_name=group_name, group_description=group_description,
                                call_api_fn=_call_api)


def _load_config_file(dir_path=None):
    """读取统一配置文件 tencent-vod-config.json（委托 core.load_config）。

    返回 {"secret_id", "secret_key", "sub_app_id", "prices": {分辨率: 单价}}，
    文件缺失/损坏返回空结构。
    """
    base = dir_path or os.path.dirname(os.path.abspath(__file__))
    return core.load_config(os.path.join(base, _CONFIG_FILE))


def _resolve_credentials(secret_id, secret_key, sub_app_id):
    """凭据解析优先级：节点输入 > tencent-vod-config.json（校验委托 core.resolve_credentials）。"""
    file_creds = _load_config_file()
    return core.resolve_credentials({
        "secret_id": (secret_id or "").strip() or file_creds.get("secret_id"),
        "secret_key": (secret_key or "").strip() or file_creds.get("secret_key"),
        "sub_app_id": (sub_app_id or "").strip() or file_creds.get("sub_app_id"),
    })


def _resolve_secret_pair(secret_id, secret_key):
    """凭据解析（仅密钥对）：供 MPS 等不使用 SubAppId 的服务（如音乐生成）。"""
    file_creds = _load_config_file()
    return core.resolve_secret_pair({
        "secret_id": (secret_id or "").strip() or file_creds.get("secret_id"),
        "secret_key": (secret_key or "").strip() or file_creds.get("secret_key"),
    })


def _credentials_configured() -> bool:
    """凭据是否已配置（tencent-vod-config.json 三项齐全），供前端弹窗判断。"""
    file_creds = _load_config_file()
    return bool(file_creds.get("secret_id") and file_creds.get("secret_key") and file_creds.get("sub_app_id"))


def _price_for(resolution: str) -> float:
    """单价（元/秒）解析：tencent-vod-config.json prices，未配置返回 0（委托 core.price_for）。"""
    return core.price_for(resolution, _load_config_file())


def _image_price_for(model: str) -> float:
    """生图单价（元/张）：tencent-vod-config.json image_prices，按模型区分，未配置返回 0。"""
    return core.image_price_for(model, _load_config_file())


def _estimate_cost(resolution: str, duration: int) -> tuple:
    """按计费规则估算费用：秒数 = max(时长, 5)，费用 = 秒数 × 单价（元）。"""
    return core.estimate_cost(resolution, duration, _load_config_file())


def _base_record(mode: str, prompt: str, kwargs: dict, task_id="", url="", path="", error="", cache_key=""):
    """构造台账记录：含计费要素（时长/分辨率/模型/张数），便于成本审计（委托 core.base_record）。"""
    return core.base_record(mode, prompt, kwargs, task_id=task_id, url=url, path=path,
                            error=error, cache_key=cache_key, cfg=_load_config_file(),
                            view_url=_view_url_for(path) if path else "")


def _find_cached_record(cache_key: str):
    """台账查重：返回同缓存键最近一次成功且产物文件仍在的记录，否则 None。"""
    ledger = os.path.join(folder_paths.get_output_directory(), "vod_aigc", "execution_history.jsonl")
    return core.find_cached_record(cache_key, ledger)


def _append_history(record: dict):
    """把一条执行记录追加到 output/vod_aigc/execution_history.jsonl（成功/失败都记）。"""
    ledger = os.path.join(folder_paths.get_output_directory(), "vod_aigc", "execution_history.jsonl")
    core.append_history(record, ledger)


def _resolve_media_path(path: str) -> str:
    """素材路径解析：~ 展开 + 绝对路径原样；input/、output/ 前缀解析到 ComfyUI 对应目录；其余按进程工作目录。"""
    return core.resolve_media_path(path, input_dir=folder_paths.get_input_directory(),
                                   out_dir=folder_paths.get_output_directory())


def _file_to_base64(path: str, max_bytes: int, what: str, allowed_exts=None, image=False) -> str:
    """本地素材 → Base64（≤max_bytes）；文件缺失 / 扩展名不允许 / 超限抛 ValueError。

    image=True 时走 PIL 压缩路径（参考图不保真压缩到 ≤1.2MB，控请求体在网关 10MB 内）；
    视频/音频等素材保持原样（Base64 直读，走 core）。
    """
    if image:
        resolved = _resolve_media_path(path)
        if not os.path.isfile(resolved):
            raise ValueError(f"文件不存在: {path}（支持 ~/、input/xxx、output/xxx 或绝对路径）")
        if allowed_exts:
            ext = os.path.splitext(resolved)[1].lower()
            if ext and ext not in allowed_exts:
                raise ValueError(f"{what} 扩展名 \"{ext}\" 不支持，允许: {', '.join(allowed_exts)}（路径: {path[:80]}）")
        with Image.open(resolved) as im:
            data = _compress_image(im)
        if len(data) > max_bytes:
            raise ValueError(f"{what} 超过 {max_bytes // (1024*1024)}MB 上限: {path}")
        return base64.b64encode(data).decode("ascii")
    return core.file_to_base64(path, max_bytes, what, allowed_exts,
                               input_dir=folder_paths.get_input_directory(),
                               out_dir=folder_paths.get_output_directory())


def _resolve_save_name(url: str, task_id: str, name_hint: str = "", out_dir=None) -> str:
    """本地保存文件名：name_hint 优先（自动补扩展名、重名加序号），否则 task_id 尾号 + URL 文件名。"""
    out_dir = out_dir or os.path.join(folder_paths.get_output_directory(), "vod_aigc")
    return core.resolve_save_name(url, task_id, name_hint, out_dir)


def _view_url_for(path: str) -> str:
    """把输出目录下的文件转成 ComfyUI /view 链接（浏览器可直接播放）。"""
    try:
        rel = os.path.relpath(path, folder_paths.get_output_directory())
        sub, name = os.path.split(rel)
        return f"/view?filename={urllib.parse.quote(name)}&subfolder={urllib.parse.quote(sub)}&type=output"
    except Exception:
        return ""


def _set_status(node, text: str):
    """向前端显示节点运行状态（旧版本忽略）。"""
    try:
        node.display_string = text
    except Exception:
        pass


_REF_IMAGE_TARGET = int(1.2 * 1024 * 1024)  # 参考图单张压缩目标（网关请求体 10MB，5 图场景留余量）


def _compress_image(img) -> bytes:
    """参考图压缩：RGB 白底合成 → 缩放（最长边 ≤2048）→ JPEG 迭代降质到 ≤1.2MB。

    模型内部会缩放参考图，压缩画质损失可接受；参数序列固定（确定性，缓存键不受影响）。
    """
    if img.mode != "RGB":
        img = img.convert("RGB")
    max_side, quality = 2048, 88
    data = b""
    for _ in range(8):
        im = img
        w, h = im.size
        if max(w, h) > max_side:
            scale = max_side / max(w, h)
            im = im.resize((max(1, int(w * scale)), max(1, int(h * scale))), Image.LANCZOS)
        buf = io.BytesIO()
        im.save(buf, format="JPEG", quality=quality)
        data = buf.getvalue()
        if len(data) <= _REF_IMAGE_TARGET:
            return data
        quality -= 10
        if quality < 60:
            max_side = int(max_side * 0.75)
            quality = 88
    return data


def _image_tensor_to_base64(image_tensor, frame_index: int = 0) -> str:
    """ComfyUI IMAGE tensor（B,H,W,C float 0-1）→ 压缩 JPEG Base64（参考图不保真压缩，控请求体）。"""
    img = image_tensor[frame_index].cpu().numpy()
    img = np.clip(img * 255.0, 0, 255).astype(np.uint8)
    data = _compress_image(Image.fromarray(img))
    if len(data) > _MAX_IMAGE_BYTES:
        raise ValueError(f"图片超过 30MB 上限，请压缩后再试（{len(data) // (1024*1024)}MB）")
    return base64.b64encode(data).decode("ascii")


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


# ---------------------------------------------------------------- 执行台账

def _ledger(mode):
    """生成节点装饰器：成功/失败都写执行台账；失败原样抛出。

    mode 为 None 时按输入自动推断：有参考图/参考 URL 记 i2i，否则 t2i；
    也可传可调用对象 mode(node, kwargs) 返回模式字符串（VS 四模式合一用）。
    被装饰函数返回 dict 时（{"ui":..., "result":...} 或含额外键），除 ui/result 外的
    键并入台账记录（VS 尾帧图 URL/路径）。
    """
    def deco(fn):
        @functools.wraps(fn)
        def wrapper(self, prompt, **kwargs):
            if callable(mode):
                m = mode(self, kwargs)
            else:
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
                original = fn(self, prompt, **kwargs)  # 元组，或 {"ui": ..., "result": ...} 字典（生图预览 / VS 尾帧与计费信息）
                result = original.get("result") if isinstance(original, dict) else original
                task_id, url, path = result[0], result[1], result[2]
                if isinstance(original, dict):
                    # 额外键（VS model/has_video_ref 计费要素、尾帧图信息）先并入 kwargs，
                    # 供 base_record 计费与扩展字段透传；缓存键在 fn 执行前已算好，不受影响
                    kwargs.update({k: v for k, v in original.items() if k not in ("result", "ui")})
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


def _vs_output_config_inputs():
    """VS 专属输出配置：时长上限 30（2.0 系 15 由校验兜底）、分辨率/宽高比含 VS 档位。"""
    return {
        "duration": ("INT", {"default": 5, "min": 4, "max": 30, "step": 1,
                             "tooltip": "生成时长（秒）。2.0 / 2.0-fast / 2.0-mini 上限 15 秒，2.5 上限 30 秒（超范围由本地校验兜底）"}),
        "resolution": (["480P", "720P", "1080P", "2K", "4K"], {"default": "720P",
                       "tooltip": "输出分辨率。按参数表：2.0 系可选 480P/720P/1080P/2K/4K；2.5 系可选 480P/720P/2K/4K（更新记录提及 2.5 新增 1080P 直出，与参数表有出入，实现以参数表为准）"}),
        "aspect_ratio": (["21:9", "16:9", "4:3", "1:1", "3:4", "9:16", "adaptive"], {"default": "16:9",
                        "tooltip": "输出宽高比（adaptive=由模型决定）"}),
        "audio_generation": (ON_OFF, {"default": "Enabled", "tooltip": "音画同出"}),
        "seed": ("INT", {"default": -1, "min": -1, "max": 2147483647, "step": 1,
                         "tooltip": "随机种子；-1 或留空 = 不传（由模型随机）"}),
        "logo_add": (ON_OFF, {"default": "Disabled", "tooltip": "图标水印：Enabled 时传 OutputConfig.LogoAdd=Enabled，Disabled 不传"}),
        "high_bitrate": (ON_OFF, {"default": "Disabled", "tooltip": "高码率模式：Enabled 时 ExtInfo 注入 bitrate_mode=high"}),
        "return_last_frame": (ON_OFF, {"default": "Disabled", "tooltip": "返回尾帧图：Enabled 时 ExtInfo 注入 return_last_frame=true，并把尾帧图一并下载到本地（台账记录其 URL/路径）"}),
        "storage_mode": (STORAGE_MODES, {"default": "Temporary", "tooltip": "Temporary=临时存储(URL 限时有效) / Permanent=永久存储(可后续超分处理)"}),
        "use_cache": (ON_OFF, {"default": "Enabled",
                               "tooltip": "结果缓存：同参数（提示词/模型版本/种子/参考素材等）已成功过的任务直接复用本地产物，不调用腾讯云 API。需要新结果时改 Disabled 或修改任一参数"}),
        "media_name": ("STRING", {"default": "", "tooltip": "可选，输出文件名/备注"}),
        "filename": ("STRING", {"default": "", "tooltip": "可选，本地保存文件名（不含扩展名，留空自动命名；尾帧图自动加 _last_frame 后缀）"}),
        "region": ("STRING", {"default": DEFAULT_REGION, "tooltip": "腾讯云地域，如 ap-guangzhou"}),
        "endpoint": ("STRING", {"default": "", "tooltip": "API 地址，留空用 vod.tencentcloudapi.com（新版可用 gateway.vod-qcloud.com）"}),
        "input_region": ("STRING", {"default": "", "tooltip": "可选 InputRegion，素材在海外时填 oversea"}),
        "poll_interval": ("INT", {"default": 10, "min": 3, "max": 120, "step": 1, "tooltip": "任务轮询间隔（秒）"}),
        "timeout": ("INT", {"default": 1800, "min": 60, "max": 7200, "step": 60, "tooltip": "任务超时时间（秒），视频生成通常需数分钟"}),
    }


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
                data = _file_to_base64(path, _MAX_IMAGE_BYTES, f"{usage}图", _ALLOWED_IMAGE_EXTS, image=True)
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
        required = {"prompt": ("STRING", {"multiline": True, "default": "", "tooltip": "提示词；引用参考图用 @N 或「图N」（N 从 1 开始，BatchImagesNode 的 image0=第 1 张=@1），例如 @1=皇后、@2=祺贵人；仅支持 @N 序号引用（@名称 绑定是 PixVerse 专属能力，H3 不支持，写了会报错）"})}
        optional = dict(_cred_inputs())          # 凭据选填：留空读 tencent-vod-config.json
        optional.update(_output_config_inputs())
        optional["ref_images"] = ("IMAGE", {"tooltip": "参考图，支持批量（batch）：多张图请先合成 batch（如 Load Images / ImageBatch 节点），每帧一张，最多 9 张；帧序即编号（image0=第1张=@1）；也可用 ref_image_urls 传多个 URL"})
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
            data = _file_to_base64(path, _MAX_IMAGE_BYTES, "参考图", _ALLOWED_IMAGE_EXTS, image=True)
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

        # 素材配额：图≤9 / 视频≤3 / 音频≤3 / 总数≤12 / 音频不能单独 / Base64≤70MB
        _check_media_quota(file_infos, base64_total)

        # @N 引用展开：@1 / @图片1 → 图1（1 基，对应参考图第 N 张，越界报错）
        prompt = _expand_prompt_refs(prompt, sum(
            1 for f in file_infos
            if f.get("Category") == "Image" and f.get("Usage") == "Reference"))
        _validate_prompt_refs(prompt)  # @名称 绑定仅 PixVerse 支持，H3/VS 报错提示

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


def _vs_ledger_mode(node, kwargs):
    """VS 台账模式：有参考素材=r2v；只有首/尾帧=i2v；无素材=t2v（费用均按视频秒数计，与 H3 一致）。"""
    has_refs = (kwargs.get("ref_images") is not None
                or any((kwargs.get(k) or "").strip() for k in (
                    "ref_image_paths", "ref_image_urls", "ref_video_paths", "ref_video_urls",
                    "ref_audio_paths", "ref_audio_urls")))
    has_frame = (kwargs.get("first_frame") is not None or kwargs.get("last_frame") is not None
                 or any((kwargs.get(k) or "").strip() for k in (
                     "first_frame_path", "first_frame_url", "last_frame_path", "last_frame_url")))
    if has_refs:
        return "r2v"
    if has_frame:
        return "i2v"
    return "t2v"


class TencentVODVSVideoTask:
    """VS 视频生成（四模式合一）：文生视频 / 首帧 / 首尾帧 / 多模态参考（图片、视频、音频）。

    素材输入全可选，给什么素材就是什么模式：无素材=文生视频；只给首帧=首帧生视频；
    首帧+尾帧=首尾帧生视频；给了参考素材=多模态参考（可与首尾帧并存，文档未禁止）。
    含人脸素材直接引用 URL 会被服务端拒绝（ret:-4 提示 real person），需先经
    「VOD AIGC - 创建素材」注册为 asset，Url 传 asset://asset-xxx。
    素材上限：30 图 + 10 视频 + 10 音频，总数 ≤50。
    """

    _MODEL_VERSIONS = ["2.0", "2.0-fast", "2.0-mini", "2.5"]

    @classmethod
    def INPUT_TYPES(cls):
        required = {"prompt": ("STRING", {"multiline": True, "default": "",
                                           "tooltip": "提示词（必填）；引用参考图用 @N 或「图N」（N 从 1 开始，BatchImagesNode 的 image0=第 1 张=@1），例如 @1=皇后、@2=祺贵人；首尾帧用「首帧」「尾帧」描述；仅支持 @N 序号引用（@名称 绑定是 PixVerse 专属能力，VS 不支持，写了会报错）"})}
        optional = dict(_cred_inputs())          # 凭据选填：留空读 tencent-vod-config.json
        optional.update(_vs_output_config_inputs())
        optional["model_version"] = (cls._MODEL_VERSIONS, {"default": "2.5",
                                      "tooltip": "VS 模型版本：2.5 支持 4-30 秒/480P-4K；2.0 / 2.0-fast / 2.0-mini 支持 4-15 秒"})
        # 首帧 / 尾帧（IMAGE / 本地路径 / URL 三选一）
        optional["first_frame"] = ("IMAGE", {"tooltip": "首帧图（ComfyUI 图像，转 Base64 上传）"})
        optional["last_frame"] = ("IMAGE", {"tooltip": "尾帧图"})
        optional["first_frame_path"] = ("STRING", {"default": "", "tooltip": "首帧图本地路径（支持 ~/、input/xxx、output/xxx 或绝对路径；与 first_frame / first_frame_url 三选一）"})
        optional["last_frame_path"] = ("STRING", {"default": "", "tooltip": "尾帧图本地路径（与 last_frame / last_frame_url 三选一）"})
        optional["first_frame_url"] = ("STRING", {"default": "", "tooltip": "首帧图 URL（可匿名下载的图片直链；含人脸请用 asset://asset-xxx 素材引用）"})
        optional["last_frame_url"] = ("STRING", {"default": "", "tooltip": "尾帧图 URL（含人脸请用 asset://asset-xxx 素材引用）"})
        # 多模态参考（全可选，可与首/尾帧并存）
        optional["ref_images"] = ("IMAGE", {"tooltip": "参考图，支持批量（batch）：每帧一张，最多 30 张；也可用 ref_image_urls / ref_image_paths"})
        optional["ref_image_paths"] = ("STRING", {"multiline": True, "default": "", "tooltip": "本地参考图路径，每行一个（最多 30 张），支持 ~/、input/xxx、output/xxx 或绝对路径"})
        optional["ref_image_urls"] = ("STRING", {"multiline": True, "default": "", "tooltip": "参考图 URL，每行一个，最多 30 个。必须为可匿名下载的图片直链；含人脸素材请用 asset://asset-xxx（先经创建素材节点注册）"})
        optional["ref_video_paths"] = ("STRING", {"multiline": True, "default": "", "tooltip": "本地参考视频路径，每行一个（最多 10 段）"})
        optional["ref_video_urls"] = ("STRING", {"multiline": True, "default": "", "tooltip": "参考视频 URL，每行一个（最多 10 段）。必须为可匿名下载的视频直链（.mp4/.mov 等），不支持网页地址"})
        optional["ref_audio_paths"] = ("STRING", {"multiline": True, "default": "", "tooltip": "本地参考音频路径，每行一个（最多 10 段，不能单独输入）"})
        optional["ref_audio_urls"] = ("STRING", {"multiline": True, "default": "", "tooltip": "参考音频 URL，每行一个（最多 10 段）。必须为可匿名下载的音频直链（.mp3/.wav 等），不支持网页地址"})
        return {"required": required, "optional": optional}

    RETURN_TYPES = ("STRING", "STRING", "STRING")
    RETURN_NAMES = ("task_id", "video_url", "video_path")
    FUNCTION = "generate"
    CATEGORY = "Tencent VOD AIGC"
    OUTPUT_NODE = True

    @classmethod
    def IS_CHANGED(cls, **kwargs):
        return float("NaN")  # 每次都提交新任务，不缓存

    @_ledger(_vs_ledger_mode)  # 台账模式按素材自动推断 t2v / i2v / r2v（视频按秒计费）
    def generate(self, prompt, **kwargs):
        if not prompt.strip():
            raise ValueError("Prompt 不能为空")
        model_version = kwargs.get("model_version", "2.5")
        duration = int(kwargs.get("duration", 5))
        resolution = kwargs.get("resolution", "720P")
        _validate_vs_options(model_version, duration, resolution)

        file_infos = []
        ref_names = []  # 与 file_infos 同序的素材名（报错 content[N] 映射用）
        base64_total = 0

        # 首帧 / 尾帧（IMAGE / 本地路径 / URL 三选一，Usage=FirstFrame/LastFrame）
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
                ref_names.append(f"{'首帧' if usage == 'FirstFrame' else '尾帧'}图(IMAGE)")
            elif url:
                _validate_media_url(url, _ALLOWED_IMAGE_EXTS, f"{usage}图")
                file_infos.append({"Type": "Url", "Category": "Image", "Url": url, "Usage": usage})
                ref_names.append(f"{'首帧' if usage == 'FirstFrame' else '尾帧'}图({url[:40]})")
            elif path:
                data = _file_to_base64(path, _MAX_IMAGE_BYTES, f"{usage}图", _ALLOWED_IMAGE_EXTS, image=True)
                base64_total += len(data)
                if base64_total > _MAX_BASE64_TOTAL:
                    raise ValueError("Base64 素材总大小超过 70MB 上限")
                file_infos.append({"Type": "Base64", "Category": "Image", "Base64": data, "Usage": usage})
                ref_names.append(f"{'首帧' if usage == 'FirstFrame' else '尾帧'}图({os.path.basename(path)})")

        # 多模态参考（Usage=Reference）
        ref_images = kwargs.get("ref_images")
        if ref_images is not None:
            n = ref_images.shape[0]
            if n > 30:
                raise ValueError(f"参考图最多 30 张，当前 IMAGE 有 {n} 帧")
            for i in range(n):
                data = _image_tensor_to_base64(ref_images, i)
                base64_total += len(data)
                if base64_total > _MAX_BASE64_TOTAL:
                    raise ValueError("Base64 素材总大小超过 70MB 上限")
                file_infos.append({"Type": "Base64", "Category": "Image", "Base64": data, "Usage": "Reference"})
                ref_names.append(f"参考图第{i+1}帧(IMAGE)")
        for path in _parse_multiline(kwargs.get("ref_image_paths")):
            data = _file_to_base64(path, _MAX_IMAGE_BYTES, "参考图", _ALLOWED_IMAGE_EXTS, image=True)
            base64_total += len(data)
            if base64_total > _MAX_BASE64_TOTAL:
                raise ValueError("Base64 素材总大小超过 70MB 上限")
            file_infos.append({"Type": "Base64", "Category": "Image", "Base64": data, "Usage": "Reference"})
            ref_names.append(os.path.basename(path))
        for url in _parse_multiline(kwargs.get("ref_image_urls")):
            _validate_media_url(url, _ALLOWED_IMAGE_EXTS, "参考图")
            file_infos.append({"Type": "Url", "Category": "Image", "Url": url, "Usage": "Reference"})
            ref_names.append(url[:40])
        for path in _parse_multiline(kwargs.get("ref_video_paths")):
            data = _file_to_base64(path, _MAX_VIDEO_BYTES, "参考视频", _ALLOWED_VIDEO_EXTS)
            base64_total += len(data)
            if base64_total > _MAX_BASE64_TOTAL:
                raise ValueError("Base64 素材总大小超过 70MB 上限")
            file_infos.append({"Type": "Base64", "Category": "Video", "Base64": data, "Usage": "Reference"})
            ref_names.append(f"参考视频({os.path.basename(path)})")
        for url in _parse_multiline(kwargs.get("ref_video_urls")):
            _validate_media_url(url, _ALLOWED_VIDEO_EXTS, "参考视频")
            file_infos.append({"Type": "Url", "Category": "Video", "Url": url, "Usage": "Reference"})
            ref_names.append(f"参考视频({url[:40]})")
        for path in _parse_multiline(kwargs.get("ref_audio_paths")):
            data = _file_to_base64(path, _MAX_AUDIO_BYTES, "参考音频", _ALLOWED_AUDIO_EXTS)
            base64_total += len(data)
            if base64_total > _MAX_BASE64_TOTAL:
                raise ValueError("Base64 素材总大小超过 70MB 上限")
            file_infos.append({"Type": "Base64", "Category": "Audio", "Base64": data, "Usage": "Reference"})
            ref_names.append(f"参考音频({os.path.basename(path)})")
        for url in _parse_multiline(kwargs.get("ref_audio_urls")):
            _validate_media_url(url, _ALLOWED_AUDIO_EXTS, "参考音频")
            file_infos.append({"Type": "Url", "Category": "Audio", "Url": url, "Usage": "Reference"})
            ref_names.append(f"参考音频({url[:40]})")

        # 素材配额（VS 上限：图≤30 / 视频≤10 / 音频≤10 / 总数≤50 / 音频不能单独 / Base64≤70MB）
        _check_media_quota(file_infos, base64_total, max_images=30, max_videos=10,
                           max_audios=10, max_total=50)
        # 计费要素：是否含视频参考素材（VS 有参考视频时输入/输出两段计费，台账据此查表）
        has_video_ref = any(f.get("Category") == "Video" for f in file_infos)

        # @N 引用展开：@1 / @图片1 → 图1（1 基，对应参考图第 N 张，越界报错）
        prompt = _expand_prompt_refs(prompt, sum(
            1 for f in file_infos
            if f.get("Category") == "Image" and f.get("Usage") == "Reference"))
        _validate_prompt_refs(prompt)  # @名称 绑定仅 PixVerse 支持，H3/VS 报错提示

        # 种子 / 水印 / ExtInfo（未启用不传，保持 payload 干净）
        seed_raw = kwargs.get("seed", -1)
        seed = int(seed_raw) if seed_raw not in (None, "") else -1
        if seed < 0:
            seed = None  # -1 或空 = 不传（由模型随机）；0 是合法种子，照常传
        logo_add = "Enabled" if kwargs.get("logo_add", "Disabled") == "Enabled" else ""
        ext_info = _build_ext_info(
            bitrate_mode="high" if kwargs.get("high_bitrate", "Disabled") == "Enabled" else None,
            return_last_frame=kwargs.get("return_last_frame", "Disabled") == "Enabled")

        secret_id, secret_key, sub_app_id = _resolve_credentials(
            kwargs.get("secret_id"), kwargs.get("secret_key"), kwargs.get("sub_app_id"))
        region = kwargs.get("region") or ""
        endpoint = kwargs.get("endpoint") or ""
        payload = _build_payload(sub_app_id, prompt, "", kwargs, file_infos=file_infos or None,
                                 input_region=kwargs.get("input_region") or "",
                                 model_name="VS", model_version=model_version,
                                 seed=seed, logo_add=logo_add, ext_info=ext_info)
        _set_status(self, f"提交 VS {model_version} 视频任务…")
        response = _call_api(secret_id, secret_key, region, endpoint, "CreateAigcVideoTask", payload)
        task_id = response.get("TaskId")
        if not task_id:
            raise RuntimeError(f"未返回 TaskId（原始响应: {json.dumps(response, ensure_ascii=False)[:400]}）")
        try:
            result = _wait_for_task(secret_id, secret_key, region, endpoint, sub_app_id, task_id,
                                    kwargs.get("poll_interval", 10), kwargs.get("timeout", 1800),
                                    on_progress=lambda t: _set_status(self, t),
                                    task_label=f"VS {model_version} 视频生成中")
        except TaskError as _err:
            # 版权/人脸等任务级拒绝：把 content[N] 映射回素材名，便于定位是哪张素材被拦
            raise TaskError(_err.task_id, _annotate_content_refs(str(_err), ref_names)) from None
        last_frame_url = ""
        if kwargs.get("return_last_frame", "Disabled") == "Enabled":
            video_url, last_frame_url = _extract_video_and_lastframe(result["detail"])
            if not video_url:
                raise RuntimeError(f"VS 任务未返回视频输出 URL（原始响应: {json.dumps(result['detail'], ensure_ascii=False)[:400]}）")
            url = video_url
        else:
            url = result["urls"][0]
        _set_status(self, "下载视频…")
        path = _download_video(url, task_id, on_progress=lambda t: _set_status(self, t),
                               name_hint=kwargs.get("filename"))
        out = {"result": (task_id, url, path),
               "model": f"VS {model_version}", "has_video_ref": has_video_ref}
        if last_frame_url:
            _set_status(self, "下载尾帧图…")
            lf_path = _download_video(last_frame_url, task_id,
                                      on_progress=lambda t: _set_status(self, t),
                                      name_hint=(kwargs.get("filename") or "") + "_last_frame")
            out["last_frame_url"] = last_frame_url
            out["last_frame_path"] = lf_path
            _set_status(self, f"完成（尾帧图: {os.path.basename(lf_path)}）")
        else:
            _set_status(self, "完成")
        return out


class TencentVODAIGCCreateMaterial:
    """创建素材（CreateAigcMaterial）：URL → 素材注册 → 轮询 → 输出 asset://asset-xxx。

    VS 含人脸素材不能直接引用 URL（服务端 ret:-4 提示 real person），需先注册为素材，
    视频任务 FileInfos.Url 传 asset://asset-xxx。真人素材（IsRealPerson=True）需先完成
    活体认证（core.create_liveness_validate / describe_liveness_validate_result 获取
    GroupId），GroupId 必填。素材注册为异步任务（返回 TaskId → DescribeTaskDetail 轮询）。
    本节点仅接受 URL（http/https 直链）；本地文件需先上传到可访问的 URL。
    """

    _ASSET_TYPES = ["Image", "Video", "Audio"]

    @classmethod
    def INPUT_TYPES(cls):
        required = {
            "asset_type": (cls._ASSET_TYPES, {"default": "Image", "tooltip": "素材类型：Image / Video / Audio"}),
            "file_url": ("STRING", {"default": "", "tooltip": "素材 URL（http/https 可匿名下载直链）。本节点不做本地文件转 URL，本地文件请先上传到可访问的 URL"}),
            "asset_name": ("STRING", {"default": "", "tooltip": "素材名称（如「小熊」）"}),
        }
        optional = dict(_cred_inputs())          # 凭据选填：留空读 tencent-vod-config.json
        optional.update({
            "is_real_person": (ON_OFF, {"default": "Disabled", "tooltip": "真人素材（含人脸）需 Enabled，且 GroupId 必填（活体认证结果返回）"}),
            "group_id": ("STRING", {"default": "", "tooltip": "素材组 ID：is_real_person=Enabled 时必填（DescribeAigcLivenessValidateResult 返回的 GroupId）；Disabled 时可留空或随意指定"}),
            "group_name": ("STRING", {"default": "", "tooltip": "素材组名称（可选）"}),
            "group_description": ("STRING", {"default": "", "tooltip": "素材组描述（可选）"}),
            "region": ("STRING", {"default": DEFAULT_REGION, "tooltip": "腾讯云地域，如 ap-guangzhou"}),
            "endpoint": ("STRING", {"default": "", "tooltip": "API 地址，留空用 vod.tencentcloudapi.com"}),
            "poll_interval": ("INT", {"default": 10, "min": 3, "max": 120, "step": 1, "tooltip": "任务轮询间隔（秒）"}),
            "timeout": ("INT", {"default": 600, "min": 60, "max": 7200, "step": 60, "tooltip": "任务超时时间（秒）"}),
        })
        return {"required": required, "optional": optional}

    RETURN_TYPES = ("STRING", "STRING")
    RETURN_NAMES = ("task_id", "asset_id")
    FUNCTION = "create"
    CATEGORY = "Tencent VOD AIGC"
    OUTPUT_NODE = True

    @classmethod
    def IS_CHANGED(cls, **kwargs):
        return float("NaN")  # 素材注册是外部状态，不做结果缓存

    def create(self, asset_type, file_url, asset_name, **kwargs):
        if not file_url.strip():
            raise ValueError("file_url 不能为空")
        if not asset_name.strip():
            raise ValueError("asset_name 不能为空")
        is_real = kwargs.get("is_real_person", "Disabled") == "Enabled"
        secret_id, secret_key, sub_app_id = _resolve_credentials(
            kwargs.get("secret_id"), kwargs.get("secret_key"), kwargs.get("sub_app_id"))
        region = kwargs.get("region") or ""
        endpoint = kwargs.get("endpoint") or ""
        task_id = _create_material(secret_id, secret_key, region, endpoint, sub_app_id,
                                   file_url.strip(), asset_type, asset_name.strip(), is_real,
                                   group_id=kwargs.get("group_id") or "",
                                   group_name=kwargs.get("group_name") or "",
                                   group_description=kwargs.get("group_description") or "")
        _set_status(self, "素材注册中…")
        result = _wait_for_task(secret_id, secret_key, region, endpoint, sub_app_id, task_id,
                                kwargs.get("poll_interval", 10), kwargs.get("timeout", 600),
                                on_progress=lambda t: _set_status(self, t),
                                task_label="素材注册中", err_label="素材",
                                require_urls=False)
        asset_id = _extract_asset_id(result["detail"])
        if not asset_id:
            raise RuntimeError(f"素材任务成功但未返回 AssetId（原始响应: {json.dumps(result['detail'], ensure_ascii=False)[:400]}）")
        _set_status(self, f"完成 asset://{asset_id}")
        return (task_id, f"asset://{asset_id}")


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
            data = _file_to_base64(path, _MAX_IMAGE_BYTES, "参考图", _ALLOWED_IMAGE_EXTS, image=True)
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

    _MUSIC_MODELS = MUSIC_MODELS

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
    "TencentVODVSVideoTask": TencentVODVSVideoTask,
    "TencentVODAIGCCreateMaterial": TencentVODAIGCCreateMaterial,
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
    "TencentVODVSVideoTask": "VOD AIGC - VS 视频生成",
    "TencentVODAIGCCreateMaterial": "VOD AIGC - 创建素材",
    "TencentVODAIGCImageTask": "VOD AIGC - 文生图/图生图",
    "TencentVODAIGCMusicTask": "VOD AIGC - 音乐生成 (MPS)",
    "TencentVODAIGCQueryTask": "VOD AIGC - 查询任务",
    "TencentVODAIGCDownloadVideo": "VOD AIGC - 下载视频",
    "TencentVODAIGCViewHistory": "VOD AIGC - 查看执行台账",
}


_register_http_routes()  # 首次使用弹窗的凭据状态/保存接口（非 ComfyUI 环境自动跳过）
