"""腾讯云 VOD AIGC 无头核心（纯标准库，零第三方依赖）。

从本仓库 nodes.py (v1.14.1) 抽取的纯逻辑层：TC3-HMAC-SHA256 签名、call_api、
payload 构造（生视频 / 生图 / MPS 音乐三任务）、任务轮询、下载、结果缓存键与
执行台账、计价、素材校验（配额 / 扩展名 / 体积）。不依赖 ComfyUI / numpy / PIL /
torch；out_dir / ledger_path / config 路径全部参数化，供无头脚本与未来 SDK 化
（batch.py 迁移）复用。

协议：腾讯云 API v3（TC3-HMAC-SHA256 签名），接口 CreateAigcVideoTask /
CreateAigcImageTask / DescribeTaskDetail / CreateAigcAudioTask /
DescribeAigcAudioTask（对应《VOD AIGC服务接入指南》3.17 节 Hailuo/H3 生视频、
3.3.2 节 GEM/Jimeng 生图、3.14 节 GPT-Image2 生图；MPS CreateAigcAudioTask 音乐；
《VS模型接入使用指南》VS 生视频 + CreateAigcMaterial / DescribeAigcMaterial /
DeleteAigcMaterial 素材管理 + CreateAigcLivenessValidate /
DescribeAigcLivenessValidateResult 活体认证）。

接口命名参考管线侧 motion-comic-pipeline/generation/vod_aigc_core.py；行为以
本仓库 nodes.py 为准（空值过滤策略、报错文案、台账字段等均与节点端逐字一致）。
"""

import base64
import hashlib
import hmac
import json
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request

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

# MPS 音乐生成模型下拉（节点端 UI 亦引用此常量）
MUSIC_MODELS = ["GL 2.0", "GL 3.0-clip", "GL 3.0-pro",
                "MiniMaxMusic 2.0", "MiniMaxMusic 2.5", "MiniMaxMusic 2.6"]

_MAX_IMAGE_BYTES = 30 * 1024 * 1024      # 单张图片 ≤30MB（文档限制）
_MAX_VIDEO_BYTES = 50 * 1024 * 1024      # 单个视频 ≤50MB（文档限制）
_MAX_AUDIO_BYTES = 15 * 1024 * 1024      # 单个音频 ≤15MB（文档限制）
_MAX_BASE64_TOTAL = 10 * 1024 * 1024     # Base64 传参总大小 ≤10MB（腾讯云网关实测硬限制 RequestSizeLimitExceeded 10485760B；文档 70MB 与实际不符）
_MIN_BILLED_SECONDS = 5                  # 每次任务不足 5 秒按 5 秒计费

_ALLOWED_VIDEO_EXTS = (".mp4", ".mov")
_ALLOWED_IMAGE_EXTS = (".jpg", ".jpeg", ".png", ".webp", ".bmp")
_ALLOWED_AUDIO_EXTS = (".mp3", ".wav", ".m4a", ".aac")

_CONFIG_FILE = "tencent-vod-config.json"
_CRED_FILE_HINT = ("custom_nodes/tencent-vod-aigc/tencent-vod-config.json"
                   "（模板见同目录 tencent-vod-config.example.json）")

# 结果缓存键：影响输出的参数 / 参考素材引用（与节点端 nodes._cache_key 逐字一致）
_CACHE_KEY_PARAMS = ("duration", "resolution", "aspect_ratio", "audio_generation",
                     "storage_mode", "enhance_prompt", "media_name", "model",
                     "output_image_count", "output_format", "lyrics", "is_instrumental",
                     "model_version", "seed", "logo_add", "ext_info",
                     "high_bitrate", "return_last_frame", "scene_type")
_CACHE_KEY_REFS = ("ref_image_urls", "ref_image_paths", "ref_video_paths", "ref_video_urls",
                   "ref_audio_paths", "ref_audio_urls",
                   "first_frame_url", "first_frame_path", "last_frame_url", "last_frame_path",
                   "image_url", "image_path", "filename")


class TaskError(RuntimeError):
    """任务级错误：携带 task_id，供台账记录与后续排查。"""

    def __init__(self, task_id, message):
        super().__init__(message)
        self.task_id = task_id


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


def _sign_request(secret_id: str, secret_key: str, region: str, endpoint: str, action: str,
                  payload: dict, version=API_VERSION, service=SERVICE):
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


def call_api(secret_id: str, secret_key: str, region: str, endpoint: str, action: str,
             payload: dict, version=API_VERSION, service=SERVICE, timeout=60) -> dict:
    """调用腾讯云接口，返回 Response 对象；业务错误抛 RuntimeError。"""
    endpoint = (endpoint or DEFAULT_ENDPOINT).strip()
    endpoint = endpoint.replace("https://", "").replace("http://", "").rstrip("/")
    headers, body = _sign_request(secret_id, secret_key, region, endpoint, action, payload,
                                  version=version, service=service)
    req = urllib.request.Request(f"https://{endpoint}/", data=body, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
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


# ---------------------------------------------------------------- 配置与凭据


def load_config(config_path=None):
    """读取统一配置文件 tencent-vod-config.json，文件缺失/损坏返回空结构。

    config_path 为文件路径（None 返回空结构，供无配置调用方）。
    返回 {"secret_id", "secret_key", "sub_app_id",
          "prices": {分辨率: 单价}, "image_prices": {模型: 单价},
          "currency": 币种（默认 "cny"）, "model_price_tables": 模型计价表（原样透传）}。
    """
    empty = {"secret_id": "", "secret_key": "", "sub_app_id": "", "prices": {}, "image_prices": {},
             "currency": "cny", "model_price_tables": {}}
    if not config_path:
        return empty
    try:
        with open(config_path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except FileNotFoundError:
        return empty
    except Exception as e:
        print(f"[tencent-vod-aigc] {_CONFIG_FILE} 读取失败（忽略）: {e}")
        return empty
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
    result["currency"] = ((data.get("currency") or "cny").strip().lower() or "cny")
    tables = data.get("model_price_tables")
    result["model_price_tables"] = tables if isinstance(tables, dict) else {}
    return result


def resolve_credentials(cfg):
    """凭据解析与校验：cfg 为节点输入与配置文件合并后的结果，返回 (secret_id, secret_key, sub_app_id)。

    缺失 / 不完整 / SubAppId 非数字抛 ValueError（文案与节点端一致）。
    """
    sid = (cfg.get("secret_id") or "").strip()
    skey = (cfg.get("secret_key") or "").strip()
    sub = (cfg.get("sub_app_id") or "").strip()
    if not sid or not skey:
        raise ValueError("缺少腾讯云密钥：请在节点填写 SecretId / SecretKey，或配置 "
                         + _CRED_FILE_HINT)
    if not sub:
        raise ValueError("缺少 SubAppId：请在节点填写，或配置 "
                         + _CRED_FILE_HINT)
    if not sub.isdigit():
        raise ValueError(f"SubAppId 必须为纯数字，当前值: {sub}")
    return sid, skey, sub


def resolve_secret_pair(cfg):
    """凭据解析（仅密钥对）：供 MPS 等不使用 SubAppId 的服务（如音乐生成）。"""
    sid = (cfg.get("secret_id") or "").strip()
    skey = (cfg.get("secret_key") or "").strip()
    if not sid or not skey:
        raise ValueError("缺少腾讯云密钥：请在节点填写 SecretId / SecretKey，或配置 "
                         + _CRED_FILE_HINT)
    return sid, skey


def save_config_file(secret_id, secret_key, sub_app_id, prices=None, image_prices=None,
                     path=None) -> str:
    """校验并写入统一配置文件（凭据必填；单价选填，仅合并非空数值项），返回文件路径。

    prices = 视频单价（元/秒，按分辨率）；image_prices = 生图单价（元/张，按模型）。
    写入前合并已存在文件中的价格（不覆盖未涉及的项）。
    """
    sid, skey, sub = (secret_id or "").strip(), (secret_key or "").strip(), (sub_app_id or "").strip()
    if not sid or not skey:
        raise ValueError("SecretId 与 SecretKey 不能为空")
    if not sub.isdigit():
        raise ValueError(f"SubAppId 必须为纯数字，当前值: {sub}")
    path = path or os.path.join(os.path.dirname(os.path.abspath(__file__)), _CONFIG_FILE)
    existing = load_config(path) if os.path.exists(path) else {}

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


# ---------------------------------------------------------------- 素材工具


def resolve_media_path(path: str, input_dir=None, out_dir=None, assets_dir=None) -> str:
    """素材路径解析：~ 展开 + 绝对路径原样；input/、output/ 前缀解析到对应目录；其余按进程工作目录。

    assets_dir 为管线侧扩展（assets/ 前缀入库素材目录），缺省时不解析（保持节点行为）。
    """
    p = (path or "").strip()
    if not p:
        return p
    p = os.path.expanduser(p)  # 支持 ~/Downloads/xxx 这类 shell 习惯写法
    if os.path.isabs(p):
        return p
    if p.startswith("input/") and input_dir:
        return os.path.join(input_dir, p[len("input/"):])
    if p.startswith("output/") and out_dir:
        return os.path.join(out_dir, p[len("output/"):])
    if p.startswith("assets/") and assets_dir:
        return os.path.join(assets_dir, p[len("assets/"):])
    return p  # 兼容旧行为：相对进程 cwd


def file_to_base64(path: str, max_bytes: int, what: str, allowed_exts=None,
                   input_dir=None, out_dir=None, assets_dir=None) -> str:
    """本地素材 → Base64（≤max_bytes）；文件缺失 / 扩展名不允许 / 超限抛 ValueError。"""
    resolved = resolve_media_path(path, input_dir, out_dir, assets_dir)
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


def parse_multiline(text):
    """多行文本 → 去空行列表（列表/元组输入原样清洗，供 SDK 调用方传路径列表）。"""
    if not text:
        return []
    if isinstance(text, (list, tuple)):
        return [str(x).strip() for x in text if str(x).strip()]
    return [line.strip() for line in text.splitlines() if line.strip()]


def annotate_content_refs(message, names):
    """把腾讯报错里的 content[N] 索引映射为素材名（0 基同序）；无法映射保留原样。"""
    if not names:
        return message or ""
    def _repl(m):
        idx = int(m.group(1))
        if 0 <= idx < len(names):
            return f"content[{idx}]({names[idx]})"
        return m.group(0)
    return re.sub(r"content\[(\d+)\]", _repl, message or "")


# 引用 token 边界：@ 前不能是词字符（邮箱/URL 里的 @ 不算引用）；@N 后不能紧跟词字符
# （@1皇后 这类粘连不算合法序号引用）。\w 在 Python3 默认 Unicode 模式，覆盖 É/キャラ/퀸 等。
_WORD = r"[\w\u4e00-\u9fa5]"
_REF_TOKEN_RE = re.compile(rf"(?<!{_WORD})@(图片)?(\d+)(?!{_WORD})")
_NAME_TOKEN_RE = re.compile(rf"(?<!{_WORD})@(?!图片\d+(?!{_WORD}))([^\d\s@][\w\u4e00-\u9fa5]*)")  # @名称（非数字开头；完整 @图片N 兼容语法放行）
_GLUE_REF_RE = re.compile(rf"(?<!{_WORD})@(\d+[^\s\d@=][\w\u4e00-\u9fa5]*)")  # @1皇后 粘连（= 是合法分隔符）
_ESCAPED_AT = "\x00VODAT\x00"  # \@ 字面量占位符（用户写 \@ 表示普通 @ 文本）


def _expand_refs(s: str, ref_image_count: int) -> str:
    """@N / @图片N → 图N（1 基，词边界内；越界报错）。"""
    def _repl(m):
        n = int(m.group(2))
        if n < 1 or n > ref_image_count:
            raise ValueError(
                f"prompt 引用了 @{n}，但当前只有 {ref_image_count} 张参考图"
                f"（@N 从 1 开始，BatchImagesNode 的 image0 即第 1 张）")
        return f"图{n}"
    return _REF_TOKEN_RE.sub(_repl, s)


def _reject_name_refs(s: str) -> None:
    """拒绝残留的 @名称 / 格式错误引用（PixVerse 专属能力，H3/VS 不适用）。"""
    for m in _NAME_TOKEN_RE.finditer(s):
        name = m.group(1)
        raise ValueError(
            f"prompt 中的 @{name} 不是有效的素材引用：本节点（H3/VS）仅支持 @N 序号引用"
            f"（@1=第 1 张参考图，BatchImagesNode 的 image0 即 @1）。"
            f"「@名称」绑定是腾讯接入层 PixVerse 模型的能力，H3/VS 不适用"
            f"（如需给素材起名请直接描述，如「图1：皇后」；普通文本中的 @ 请写 \\@）")
    for m in _GLUE_REF_RE.finditer(s):
        raise ValueError(
            f"prompt 中的 {m.group(0)} 格式不正确：@N 序号引用后不能紧跟文字，"
            f"请加空格或标点（如「@1 皇后」）；本节点（H3/VS）仅支持 @N 序号引用")


def normalize_prompt_refs(prompt: str, ref_image_count: int) -> str:
    """完整素材引用解析（节点与 SDK 的统一入口）：
    1. 暂存 `\@` 字面量（普通文本中的 @，如邮箱）
    2. 词边界内转换 `@N` / `@图片N` → 「图N」（1 基，越界报错）
    3. 拒绝残留的 `@名称` 与格式错误引用（Unicode 名称，PixVerse 专属能力）
    4. 还原字面量 @
    """
    if not prompt:
        return ""
    s = re.sub(r"\\@", _ESCAPED_AT, prompt)
    s = _expand_refs(s, ref_image_count)
    _reject_name_refs(s)
    return s.replace(_ESCAPED_AT, "@")


def expand_prompt_refs(prompt: str, ref_image_count: int) -> str:
    """兼容入口：仅做 @N → 图N 转换（不拒绝名称引用；词边界语义见 normalize_prompt_refs）。"""
    if not prompt:
        return ""
    return _expand_refs(re.sub(r"\\@", _ESCAPED_AT, prompt), ref_image_count).replace(_ESCAPED_AT, "@")


def validate_prompt_refs(prompt: str) -> None:
    """兼容入口：仅拒绝残留的 @名称 / 格式错误引用（不转换 @N）。"""
    if not prompt:
        return
    _reject_name_refs(prompt)


def validate_media_url(url: str, allowed: tuple, what: str):
    """提交前校验素材 URL 扩展名：明确不在允许列表时本地报错（避免任务跑完才失败）。

    无扩展名/无法解析的 URL 不拦截（交由服务端判断）。
    asset:// 前缀为素材注册产物引用（VS 素材机制，asset://asset-xxx），无扩展名概念，直接放行。
    """
    url = (url or "").strip()
    if url.startswith("asset://"):
        return
    path = urllib.parse.urlparse(url).path
    ext = os.path.splitext(path)[1].lower()
    if ext and ext not in allowed:
        raise ValueError(f"{what} 扩展名 \"{ext}\" 不支持，允许: {', '.join(allowed)}（URL: {url[:80]}）")


def check_media_quota(file_infos, base64_total=None, max_images=9, max_videos=3,
                      max_audios=3, max_total=12):
    """素材配额校验（默认 H3 契约：图≤9 / 视频≤3 / 音频≤3 / 总数≤12 / 音频不能单独 / Base64≤70MB）。

    VS 模型传 max_images=30 / max_videos=10 / max_audios=10 / max_total=50。
    file_infos 为待提交素材清单（含 Category 字段），违规抛 ValueError（文案与节点端一致）。
    """
    n_images = sum(1 for f in file_infos if f.get("Category") == "Image")
    n_videos = sum(1 for f in file_infos if f.get("Category") == "Video")
    n_audios = sum(1 for f in file_infos if f.get("Category") == "Audio")
    if n_images > max_images:
        raise ValueError(f"参考图最多 {max_images} 张，当前 {n_images} 张")
    if n_videos > max_videos:
        raise ValueError(f"参考视频最多 {max_videos} 段，当前 {n_videos} 段")
    if n_audios > max_audios:
        raise ValueError(f"参考音频最多 {max_audios} 段，当前 {n_audios} 段")
    if n_images + n_videos + n_audios > max_total:
        raise ValueError(f"混合素材总数上限 {max_total} 个，当前 {n_images + n_videos + n_audios} 个")
    if n_audios > 0 and n_images == 0 and n_videos == 0:
        raise ValueError("音频不能单独作为参考输入，必须配图片或视频")
    if base64_total is not None and base64_total > _MAX_BASE64_TOTAL:
        raise ValueError("Base64 素材总大小超过 70MB 上限")


def _collect_path_media(paths, category, max_bytes, what, allowed_exts, input_dir, out_dir,
                        assets_dir=None, usage="Reference", file_infos=None, base64_total=0):
    """本地路径列表 → FileInfos（Base64，Usage=Reference）；返回 (file_infos, base64_total)。"""
    file_infos = file_infos if file_infos is not None else []
    for path in parse_multiline(paths):
        data = file_to_base64(path, max_bytes, what, allowed_exts, input_dir, out_dir, assets_dir)
        base64_total += len(data)
        if base64_total > _MAX_BASE64_TOTAL:
            raise ValueError("Base64 素材总大小超过 70MB 上限")
        file_infos.append({"Type": "Base64", "Category": category, "Base64": data, "Usage": usage})
    return file_infos, base64_total


# ---------------------------------------------------------------- 任务轮询与下载


def extract_task_result(detail: dict):
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


def extract_video_and_lastframe(detail: dict):
    """双产物解析（VS return_last_frame=true）：从任务查询结果中分离视频与尾帧图 URL。

    返回 (video_url, last_frame_url)：Output.FileInfos 中 UsageType=last_frame_url
    的项为尾帧图，其余（UsageType 为空）为生成的视频；任一缺失返回 None。
    video_url 恒为第一个非尾帧项，保持现有 extract_task_result urls[0] 的取视频语义。
    """
    task_dict = None
    for key, value in detail.items():
        if isinstance(value, dict) and re.search(r"(Aigc|SceneAigc)\w*Task$", key):
            task_dict = value
            break
    if task_dict is None:
        task_dict = detail
    output = task_dict.get("Output")
    if not isinstance(output, dict):
        return None, None
    video_url, last_frame_url = None, None
    for item in output.get("FileInfos") or []:
        if not isinstance(item, dict):
            continue
        url = item.get("FileUrl")
        if not isinstance(url, str) or not url:
            continue
        if item.get("UsageType") == "last_frame_url":
            last_frame_url = url
        elif video_url is None:
            video_url = url
    return video_url, last_frame_url


def extract_asset_id(detail: dict) -> str:
    """从任务查询响应中提取 CreateAigcMaterialTask.Output.AssetId（无则返回 ""）。

    素材注册任务成功后 Output 为 {"AssetId", "AssetUrl", "GroupId"}（无 FileInfos）。
    """
    for key, value in detail.items():
        if isinstance(value, dict) and re.search(r"(Aigc|SceneAigc)\w*Task$", key):
            output = value.get("Output")
            if isinstance(output, dict) and output.get("AssetId"):
                return str(output["AssetId"])
            return ""
    output = detail.get("Output")  # 平铺结构兜底（detail 即任务对象）
    if isinstance(output, dict) and output.get("AssetId"):
        return str(output["AssetId"])
    return ""


def wait_for_task(secret_id, secret_key, region, endpoint, sub_app_id, task_id,
                  poll_interval, timeout, on_progress=None, task_label="H3 生成中",
                  action="DescribeTaskDetail", err_label="H3",
                  version=API_VERSION, service=SERVICE, call_api_fn=None,
                  require_urls=True) -> dict:
    """轮询任务直到完成，返回 {"status", "urls", "detail"}。

    action 默认 VOD DescribeTaskDetail；MPS 音乐任务传 DescribeAigcAudioTask
    （查询仅 TaskId 一个参数，状态 DONE=成功 / FAIL=失败，错误文案前缀用 err_label）。
    call_api_fn 可注入（默认本模块 call_api），供调用方测试打桩或换传输层。
    require_urls=False 用于素材注册任务（CreateAigcMaterialTask.Output 为
    AssetId/AssetUrl 而非 FileInfos[].FileUrl，成功后无 URL 属正常，由调用方提取 AssetId）。
    """
    call_api_fn = call_api_fn or call_api
    deadline = time.time() + timeout
    started = time.time()
    while True:
        if action == "DescribeAigcAudioTask":
            query = {"TaskId": task_id}  # MPS 查询接口无 SubAppId 参数
        else:
            query = {"SubAppId": int(sub_app_id), "TaskId": task_id}
        response = call_api_fn(secret_id, secret_key, region, endpoint, action, query,
                               version=version, service=service)
        # 真实响应把任务详情平铺在 Response 顶层（部分文档描述为嵌套在 TaskDetail，两者都兼容）
        detail = response.get("TaskDetail") or response
        status, err_code, err_code_ext, message, urls = extract_task_result(detail)
        if status in ("SUCCESS", "FINISH", "DONE"):
            if not urls:
                if err_code or err_code_ext or message:
                    hint = ("（提示词或素材触发内容安全审核，请修改后重试）" if err_code_ext and "Violation" in err_code_ext else "")
                    raise TaskError(task_id, f"{err_label} 任务被拒绝（ErrCode={err_code} ErrCodeExt={err_code_ext or '-'} Message={message or '-'}）TaskId: {task_id} {hint}".rstrip())
                if require_urls:
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


def resolve_save_name(url: str, task_id: str, name_hint: str = "", out_dir=None) -> str:
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
    out_dir = out_dir or ""
    name = base
    counter = 1
    while os.path.exists(os.path.join(out_dir, name)):  # 重名去重（多图同 hint 场景）
        stem, e = os.path.splitext(base)
        name = f"{stem}_{counter}{e}"
        counter += 1
    return name


def download_file(url: str, task_id: str, out_dir: str, name_hint="", on_progress=None) -> str:
    """把生成的文件（视频/图片/音频）流式下载到 out_dir，返回本地路径。

    60s 超时 + 进度回调；失败时抛出包含可手动下载 URL 的错误，
    避免阻塞线程导致整个 ComfyUI 无法中断。
    """
    os.makedirs(out_dir, exist_ok=True)
    name = resolve_save_name(url, task_id, name_hint or "", out_dir)
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


# ---------------------------------------------------------------- payload 构造


def build_video_payload(sub_app_id, prompt, enhance_prompt, oc_values, file_infos=None,
                        input_region="", model_name="Hailuo", model_version="H3",
                        seed=None, logo_add="", ext_info=""):
    """构造 CreateAigcVideoTask 请求体（Hailuo / H3 或 VS）。

    oc_values 需含 storage_mode / duration / resolution / aspect_ratio / audio_generation，
    可选 media_name。model_name / model_version 默认 Hailuo / H3，VS 传 "VS"/版本；
    seed / logo_add 仅在非空时进 OutputConfig（Seed / LogoAdd）；
    ext_info 为调用方已双重转义的 JSON 字符串（见 build_ext_info），原样放顶层 ExtInfo；
    enhance_prompt 为空时不传（VS 无此参数），H3 调用方恒传非空，产出不变。
    """
    oc = {
        "StorageMode": oc_values["storage_mode"],
        "Duration": int(oc_values["duration"]),
        "Resolution": oc_values["resolution"],
        "AspectRatio": oc_values["aspect_ratio"],
        "AudioGeneration": oc_values["audio_generation"],
    }
    if oc_values.get("media_name"):
        oc["MediaName"] = oc_values["media_name"]
    if seed is not None:
        oc["Seed"] = int(seed)
    if logo_add:
        oc["LogoAdd"] = logo_add
    payload = {
        "SubAppId": int(sub_app_id),
        "ModelName": model_name,
        "ModelVersion": model_version,
        "Prompt": prompt,
        "OutputConfig": oc,
    }
    if enhance_prompt:
        payload["EnhancePrompt"] = enhance_prompt
    if ext_info:
        payload["ExtInfo"] = ext_info
    if file_infos:
        payload["FileInfos"] = file_infos
    if input_region:
        payload["InputRegion"] = input_region
    return payload


def build_3d_world_payload(sub_app_id, prompt, storage_mode="Temporary", file_infos=None,
                           input_region=""):
    """构造混元 3D 世界生成请求体。

    对应《VOD AIGC 服务接入指南》3.15：
    ModelName=Hunyuan、ModelVersion=3d_2.0、SceneType=3d_scene。
    文档未公开相机、输出格式等参数，因此这里只发送已文档化字段；StorageMode
    作为通用输出存储配置传入，方便生成结果落 VOD。
    """
    payload = {
        "SubAppId": int(sub_app_id),
        "ModelName": "Hunyuan",
        "ModelVersion": "3d_2.0",
        "SceneType": "3d_scene",
        "Prompt": prompt,
        "OutputConfig": {"StorageMode": storage_mode},
    }
    if file_infos:
        payload["FileInfos"] = file_infos
    if input_region:
        payload["InputRegion"] = input_region
    return payload


def build_image_payload(sub_app_id, prompt, model, oc_values, file_infos=None):
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


def build_music_payload(prompt, model, oc_values, file_infos=None):
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


def build_ext_info(bitrate_mode=None, return_last_frame=None) -> str:
    """构造 VS ExtInfo（双重转义 JSON 字符串，与文档示例逐字一致）。

    文档示例（高码率）：{"AdditionalParameters":"{\"bitrate_mode\":\"high\"}"}；
    返回尾帧：{"AdditionalParameters":"{\"return_last_frame\":true}"}。
    未启用任何参数时返回 ""（调用方不传 ExtInfo）。
    """
    additional = {}
    if bitrate_mode:
        additional["bitrate_mode"] = str(bitrate_mode)
    if return_last_frame is not None:
        additional["return_last_frame"] = bool(return_last_frame)
    if not additional:
        return ""
    return json.dumps({"AdditionalParameters": json.dumps(additional, separators=(",", ":"))},
                      separators=(",", ":"))


def validate_vs_options(model_version, duration, resolution):
    """VS 模型参数校验：时长 / 分辨率按版本限制（违规抛 ValueError）。

    - 2.0 / 2.0-fast / 2.0-mini：时长 4-15 秒，分辨率 480P/720P/1080P/2K/4K
    - 2.5：时长 4-30 秒，分辨率 480P/720P/2K/4K（按参数表；更新记录提及 2.5 新增
      1080P 直出，与参数表有出入，以参数表为准）
    duration 传 -1 表示由模型自动决定（文档允许，节点层不暴露）。
    """
    mv = (model_version or "").strip()
    if mv == "2.5":
        max_duration, allowed_res = 30, ("480P", "720P", "2K", "4K")
    elif mv in ("2.0", "2.0-fast", "2.0-mini"):
        max_duration, allowed_res = 15, ("480P", "720P", "1080P", "2K", "4K")
    else:
        raise ValueError(f"未知 VS ModelVersion: {mv}（可选 2.0 / 2.0-fast / 2.0-mini / 2.5）")
    d = int(duration)
    if d != -1 and not (4 <= d <= max_duration):
        raise ValueError(f"VS {mv} 输出时长需在 4-{max_duration} 秒，当前 {d} 秒")
    if resolution and resolution not in allowed_res:
        raise ValueError(f"VS {mv} 不支持分辨率 {resolution}，可选: {', '.join(allowed_res)}")


# ---------------------------------------------------------------- 计价


def price_for(resolution: str, cfg=None) -> float:
    """视频单价（元/秒，按分辨率）；未配置返回 0。cfg 为 load_config 的结果（None 视为空）。"""
    try:
        return float((cfg or {}).get("prices", {}).get(resolution, 0.0))
    except (TypeError, ValueError):
        return 0.0


def _table_rate(table, key: str, resolution: str) -> float:
    """从计价表子结构取 {分辨率: 单价} 的单档价格；结构缺失/数值非法返回 0。"""
    try:
        return float(((table or {}).get(key) or {}).get(resolution) or 0)
    except (TypeError, ValueError, AttributeError):
        return 0.0


def video_price_for(model_name, model_version, resolution, has_video_ref, cfg=None) -> float:
    """视频单价（元/秒，按模型计价表）；未配置返回 0。cfg 为 load_config 的结果。

    VS（model_price_tables.VS.versions.<version>.<currency>）计费规则：
    - 无参考视频：no_video_ref[res]（单段单价）
    - 有参考视频：with_video_ref.input[res] + with_video_ref.output[res]（输入/输出两段求和）
    model_name 接受 "VS" 或 "VS 2.5"（含版本时按空格切分）；其他模型名/空回退旧
    prices[res]（H3 等旧模型兼容）。币种严格按 cfg.currency（默认 cny）取——版本、
    分辨率或币种缺失返回 0，不回退旧表、不跨币种混用。
    """
    name = (model_name or "").strip()
    ver = (model_version or "").strip()
    if " " in name:
        name, _, inlined = name.partition(" ")
        ver = ver or inlined.strip()
    if name != "VS":
        return price_for(resolution, cfg)
    versions = (((cfg or {}).get("model_price_tables") or {}).get("VS") or {}).get("versions") or {}
    rates = versions.get(ver)
    if not rates:
        return 0.0
    cur = ((cfg or {}).get("currency") or "cny").strip().lower() or "cny"
    table = rates.get(cur)
    if not table:
        return 0.0
    if has_video_ref:
        wvr = table.get("with_video_ref") or {}
        return _table_rate(wvr, "input", resolution) + _table_rate(wvr, "output", resolution)
    return _table_rate(table, "no_video_ref", resolution)


def image_price_for(model: str, cfg=None) -> float:
    """生图单价（元/张，按模型）；未配置返回 0。cfg 为 load_config 的结果（None 视为空）。

    不同模型对应不同计费项（如即梦→SI、OG→GPT-Image2 计费），键为模型下拉全名。
    """
    try:
        return float((cfg or {}).get("image_prices", {}).get(model or "", 0.0))
    except (TypeError, ValueError):
        return 0.0


def estimate_cost(resolution: str, duration: int, cfg=None, model_name="",
                  model_version="", has_video_ref=False) -> tuple:
    """按计费规则估算费用：秒数 = max(时长, 5)，费用 = 秒数 × 单价（元）。

    model_name / model_version / has_video_ref 供 VS 计价表（model_price_tables）使用；
    旧调用（H3 等）不传时单价走 prices[res]，行为与旧版完全一致。
    """
    seconds_billed = max(int(duration or 0), _MIN_BILLED_SECONDS)
    rate = video_price_for(model_name, model_version, resolution, has_video_ref, cfg)
    return seconds_billed, round(seconds_billed * rate, 4)


# ---------------------------------------------------------------- 素材与活体认证 API（VS）

# CreateAigcMaterial / DescribeAigcMaterial / DeleteAigcMaterial /
# CreateAigcLivenessValidate / DescribeAigcLivenessValidateResult（同步或异步提交）。
# 素材注册（CreateAigcMaterial）为异步任务：提交返回 TaskId，轮询复用 wait_for_task
# （DescribeTaskDetail，require_urls=False），成功后用 extract_asset_id 取 AssetId。


def create_material(secret_id, secret_key, region, endpoint, sub_app_id, file_url,
                    asset_type, asset_name, is_real_person, group_id="", group_name="",
                    group_description="", session_id="", session_context="",
                    tasks_priority=None, ext_info="", call_api_fn=None) -> str:
    """提交 CreateAigcMaterial 素材注册任务，返回 TaskId。

    file_url 为素材 URL（http/https 直链）；asset_type 取值 Image/Video/Audio；
    is_real_person 为 bool（或 "True"/"False" 字符串），真人素材必须提供 group_id
    （活体认证 DescribeAigcLivenessValidateResult 返回）；非真人 group_id 可留空。
    轮询由调用方走 wait_for_task（require_urls=False）+ extract_asset_id。
    call_api_fn 可注入（默认本模块 call_api），供测试打桩。
    """
    real = str(is_real_person).strip().lower() == "true"
    if real and not (group_id or "").strip():
        raise ValueError("真人素材（is_real_person=True）必须提供 GroupId（先完成活体认证获取）")
    payload = {
        "SubAppId": int(sub_app_id),
        "FileInfo": {"Type": "Url", "Url": file_url},
        "AssetType": asset_type,
        "AssetName": asset_name,
        "IsRealPerson": "True" if real else "False",
    }
    if group_id:
        payload["GroupId"] = group_id
    if group_name:
        payload["GroupName"] = group_name
    if group_description:
        payload["GroupDescription"] = group_description
    if session_id:
        payload["SessionId"] = session_id
    if session_context:
        payload["SessionContext"] = session_context
    if tasks_priority is not None:
        payload["TasksPriority"] = int(tasks_priority)
    if ext_info:
        payload["ExtInfo"] = ext_info
    response = (call_api_fn or call_api)(secret_id, secret_key, region, endpoint,
                                         "CreateAigcMaterial", payload)
    task_id = response.get("TaskId")
    if not task_id:
        raise RuntimeError(f"未返回 TaskId（原始响应: {json.dumps(response, ensure_ascii=False)[:400]}）")
    return task_id


def describe_material(secret_id, secret_key, region, endpoint, sub_app_id, asset_id,
                      call_api_fn=None) -> dict:
    """查询素材（同步接口 DescribeAigcMaterial），返回 Response。"""
    if not (asset_id or "").strip():
        raise ValueError("查询素材需提供 AssetId")
    return (call_api_fn or call_api)(secret_id, secret_key, region, endpoint,
                                     "DescribeAigcMaterial",
                                     {"SubAppId": int(sub_app_id), "AssetId": asset_id.strip()})


def delete_material(secret_id, secret_key, region, endpoint, sub_app_id, asset_id="",
                    group_id="", call_api_fn=None) -> dict:
    """删除素材（同步接口 DeleteAigcMaterial），asset_id 或 group_id 至少一个。

    传 group_id 时删除该组全部素材。
    """
    asset_id, group_id = (asset_id or "").strip(), (group_id or "").strip()
    if not asset_id and not group_id:
        raise ValueError("删除素材需提供 AssetId 或 GroupId 至少一个")
    payload = {"SubAppId": int(sub_app_id)}
    if asset_id:
        payload["AssetId"] = asset_id
    if group_id:
        payload["GroupId"] = group_id
    return (call_api_fn or call_api)(secret_id, secret_key, region, endpoint,
                                     "DeleteAigcMaterial", payload)


def create_liveness_validate(secret_id, secret_key, region, endpoint, sub_app_id,
                             callback_url="", call_api_fn=None) -> dict:
    """发起活体认证（真人素材前置）：返回 {"H5Link", "LivenessToken"}。

    H5Link 需用户在浏览器完成人脸认证；完成后用 LivenessToken 查询结果。
    """
    payload = {"SubAppId": int(sub_app_id)}
    if callback_url:
        payload["CallbackUrl"] = callback_url
    return (call_api_fn or call_api)(secret_id, secret_key, region, endpoint,
                                     "CreateAigcLivenessValidate", payload)


def describe_liveness_validate_result(secret_id, secret_key, region, endpoint,
                                      sub_app_id, liveness_token, call_api_fn=None) -> dict:
    """查询活体认证结果：返回含 GroupId 的 Response（认证通过后用于创建真人素材）。"""
    if not (liveness_token or "").strip():
        raise ValueError("查询活体认证结果需提供 LivenessToken")
    return (call_api_fn or call_api)(secret_id, secret_key, region, endpoint,
                                     "DescribeAigcLivenessValidateResult",
                                     {"SubAppId": int(sub_app_id),
                                      "LivenessToken": liveness_token.strip()})


# ---------------------------------------------------------------- 缓存与台账


def cache_key(mode: str, prompt: str, kwargs: dict) -> str:
    """结果缓存键：mode + prompt + 全部影响输出的参数 + 参考素材引用（原样字符串）。

    同键且产物仍在 = 命中，直接复用本地文件（不调腾讯云 API）。
    素材路径字符串原样进键（同一文件两种写法 = 两个键）；ref_images 张量按内容指纹进键。
    """
    key = {"mode": mode, "prompt": prompt or ""}
    for k in _CACHE_KEY_PARAMS:
        if k in kwargs:
            key[k] = kwargs[k]
    refs = []
    for k in _CACHE_KEY_REFS:
        v = kwargs.get(k)
        if v:
            refs.append(f"{k}={v}")
    for image_key in ("ref_images", "image"):
        img = kwargs.get(image_key)
        if img is not None:
            try:
                refs.append(f"{image_key}={hashlib.sha256(img.cpu().numpy().tobytes()).hexdigest()[:16]}")
            except Exception:
                refs.append(f"{image_key}=<unhashable>")
    key["refs"] = "|".join(refs)
    return hashlib.sha256(json.dumps(key, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()


def base_record(mode: str, prompt: str, kwargs: dict, task_id="", url="", path="",
                error="", cache_key="", cfg=None, view_url="", cached=False):
    """构造台账记录：含计费要素（时长/分辨率/模型/张数），便于成本审计。

    视频按秒计费（元/秒 × 计费秒数）；生图按张计费（元/张 × 张数，按模型）；
    音乐生成（t2a）不计秒不计费，费用恒为 0。
    cfg 为 load_config 结果（计价用，由调用方注入以便离线复用）；
    view_url 为调用方计算的 ComfyUI /view 链接（无头环境传空）。
    """
    if mode in ("t2i", "i2i"):
        model = kwargs.get("model") or ("Hunyuan 3d_2.0" if mode in ("t23d", "i23d") else "")
        image_count = int(kwargs.get("output_image_count") or 1)
        seconds_billed, estimated_cost = 0, round(image_count * image_price_for(model, cfg), 4)
    elif mode == "t2a":
        model, image_count = kwargs.get("model") or "", 0
        seconds_billed, estimated_cost = 0, 0.0  # 音乐生成不计秒不计费
    elif mode in ("t23d", "i23d"):
        model, image_count = kwargs.get("model") or "Hunyuan 3d_2.0", 0
        seconds_billed, estimated_cost = 0, 0.0  # 3D 世界按次计费，当前配置未维护单次价格
    else:
        # 视频按秒计费：H3 等旧模型走 prices[res]；VS 走 model_price_tables
        # （kwargs.model 形如 "VS 2.5"，has_video_ref 由 VS 节点注入）
        model = kwargs.get("model") or ""
        image_count = 0
        seconds_billed, estimated_cost = estimate_cost(
            kwargs.get("resolution") or "", kwargs.get("duration") or 0, cfg,
            model_name=model, model_version=kwargs.get("model_version") or "",
            has_video_ref=bool(kwargs.get("has_video_ref")))
    record = {
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
        "view_url": view_url,
        "seconds_billed": seconds_billed,
        "estimated_cost": estimated_cost,
        "cache_key": cache_key,
        "cached": cached,
        "error": (error or "")[:500],
    }
    # VS 尾帧图等扩展字段透传（仅调用方传入时出现；H3 等路径无这些键，记录结构不变）
    for k in ("last_frame_url", "last_frame_path"):
        if kwargs.get(k):
            record[k] = kwargs[k]
    return record


def append_history(record: dict, ledger_path: str):
    """把一条执行记录追加到台账 JSONL（成功/失败都记，只追加不覆盖）。

    台账写入失败不影响生成主流程（仅打印告警）。
    """
    try:
        if not ledger_path:
            return
        os.makedirs(os.path.dirname(os.path.abspath(ledger_path)), exist_ok=True)
        record.setdefault("time", time.strftime("%Y-%m-%dT%H:%M:%S%z"))
        with open(ledger_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
    except Exception as e:  # 台账写入失败不影响生成主流程
        print(f"[tencent-vod-aigc] 执行台账写入失败: {e}")


def find_cached_record(cache_key: str, ledger_path: str):
    """台账查重：返回同缓存键最近一次成功且产物文件仍在的记录，否则 None。"""
    try:
        if not ledger_path or not os.path.isfile(ledger_path):
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


# ---------------------------------------------------------------- 高级编排（SDK 入口）


def run_image_task(cfg, *, prompt, model, resolution, aspect_ratio, storage_mode,
                   out_dir, ledger_path, input_dir=None, assets_dir=None, filename="",
                   output_image_count=1, output_format="", ref_paths=None,
                   use_cache="Enabled", region="", endpoint="", poll_interval=10,
                   timeout=600, on_progress=None):
    """文生图/图生图任务（CreateAigcImageTask）：缓存判定 → 提交 → 轮询 → 下载 → 台账。

    ref_paths 非空时按图生图（mode=i2i）处理（FileInfos 仅 Type + Base64，无 Category/Usage）。
    返回 {"task_id", "urls", "paths", "cache_key", "cached", "cost", "seconds_billed",
    "record"}；缓存命中时 task_id/urls/paths 复用台账记录（零 API 调用、费用记 0）。
    """
    mode = "i2i" if ref_paths else "t2i"
    kwargs = {
        "model": model, "resolution": resolution, "aspect_ratio": aspect_ratio,
        "storage_mode": storage_mode, "output_image_count": output_image_count,
        "output_format": output_format, "filename": filename,
        "ref_image_paths": ref_paths or [], "use_cache": use_cache,
    }
    ck = cache_key(mode, prompt, kwargs)

    if use_cache != "Disabled":
        hit = find_cached_record(ck, ledger_path)
        if hit is not None:
            rec = base_record(mode, prompt, kwargs, hit.get("task_id", ""),
                              hit.get("video_url", ""), hit.get("video_path", ""),
                              cache_key=ck, cfg=cfg, cached=True)
            append_history(rec, ledger_path)
            return {"task_id": hit.get("task_id", ""), "urls": (hit.get("video_url") or "").splitlines(),
                    "paths": (hit.get("video_path") or "").splitlines(), "cache_key": ck,
                    "cached": True, "cost": 0.0, "seconds_billed": 0, "record": rec}

    secret_id, secret_key, sub_app_id = resolve_credentials(cfg)
    try:
        file_infos = []
        if ref_paths:
            for path in parse_multiline(ref_paths):
                data = file_to_base64(path, _MAX_IMAGE_BYTES, "参考图", _ALLOWED_IMAGE_EXTS,
                                      input_dir, out_dir, assets_dir)
                file_infos.append({"Type": "Base64", "Base64": data})
        payload = build_image_payload(sub_app_id, prompt, model, kwargs, file_infos or None)
        if on_progress:
            on_progress(f"提交生图任务: model={model} prompt={prompt[:40]!r}…")
        response = call_api(secret_id, secret_key, region, endpoint, "CreateAigcImageTask", payload)
        task_id = response.get("TaskId")
        if not task_id:
            raise RuntimeError(f"未返回 TaskId（原始响应: {json.dumps(response, ensure_ascii=False)[:400]}）")
        result = wait_for_task(secret_id, secret_key, region, endpoint, sub_app_id, task_id,
                               poll_interval, timeout, on_progress=on_progress, task_label="生图生成中")
        urls = result["urls"]
        if not urls:
            raise RuntimeError("生图任务未返回输出文件 URL")
        paths = [download_file(u, task_id, out_dir, name_hint=filename or "", on_progress=on_progress)
                 for u in urls]
    except Exception as e:
        append_history(base_record(mode, prompt, kwargs, task_id=getattr(e, "task_id", ""),
                                   error=str(e), cache_key=ck, cfg=cfg), ledger_path)
        raise
    rec = base_record(mode, prompt, kwargs, task_id, "\n".join(urls), "\n".join(paths),
                      cache_key=ck, cfg=cfg)
    append_history(rec, ledger_path)
    return {"task_id": task_id, "urls": urls, "paths": paths, "cache_key": ck,
            "cached": False, "cost": rec["estimated_cost"], "seconds_billed": 0, "record": rec}


def run_video_task(cfg, *, mode, prompt, duration, resolution, aspect_ratio,
                   audio_generation, storage_mode, enhance_prompt, out_dir, ledger_path,
                   input_dir=None, assets_dir=None, media_name="", filename="",
                   first_frame_path="", last_frame_path="", ref_image_paths=None,
                   ref_video_paths=None, ref_audio_paths=None, use_cache="Enabled",
                   region="", endpoint="", poll_interval=10, timeout=1800,
                   on_progress=None):
    """H3 生视频任务（t2v/i2v/r2v）：缓存判定 → 提交 → 轮询 → 下载 → 台账。

    素材一律走本地路径输入（first_frame_path / last_frame_path / ref_*_paths）。
    返回 {"task_id", "url", "path", "cache_key", "cached", "cost", "seconds_billed",
    "record"}；缓存命中时复用台账产物（零 API 调用、费用记 0）。

    互斥校验（管线侧 POC 实测）：H3 拒绝「reference 场景混用 first_frame/last_frame」
    （Usage=FirstFrame/LastFrame 与 Usage=Reference 不能同时出现，ErrCode=70000）——
    本地报错防浪费 API 费；r2v 模式的首帧图请走 ref_image_paths（Usage=Reference）。
    """
    has_frame = bool((first_frame_path or "").strip() or (last_frame_path or "").strip())
    has_refs = bool(ref_image_paths or ref_video_paths or ref_audio_paths)
    if has_frame and has_refs:
        raise ValueError(
            "素材用法冲突：first_frame/last_frame（Usage=FirstFrame/LastFrame）不能与 "
            "ref_image/ref_video/ref_audio（Usage=Reference）混用（H3 ErrCode=70000）。"
            "r2v 模式请把首帧图放入 ref_image_paths")
    kwargs = {
        "duration": duration, "resolution": resolution, "aspect_ratio": aspect_ratio,
        "audio_generation": audio_generation, "storage_mode": storage_mode,
        "enhance_prompt": enhance_prompt, "media_name": media_name, "filename": filename,
        "first_frame_path": first_frame_path, "last_frame_path": last_frame_path,
        "ref_image_paths": ref_image_paths or [], "ref_video_paths": ref_video_paths or [],
        "ref_audio_paths": ref_audio_paths or [], "use_cache": use_cache,
    }
    # 素材引用归一化：@N → 图N（r2v 按参考图顺序），@名称拒绝；归一化结果参与缓存键。
    ref_image_count = len(parse_multiline(ref_image_paths))
    prompt = normalize_prompt_refs(prompt, ref_image_count)
    ck = cache_key(mode, prompt, kwargs)

    if use_cache != "Disabled":
        hit = find_cached_record(ck, ledger_path)
        if hit is not None:
            rec = base_record(mode, prompt, kwargs, hit.get("task_id", ""),
                              hit.get("video_url", ""), hit.get("video_path", ""),
                              cache_key=ck, cfg=cfg, cached=True)
            append_history(rec, ledger_path)
            return {"task_id": hit.get("task_id", ""), "url": hit.get("video_url", ""),
                    "path": hit.get("video_path", ""), "cache_key": ck, "cached": True,
                    "cost": 0.0, "seconds_billed": 0, "record": rec}

    secret_id, secret_key, sub_app_id = resolve_credentials(cfg)
    try:
        file_infos, base64_total = [], 0

        # 首帧 / 末帧图（本地路径，Usage=FirstFrame/LastFrame）
        for key, usage in (("first_frame_path", "FirstFrame"), ("last_frame_path", "LastFrame")):
            path = (kwargs.get(key) or "").strip()
            if path:
                data = file_to_base64(path, _MAX_IMAGE_BYTES, f"{usage}图", _ALLOWED_IMAGE_EXTS,
                                      input_dir, out_dir, assets_dir)
                base64_total += len(data)
                file_infos.append({"Type": "Base64", "Category": "Image", "Base64": data, "Usage": usage})

        # 参考图 / 视频 / 音频（本地路径，Usage=Reference）
        file_infos, base64_total = _collect_path_media(ref_image_paths, "Image", _MAX_IMAGE_BYTES,
                                                       "参考图", _ALLOWED_IMAGE_EXTS, input_dir,
                                                       out_dir, assets_dir, file_infos=file_infos,
                                                       base64_total=base64_total)
        file_infos, base64_total = _collect_path_media(ref_video_paths, "Video", _MAX_VIDEO_BYTES,
                                                       "参考视频", _ALLOWED_VIDEO_EXTS, input_dir,
                                                       out_dir, assets_dir, file_infos=file_infos,
                                                       base64_total=base64_total)
        file_infos, base64_total = _collect_path_media(ref_audio_paths, "Audio", _MAX_AUDIO_BYTES,
                                                       "参考音频", _ALLOWED_AUDIO_EXTS, input_dir,
                                                       out_dir, assets_dir, file_infos=file_infos,
                                                       base64_total=base64_total)

        # 素材配额（图≤9 / 视频≤3 / 音频≤3 / 总数≤12 / 音频不能单独 / Base64≤70MB）
        check_media_quota(file_infos, base64_total)
        if mode in ("i2v", "r2v") and not any(f.get("Category") == "Image" for f in file_infos):
            raise ValueError(f"模式 {mode} 需要至少一张图（首帧/末帧/参考图）")

        payload = build_video_payload(sub_app_id, prompt, enhance_prompt, kwargs,
                                      file_infos or None)
        if on_progress:
            on_progress(f"提交 {mode} 视频任务: {resolution}/{duration}s prompt={prompt[:40]!r}…")
        response = call_api(secret_id, secret_key, region, endpoint, "CreateAigcVideoTask", payload)
        task_id = response.get("TaskId")
        if not task_id:
            raise RuntimeError(f"未返回 TaskId（原始响应: {json.dumps(response, ensure_ascii=False)[:400]}）")
        result = wait_for_task(secret_id, secret_key, region, endpoint, sub_app_id, task_id,
                               poll_interval, timeout, on_progress=on_progress)
        url = result["urls"][0]
        path = download_file(url, task_id, out_dir, name_hint=filename or "", on_progress=on_progress)
    except Exception as e:
        append_history(base_record(mode, prompt, kwargs, task_id=getattr(e, "task_id", ""),
                                   error=str(e), cache_key=ck, cfg=cfg), ledger_path)
        raise
    rec = base_record(mode, prompt, kwargs, task_id, url, path, cache_key=ck, cfg=cfg)
    append_history(rec, ledger_path)
    return {"task_id": task_id, "url": url, "path": path, "cache_key": ck, "cached": False,
            "cost": rec["estimated_cost"], "seconds_billed": rec["seconds_billed"], "record": rec}
