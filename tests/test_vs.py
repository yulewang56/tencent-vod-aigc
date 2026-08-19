"""VS 模型接入测试（《VS模型接入使用指南》）：payload 四模式 / ExtInfo 双重转义 /
配额 30/10/10/50 / 时长分辨率按版本校验 / asset:// 素材引用 / 素材节点 / 双产物解析 /
素材与活体 API 打桩。自包含风格，stub 不联网；H3 行为零改动（H3 测试在 test_nodes.py）。
"""
import json
import os
import sys
import types

# ---- stub ComfyUI/numpy/PIL dependencies (not needed at import time) ----
comfy = types.ModuleType("comfy")
folder_paths = types.ModuleType("comfy.folder_paths")
folder_paths.get_output_directory = lambda: "/tmp/comfy_output"
folder_paths.get_input_directory = lambda: "/tmp/comfy_input"
comfy.folder_paths = folder_paths
sys.modules["comfy"] = comfy
sys.modules["comfy.folder_paths"] = folder_paths
sys.modules["numpy"] = types.ModuleType("numpy")
pil = types.ModuleType("PIL")
pil.Image = types.SimpleNamespace()
sys.modules["PIL"] = pil

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))  # 仓库根目录
import nodes

failures = []

def check(name, cond, detail=""):
    if cond:
        print(f"  PASS  {name}")
    else:
        failures.append(name)
        print(f"  FAIL  {name}  {detail}")

OC = {"storage_mode": "Temporary", "duration": 5, "resolution": "1080P",
      "aspect_ratio": "16:9", "audio_generation": "Enabled", "media_name": ""}


# ---- 1. H3 payload 逐字节一致（硬约束：build_video_payload 默认参数下产出不变）----
h3_old = {
    "SubAppId": 1500044236,
    "ModelName": "Hailuo",
    "ModelVersion": "H3",
    "Prompt": "hello world",
    "EnhancePrompt": "Disabled",
    "OutputConfig": {
        "StorageMode": "Temporary",
        "Duration": 5,
        "Resolution": "1080P",
        "AspectRatio": "16:9",
        "AudioGeneration": "Enabled",
        "MediaName": "test",
    },
    "FileInfos": [{"Type": "Url", "Category": "Image", "Url": "https://x/1.png", "Usage": "FirstFrame"}],
    "InputRegion": "oversea",
}
oc_h3 = dict(OC)
oc_h3["media_name"] = "test"
h3_new = nodes._build_payload("1500044236", "hello world", "Disabled", oc_h3,
                              file_infos=[{"Type": "Url", "Category": "Image", "Url": "https://x/1.png", "Usage": "FirstFrame"}],
                              input_region="oversea")
check("h3: payload byte-identical (dict)",
      h3_new == h3_old and json.dumps(h3_new, ensure_ascii=False, sort_keys=True)
      == json.dumps(h3_old, ensure_ascii=False, sort_keys=True), str(h3_new))
oc_h3b = dict(OC)
h3_plain = nodes._build_payload("1", "p", "Enabled", oc_h3b)
check("h3: EnhancePrompt/OutputConfig shape unchanged",
      h3_plain["EnhancePrompt"] == "Enabled" and "Seed" not in h3_plain["OutputConfig"]
      and "LogoAdd" not in h3_plain["OutputConfig"] and "ExtInfo" not in h3_plain)

# ---- 2. VS payload 四模式构造 ----
vs_base = {"model_version": "2.5", "duration": 8, "resolution": "720P", "aspect_ratio": "adaptive",
           "audio_generation": "Enabled", "storage_mode": "Temporary", "media_name": ""}

# 2.1 文生视频：无素材，无 EnhancePrompt，ExtInfo/Seed/LogoAdd 不传
p_t2v = nodes._build_payload("1500044236", "一只猫咪在阳光下玩耍", "", vs_base,
                             model_name="VS", model_version="2.5")
check("vs t2v: ModelName/ModelVersion", p_t2v["ModelName"] == "VS" and p_t2v["ModelVersion"] == "2.5"
      and "FileInfos" not in p_t2v and "EnhancePrompt" not in p_t2v, str(p_t2v))
check("vs t2v: adaptive passthrough + no optional fields",
      p_t2v["OutputConfig"]["AspectRatio"] == "adaptive"
      and set(p_t2v["OutputConfig"]) == {"StorageMode", "Duration", "Resolution", "AspectRatio", "AudioGeneration"},
      str(p_t2v["OutputConfig"]))

# 2.2 首帧（Usage=FirstFrame）
p_ff = nodes._build_payload("123456", "他在散步", "", vs_base,
                            file_infos=[{"Type": "Url", "Category": "Image", "Url": "https://qq.com/1.png",
                                         "Usage": "FirstFrame"}],
                            model_name="VS", model_version="2.0")
check("vs firstframe: Usage=FirstFrame",
      p_ff["FileInfos"] == [{"Type": "Url", "Category": "Image", "Url": "https://qq.com/1.png", "Usage": "FirstFrame"}],
      str(p_ff["FileInfos"]))

# 2.3 首尾帧（FirstFrame + LastFrame，文档 2.1.3 ④）
p_fflf = nodes._build_payload("123456", "他在散步", "", vs_base,
                              file_infos=[
                                  {"Type": "Url", "Category": "Image", "Url": "https://qq.com/1.png", "Usage": "FirstFrame"},
                                  {"Type": "Url", "Category": "Image", "Url": "https://qq.com/2.png", "Usage": "LastFrame"}],
                              model_name="VS", model_version="2.0")
check("vs first+last frame",
      [f["Usage"] for f in p_fflf["FileInfos"]] == ["FirstFrame", "LastFrame"], str(p_fflf["FileInfos"]))

# 2.4 多模态参考（Usage=Reference，可与首尾帧并存——文档未禁止）
p_ref = nodes._build_payload("123456", "他在散步", "", vs_base,
                             file_infos=[
                                 {"Type": "Url", "Category": "Image", "Url": "asset://asset-abc", "Usage": "Reference"},
                                 {"Type": "Url", "Category": "Image", "Url": "https://qq.com/1.png", "Usage": "FirstFrame"},
                                 {"Type": "Url", "Category": "Video", "Url": "https://qq.com/v.mp4", "Usage": "Reference"},
                                 {"Type": "Url", "Category": "Audio", "Url": "https://qq.com/a.mp3", "Usage": "Reference"}],
                             model_name="VS", model_version="2.0")
check("vs reference: frames+refs coexist",
      [f["Usage"] for f in p_ref["FileInfos"]] == ["Reference", "FirstFrame", "Reference", "Reference"]
      and [f["Category"] for f in p_ref["FileInfos"]] == ["Image", "Image", "Video", "Audio"], str(p_ref["FileInfos"]))

# ---- 3. ExtInfo 双重转义（文档示例逐字一致）----
e_high = nodes._build_ext_info(bitrate_mode="high")
check("ext: bitrate_mode double-escaped",
      e_high == '{"AdditionalParameters":"{\\"bitrate_mode\\":\\"high\\"}"}', repr(e_high))
e_lf = nodes._build_ext_info(return_last_frame=True)
check("ext: return_last_frame double-escaped",
      e_lf == '{"AdditionalParameters":"{\\"return_last_frame\\":true}"}', repr(e_lf))
check("ext: both off -> empty", nodes._build_ext_info() == "" and nodes._build_ext_info(bitrate_mode="") == "")
# 服务端视角：ExtInfo 反序列化两层后拿到真实参数
outer = json.loads(e_high)
check("ext: parse outer", set(outer) == {"AdditionalParameters"}
      and json.loads(outer["AdditionalParameters"]) == {"bitrate_mode": "high"})
# wire JSON 片段与文档请求示例一致（"ExtInfo": "{\"AdditionalParameters\":\"{\\\"bitrate_mode\\\":\\\"high\\\"}\"}"）
p_wire = nodes._build_payload("123456", "他在散步", "", vs_base, model_name="VS", model_version="2.0",
                              ext_info=e_high)
wire = json.dumps(p_wire, ensure_ascii=False)
def esc(s):
    return json.dumps(s)[1:-1]
check("ext: wire fragment matches doc", '"ExtInfo": "%s"' % esc(e_high) in wire, wire)

# ---- 4. seed / logo_add 注入与省略 ----
p_seed = nodes._build_payload("1", "p", "", vs_base, model_name="VS", model_version="2.5",
                              seed=42, logo_add="Enabled")
check("vs: seed/logo_add injected",
      p_seed["OutputConfig"]["Seed"] == 42 and p_seed["OutputConfig"]["LogoAdd"] == "Enabled")
p_seed0 = nodes._build_payload("1", "p", "", vs_base, model_name="VS", model_version="2.5",
                               seed=0)
check("vs: seed=0 is a valid seed (passed)", p_seed0["OutputConfig"]["Seed"] == 0)
p_noseed = nodes._build_payload("1", "p", "", vs_base, model_name="VS", model_version="2.5",
                                seed=None, logo_add="")
check("vs: seed/logo_add omitted when empty",
      "Seed" not in p_noseed["OutputConfig"] and "LogoAdd" not in p_noseed["OutputConfig"])

# ---- 5. 素材配额：VS 30/10/10/50 vs H3 9/3/3/12 ----
def mk(n, cat):
    return [{"Category": cat} for _ in range(n)]
check("quota vs: 30 images ok", nodes._check_media_quota(mk(30, "Image"), 0,
                                                          max_images=30, max_videos=10, max_audios=10, max_total=50) is None)
try:
    nodes._check_media_quota(mk(31, "Image"), 0, max_images=30, max_videos=10, max_audios=10, max_total=50)
    check("quota vs: 31 images rejected", False)
except ValueError as e:
    check("quota vs: 31 images rejected", "最多 30 张" in str(e), str(e))
try:
    nodes._check_media_quota(mk(11, "Video"), 0, max_images=30, max_videos=10, max_audios=10, max_total=50)
    check("quota vs: 11 videos rejected", False)
except ValueError as e:
    check("quota vs: 11 videos rejected", "最多 10 段" in str(e), str(e))
try:
    nodes._check_media_quota(mk(11, "Audio"), 0, max_images=30, max_videos=10, max_audios=10, max_total=50)
    check("quota vs: 11 audios rejected", False)
except ValueError as e:
    check("quota vs: 11 audios rejected", "最多 10 段" in str(e), str(e))
try:
    nodes._check_media_quota(mk(30, "Image") + mk(10, "Video") + mk(11, "Audio"), 0,
                             max_images=30, max_videos=10, max_audios=10, max_total=50)
    check("quota vs: 51 total rejected", False)
except ValueError as e:
    # 11 音频先击穿单类上限（每类 ≤10，总数 50=30+10+10 与单类上限一致）
    check("quota vs: 51 total rejected (audio cap fires first)", "最多 10 段" in str(e), str(e))
check("quota vs: exactly 30+10+10=50 ok",
      nodes._check_media_quota(mk(30, "Image") + mk(10, "Video") + mk(10, "Audio"), 0,
                               max_images=30, max_videos=10, max_audios=10, max_total=50) is None)
# max_total 独立生效：总上限低于单类之和时按总数拦截
try:
    nodes._check_media_quota(mk(20, "Image") + mk(10, "Video") + mk(10, "Audio"), 0,
                             max_images=30, max_videos=10, max_audios=10, max_total=30)
    check("quota vs: max_total=30 fires on 40 total", False)
except ValueError as e:
    check("quota vs: max_total=30 fires on 40 total", "上限 30 个" in str(e), str(e))
try:
    nodes._check_media_quota(mk(1, "Audio"), 0, max_images=30, max_videos=10, max_audios=10, max_total=50)
    check("quota vs: audio-only rejected", False)
except ValueError as e:
    check("quota vs: audio-only rejected", "不能单独" in str(e), str(e))
# H3 契约不受影响：默认参数仍是 9/3/3/12（总数 12 低于单类之和 15，按总数拦截）
try:
    nodes._check_media_quota(mk(10, "Image"), 0)
    check("quota h3: 10 images rejected", False)
except ValueError as e:
    check("quota h3: 10 images rejected", "最多 9 张" in str(e), str(e))
try:
    nodes._check_media_quota(mk(7, "Image") + mk(3, "Video") + mk(3, "Audio"), 0)
    check("quota h3: 13 total rejected", False)
except ValueError as e:
    check("quota h3: 13 total rejected", "上限 12 个" in str(e), str(e))

# ---- 6. 时长 / 分辨率按版本校验 ----
nodes._validate_vs_options("2.5", 16, "4K")     # 2.5: 16s 合法
nodes._validate_vs_options("2.0", 4, "480P")    # 2.0: 下限
nodes._validate_vs_options("2.0-fast", 15, "720P")
nodes._validate_vs_options("2.0-mini", -1, "1080P")  # -1 = 模型决定
try:
    nodes._validate_vs_options("2.0", 16, "720P")
    check("vs opt: 2.0+16s rejected", False)
except ValueError as e:
    check("vs opt: 2.0+16s rejected", "4-15 秒" in str(e) and "16" in str(e), str(e))
try:
    nodes._validate_vs_options("2.5", 31, "720P")
    check("vs opt: 2.5+31s rejected", False)
except ValueError as e:
    check("vs opt: 2.5+31s rejected", "4-30 秒" in str(e), str(e))
try:
    nodes._validate_vs_options("2.5", 5, "1080P")
    check("vs opt: 2.5+1080P rejected (参数表为准)", False)
except ValueError as e:
    check("vs opt: 2.5+1080P rejected (参数表为准)", "1080P" in str(e) and "480P, 720P, 2K, 4K" in str(e), str(e))
nodes._validate_vs_options("2.0", 5, "1080P")   # 2.0 系支持 1080P
nodes._validate_vs_options("2.5", 5, "4K")
try:
    nodes._validate_vs_options("2.7", 5, "720P")
    check("vs opt: unknown version rejected", False)
except ValueError as e:
    check("vs opt: unknown version rejected", "未知 VS ModelVersion" in str(e), str(e))

# ---- 7. asset:// URL 放行（素材引用，不校验扩展名）----
nodes._validate_media_url("asset://asset-20260811154248-gl654", nodes._ALLOWED_IMAGE_EXTS, "首帧图")
nodes._validate_media_url("asset://asset-abc.123", nodes._ALLOWED_VIDEO_EXTS, "参考视频")
check("vs: asset:// urls pass", True)
try:
    nodes._validate_media_url("https://x.com/a.mp4", nodes._ALLOWED_IMAGE_EXTS, "参考图")
    check("vs: .mp4 as image still rejected", False)
except ValueError:
    check("vs: .mp4 as image still rejected", True)

# ---- 8. 素材 API：create_material payload / extract_asset_id ----
mat_calls = []
def fake_mat(sid, sk, reg, ep, action, payload, version="", service=""):
    mat_calls.append((action, payload))
    return {"TaskId": "123456-CreateAigcMaterial-6ae386ft"}
old_mat_call = nodes._call_api
nodes._call_api = fake_mat
tid = nodes._create_material("s", "k", "ap-guangzhou", "vod.tencentcloudapi.com",
                             "1500044236", "https://x/1.png", "Image", "小熊", False)
check("material: submits and returns TaskId", tid == "123456-CreateAigcMaterial-6ae386ft")
action, payload = mat_calls[-1]
check("material: payload shape (doc 4.1.1)", action == "CreateAigcMaterial"
      and payload == {"SubAppId": 1500044236,
                      "FileInfo": {"Type": "Url", "Url": "https://x/1.png"},
                      "AssetType": "Image", "AssetName": "小熊", "IsRealPerson": "False"},
      str(payload))
tid2 = nodes._create_material("s", "k", "r", "e", "1", "https://x/2.png", "Video", "v", "True",
                              group_id="group-abc", group_name="真人", group_description="库")
check("material: real person + group fields",
      mat_calls[-1][1]["IsRealPerson"] == "True"
      and mat_calls[-1][1]["GroupId"] == "group-abc"
      and mat_calls[-1][1]["GroupName"] == "真人" and mat_calls[-1][1]["GroupDescription"] == "库")
try:
    nodes._create_material("s", "k", "r", "e", "1", "https://x/3.png", "Image", "p", True)
    check("material: real person without group rejected", False)
except ValueError as e:
    check("material: real person without group rejected", "GroupId" in str(e), str(e))
nodes._call_api = old_mat_call

mat_detail = {"TaskType": "CreateAigcMaterial", "Status": "FINISH",
              "CreateAigcMaterialTask": {"Status": "FINISH", "ErrCode": 0, "Message": "",
                  "Input": {"AssetName": "小熊", "IsRealPerson": "True"},
                  "Output": {"AssetId": "asset-20260811154248-gl654", "AssetUrl": "url",
                             "GroupId": "group-abc"}}}
check("material: extract AssetId",
      nodes._extract_asset_id(mat_detail) == "asset-20260811154248-gl654")
check("material: extract AssetId absent -> empty",
      nodes._extract_asset_id({"TaskType": "AigcVideoTask", "AigcVideoTask": {"Status": "FINISH"}}) == "")
check("material: extract AssetId flat fallback",
      nodes._extract_asset_id({"Status": "FINISH", "Output": {"AssetId": "asset-x"}}) == "asset-x")

# 素材任务轮询：require_urls=False 时成功无 URL 不报错（Output 无 FileInfos[].FileUrl）
mat_poll = []
def fake_mat_poll(sid, sk, reg, ep, action, payload, version="", service=""):
    mat_poll.append(action)
    return {"TaskType": "CreateAigcMaterial", "Status": "FINISH",
            "CreateAigcMaterialTask": {"Status": "FINISH", "ErrCode": 0, "Message": "",
                "Output": {"AssetId": "asset-x", "AssetUrl": "url", "GroupId": "g"}}}
old_call = nodes._call_api
nodes._call_api = fake_mat_poll
try:
    r_mat = nodes._wait_for_task("s", "k", "r", "e", "1", "t1", 3, 60,
                                 task_label="素材注册中", err_label="素材", require_urls=False)
    check("material: poll succeeds without urls (require_urls=False)",
          r_mat["status"] == "FINISH" and nodes._extract_asset_id(r_mat["detail"]) == "asset-x")
    try:
        nodes._wait_for_task("s", "k", "r", "e", "1", "t1", 3, 60, require_urls=True)
        check("material: require_urls=True still raises", False)
    except nodes.TaskError as e:
        check("material: require_urls=True still raises", "未找到输出文件 URL" in str(e), str(e))
finally:
    nodes._call_api = old_call

# 素材任务失败路径
def fake_mat_fail(sid, sk, reg, ep, action, payload, version="", service=""):
    return {"TaskType": "CreateAigcMaterial", "Status": "FAIL",
            "CreateAigcMaterialTask": {"Status": "FAIL", "ErrCode": 1000, "Message": "素材注册失败"}}
nodes._call_api = fake_mat_fail
try:
    nodes._wait_for_task("s", "k", "r", "e", "1", "t-fail", 3, 60,
                         task_label="素材注册中", err_label="素材", require_urls=False)
    check("material: fail path raises", False)
except nodes.TaskError as e:
    check("material: fail path raises TaskError with 素材 prefix",
          "素材 任务失败" in str(e) and e.task_id == "t-fail", str(e))

# ---- 9. describe / delete / liveness API 打桩 ----
sync_calls = []
def fake_sync(sid, sk, reg, ep, action, payload, version="", service=""):
    sync_calls.append((action, payload))
    return {"RequestId": "r1"}
from vod_aigc_core import describe_material as _describe_material, \
    delete_material as _delete_material, \
    create_liveness_validate as _create_liveness_validate, \
    describe_liveness_validate_result as _describe_liveness_validate_result
_describe_material("s", "k", "r", "e", "1500044236", "asset-xxx", call_api_fn=fake_sync)
check("material: DescribeAigcMaterial payload",
      sync_calls[-1] == ("DescribeAigcMaterial", {"SubAppId": 1500044236, "AssetId": "asset-xxx"}),
      str(sync_calls[-1]))
_delete_material("s", "k", "r", "e", "1500044236", asset_id="asset-xxx", call_api_fn=fake_sync)
check("material: DeleteAigcMaterial by asset",
      sync_calls[-1] == ("DeleteAigcMaterial", {"SubAppId": 1500044236, "AssetId": "asset-xxx"}), str(sync_calls[-1]))
_delete_material("s", "k", "r", "e", "1500044236", group_id="group-abc", call_api_fn=fake_sync)
check("material: DeleteAigcMaterial by group",
      sync_calls[-1] == ("DeleteAigcMaterial", {"SubAppId": 1500044236, "GroupId": "group-abc"}), str(sync_calls[-1]))
try:
    _delete_material("s", "k", "r", "e", "1", call_api_fn=fake_sync)
    check("material: delete both empty rejected", False)
except ValueError as e:
    check("material: delete both empty rejected", "AssetId 或 GroupId" in str(e), str(e))
resp_lv = _create_liveness_validate("s", "k", "r", "e", "1500044236", call_api_fn=fake_sync)
check("liveness: CreateAigcLivenessValidate payload",
      sync_calls[-1] == ("CreateAigcLivenessValidate", {"SubAppId": 1500044236}), str(sync_calls[-1]))
_create_liveness_validate("s", "k", "r", "e", "1", callback_url="https://x/cb", call_api_fn=fake_sync)
check("liveness: callback_url included",
      sync_calls[-1][1]["CallbackUrl"] == "https://x/cb", str(sync_calls[-1]))
_describe_liveness_validate_result("s", "k", "r", "e", "1500044236", "2****6B", call_api_fn=fake_sync)
check("liveness: DescribeAigcLivenessValidateResult payload",
      sync_calls[-1] == ("DescribeAigcLivenessValidateResult",
                         {"SubAppId": 1500044236, "LivenessToken": "2****6B"}), str(sync_calls[-1]))

# ---- 10. 双产物解析（return_last_frame=true）----
vs_out = {"TaskType": "AigcVideoTask", "Status": "FINISH",
          "AigcVideoTask": {"Status": "FINISH", "Output": {"FileInfos": [
              {"FileUrl": "http://cdn/aigcVideoGenFile.mp4", "UsageType": ""},
              {"FileUrl": "http://cdn/aigcVideoGenFile.png", "UsageType": "last_frame_url"}]}}}
v_url, lf_url = nodes._extract_video_and_lastframe(vs_out)
check("vs dual: video + last_frame split",
      v_url == "http://cdn/aigcVideoGenFile.mp4" and lf_url == "http://cdn/aigcVideoGenFile.png",
      (v_url, lf_url))
v2, lf2 = nodes._extract_video_and_lastframe({"TaskType": "AigcVideoTask",
    "AigcVideoTask": {"Status": "FINISH", "Output": {"FileInfos": [
        {"FileUrl": "http://cdn/v.mp4", "UsageType": ""}]}}})
check("vs dual: no last_frame -> None", v2 == "http://cdn/v.mp4" and lf2 is None)
v3, lf3 = nodes._extract_video_and_lastframe({"TaskType": "AigcVideoTask",
    "AigcVideoTask": {"Status": "FINISH", "Output": {"FileInfos": [
        {"FileUrl": "http://cdn/last.png", "UsageType": "last_frame_url"}]}}})
check("vs dual: only last_frame -> video None", v3 is None and lf3 == "http://cdn/last.png")
st, _, _, _, urls = nodes._extract_task_result(vs_out)
check("vs dual: urls[0] keeps video-first semantics", urls[0] == "http://cdn/aigcVideoGenFile.mp4", str(urls))

# ---- 11. VS 节点全流程（return_last_frame + seed + logo_add + high_bitrate）----
hpath = "/tmp/comfy_output/vod_aigc/execution_history.jsonl"
if os.path.exists(hpath):
    os.remove(hpath)
captured = {}
def fake_vs_api(sid, sk, reg, ep, action, payload, version="", service=""):
    if action == "CreateAigcVideoTask":
        captured["payload"] = payload
        return {"TaskId": "1500044236-AigcVideoTask-vs000001t"}
    return {"TaskType": "AigcVideoTask", "Status": "FINISH",
            "AigcVideoTask": {"Status": "FINISH", "Output": {"FileInfos": [
                {"FileUrl": "https://cdn/vs.mp4", "UsageType": ""},
                {"FileUrl": "https://cdn/last.png", "UsageType": "last_frame_url"}]}}}
nodes._call_api = fake_vs_api
dl_seen = []
def fake_dl(url, task_id, on_progress=None, name_hint=None):
    dl_seen.append((url, name_hint))
    return f"/tmp/comfy_output/vod_aigc/{name_hint or 'x'}_{task_id[-8:]}.mp4"
orig_dl = nodes._download_video
nodes._download_video = fake_dl
vs_params = {"secret_id": "x", "secret_key": "y", "sub_app_id": "1500044236",
             "model_version": "2.5", "duration": 8, "resolution": "720P", "aspect_ratio": "adaptive",
             "audio_generation": "Enabled", "seed": 42, "logo_add": "Enabled",
             "high_bitrate": "Enabled", "return_last_frame": "Enabled",
             "storage_mode": "Temporary", "use_cache": "Disabled", "media_name": "",
             "filename": "vs_clip", "region": "ap-guangzhou", "endpoint": "",
             "input_region": "", "poll_interval": 3, "timeout": 60,
             "first_frame_url": "asset://asset-abc123"}
try:
    res = nodes.TencentVODVSVideoTask().generate("猫咪奔跑", **vs_params)
    check("vs node: returns 3-tuple in result",
          res["result"][0] == "1500044236-AigcVideoTask-vs000001t"
          and res["result"][1] == "https://cdn/vs.mp4", res)
    check("vs node: last frame downloaded with _last_frame hint",
          dl_seen == [("https://cdn/vs.mp4", "vs_clip"),
                      ("https://cdn/last.png", "vs_clip_last_frame")], str(dl_seen))
    check("vs node: last frame url/path in return",
          res["last_frame_url"] == "https://cdn/last.png"
          and "_last_frame" in res["last_frame_path"], str(res))
    p = captured["payload"]
    check("vs node: payload VS/2.5/adaptive/Seed/LogoAdd/ExtInfo",
          p["ModelName"] == "VS" and p["ModelVersion"] == "2.5"
          and p["OutputConfig"]["AspectRatio"] == "adaptive"
          and p["OutputConfig"]["Seed"] == 42 and p["OutputConfig"]["LogoAdd"] == "Enabled"
          and json.loads(p["ExtInfo"])["AdditionalParameters"] == '{"bitrate_mode":"high","return_last_frame":true}',
          json.dumps(p, ensure_ascii=False))
    check("vs node: asset:// first frame passes through",
          p["FileInfos"] == [{"Type": "Url", "Category": "Image",
                              "Url": "asset://asset-abc123", "Usage": "FirstFrame"}], str(p["FileInfos"]))
    check("vs node: no EnhancePrompt for VS", "EnhancePrompt" not in p)
    rec = json.loads(open(hpath).read().strip().splitlines()[-1])
    check("vs node: ledger mode i2v + last_frame fields",
          rec["mode"] == "i2v" and rec["duration"] == 8 and rec["resolution"] == "720P"
          and rec["last_frame_url"] == "https://cdn/last.png"
          and "_last_frame" in rec["last_frame_path"], json.dumps(rec, ensure_ascii=False))
    check("vs node: ledger billed per second", rec["seconds_billed"] == 8 and rec["estimated_cost"] == 0.0)
finally:
    nodes._download_video = orig_dl

# 模式推断：无素材=t2v / 只有首帧=i2v / 有参考=r2v
check("vs mode: t2v", nodes._vs_ledger_mode(None, {"prompt": "p"}) == "t2v")
check("vs mode: i2v (frame only)", nodes._vs_ledger_mode(None, {"first_frame_url": "https://x/1.png"}) == "i2v")
check("vs mode: r2v (refs)", nodes._vs_ledger_mode(None, {"ref_image_urls": "https://x/1.png"}) == "r2v")
check("vs mode: r2v wins over frames", nodes._vs_ledger_mode(
    None, {"ref_image_urls": "https://x/1.png", "first_frame_url": "https://x/2.png"}) == "r2v")

# 模式校验：首尾帧同时给 IMAGE/URL/路径三选一冲突
try:
    nodes.TencentVODVSVideoTask().generate("p", **{**vs_params, "use_cache": "Disabled",
                                                   "first_frame_url": "https://x/1.png",
                                                   "first_frame_path": "/tmp/a.png"})
    check("vs node: first_frame url+path conflict", False)
except ValueError as e:
    check("vs node: first_frame url+path conflict", "同时提供" in str(e), str(e))

# 校验兜底：2.0 系 16 秒在节点层被拒（validate_vs_options 兜底）
try:
    nodes.TencentVODVSVideoTask().generate("p", **{**vs_params, "use_cache": "Disabled",
                                                   "model_version": "2.0", "duration": 16})
    check("vs node: 2.0+16s rejected at node", False)
except ValueError as e:
    check("vs node: 2.0+16s rejected at node", "4-15 秒" in str(e), str(e))

# 文生视频：无素材可跑（t2v 模式），prompt 必填
try:
    nodes.TencentVODVSVideoTask().generate("", **{**vs_params, "use_cache": "Disabled"})
    check("vs node: empty prompt rejected", False)
except ValueError:
    check("vs node: empty prompt rejected", True)

# ---- 12. 结果缓存：seed / model_version 参与缓存键 ----
ck_a = nodes._cache_key("t2v", "p", {"model_version": "2.5", "seed": 42, "logo_add": "Disabled"})
ck_b = nodes._cache_key("t2v", "p", {"model_version": "2.5", "seed": 43, "logo_add": "Disabled"})
ck_c = nodes._cache_key("t2v", "p", {"model_version": "2.0", "seed": 42, "logo_add": "Disabled"})
ck_d = nodes._cache_key("t2v", "p", {"model_version": "2.5", "seed": 42, "logo_add": "Enabled"})
check("vs cache: seed/version/logo in key", len({ck_a, ck_b, ck_c, ck_d}) == 4)
# H3 缓存键不含新参数（不传即不参与），现有键不漂移
ck_h3 = nodes._cache_key("t2v", "p", {"duration": 5, "resolution": "1080P"})
check("vs cache: h3 key unchanged without new params",
      ck_h3 == nodes._cache_key("t2v", "p", {"duration": 5, "resolution": "1080P"}))

# 缓存命中：零 API 调用、复用产物（台账种子记录）。
# 注意：vs_params 含 first_frame_url → 节点模式推断为 i2v，种子记录的 mode 必须一致；
# 命中测试需 use_cache=Enabled（use_cache 不参与缓存键，与种子记录同键）。
cache_hit_file = "/tmp/comfy_output/vod_aigc/vs_cache.mp4"
with open(cache_hit_file, "wb") as f:
    f.write(b"fake-vs-video")
ck_hit = nodes._cache_key("i2v", "缓存命中测试", vs_params)
rec_hit = nodes._base_record("i2v", "缓存命中测试", vs_params, task_id="t-vs-cache",
                             url="https://cdn/cache.mp4", path=cache_hit_file, cache_key=ck_hit)
with open(hpath, "a", encoding="utf-8") as f:
    f.write(json.dumps(rec_hit, ensure_ascii=False) + "\n")
hit_calls = {"n": 0}
def fake_hit_api(sid, sk, reg, ep, action, payload, version="", service=""):
    hit_calls["n"] += 1
    raise AssertionError("缓存命中不应调用 API")
nodes._call_api = fake_hit_api
out_hit = nodes.TencentVODVSVideoTask().generate("缓存命中测试", **{**vs_params, "use_cache": "Enabled"})
check("vs cache: hit returns cached result, zero API", hit_calls["n"] == 0 and out_hit[1] == "https://cdn/cache.mp4", out_hit)
# 改 seed → 不命中（缓存键含 seed）
try:
    nodes.TencentVODVSVideoTask().generate("缓存命中测试", **{**vs_params, "use_cache": "Enabled", "seed": 999})
    check("vs cache: changed seed misses", False)
except AssertionError:
    check("vs cache: changed seed misses", hit_calls["n"] == 1)
nodes._call_api = fake_vs_api

# ---- 13. 素材节点全流程（含失败路径）----
mat_node_calls = []
def fake_mat_node(sid, sk, reg, ep, action, payload, version="", service=""):
    mat_node_calls.append(action)
    if action == "CreateAigcMaterial":
        return {"TaskId": "123456-CreateAigcMaterial-abc001t"}
    return {"TaskType": "CreateAigcMaterial", "Status": "FINISH",
            "CreateAigcMaterialTask": {"Status": "FINISH", "ErrCode": 0, "Message": "",
                "Output": {"AssetId": "asset-20260811154248-gl654", "AssetUrl": "url",
                           "GroupId": "group-abc"}}}
nodes._call_api = fake_mat_node
r_matnode = nodes.TencentVODAIGCCreateMaterial().create(
    "Image", "https://x/1.png", "小熊", secret_id="x", secret_key="y", sub_app_id="1500044236",
    is_real_person="Disabled", region="ap-guangzhou", endpoint="", poll_interval=3, timeout=60)
check("material node: returns (task_id, asset://...)",
      r_matnode == ("123456-CreateAigcMaterial-abc001t", "asset://asset-20260811154248-gl654"), str(r_matnode))
check("material node: submit + poll actions",
      mat_node_calls == ["CreateAigcMaterial", "DescribeTaskDetail"], str(mat_node_calls))
# 素材节点无缓存（IS_CHANGED=NaN 每次重跑）
check("material node: IS_CHANGED=NaN", nodes.TencentVODAIGCCreateMaterial.IS_CHANGED()
      != nodes.TencentVODAIGCCreateMaterial.IS_CHANGED())

# 素材任务成功但无 AssetId → 明确报错
def fake_mat_noasset(sid, sk, reg, ep, action, payload, version="", service=""):
    if action == "CreateAigcMaterial":
        return {"TaskId": "t-noasset"}
    return {"TaskType": "CreateAigcMaterial", "Status": "FINISH",
            "CreateAigcMaterialTask": {"Status": "FINISH", "ErrCode": 0, "Message": "",
                "Output": {"AssetUrl": "url"}}}
nodes._call_api = fake_mat_noasset
try:
    nodes.TencentVODAIGCCreateMaterial().create("Image", "https://x/2.png", "p",
        secret_id="x", secret_key="y", sub_app_id="1500044236", poll_interval=3, timeout=60)
    check("material node: missing AssetId raises", False)
except RuntimeError as e:
    check("material node: missing AssetId raises", "未返回 AssetId" in str(e), str(e))
# 素材节点必填校验
try:
    nodes.TencentVODAIGCCreateMaterial().create("Image", "", "p", secret_id="x", secret_key="y",
                                                sub_app_id="1", poll_interval=3, timeout=60)
    check("material node: empty url rejected", False)
except ValueError as e:
    check("material node: empty url rejected", "file_url" in str(e), str(e))

# ---- @N 引用展开（expand_prompt_refs）----
from vod_aigc_core import expand_prompt_refs as _epr
check("refs: @1 -> 图1", _epr("@1=皇后", 5) == "图1=皇后")
check("refs: @图片3 -> 图3", _epr("人物：@图片3=皇后", 5) == "人物：图3=皇后")
check("refs: mixed @N and 图N passthrough", _epr("图1 场景，@2 角色", 5) == "图1 场景，图2 角色")
check("refs: no @ unchanged", _epr("普通提示词", 5) == "普通提示词")
check("refs: empty prompt ok", _epr("", 5) == "")
check("refs: @1 with 1 image", _epr("@1", 1) == "图1")
try:
    _epr("@6=谁", 5)
    check("refs: out-of-range rejected", False)
except ValueError as e:
    check("refs: out-of-range rejected", "5 张参考图" in str(e) and "@6" in str(e), str(e))
try:
    _epr("@0", 5)
    check("refs: @0 rejected", False)
except ValueError:
    check("refs: @0 rejected", True)

# VS 节点：@N 在提交前展开为图N（mock _call_api 捕获 payload）
_orig_call = nodes._call_api
_captured = {}
def _fake_call(*a, **kw):
    _captured["payload"] = a[5] if len(a) > 5 else None
    raise RuntimeError("stop")  # 只捕获 payload，不继续
nodes._call_api = _fake_call
try:
    _vs_kw = dict(secret_id="x", secret_key="y", sub_app_id="1",
                  duration=5, resolution="720P", aspect_ratio="9:16",
                  audio_generation="Enabled", seed=-1, logo_add="Disabled",
                  high_bitrate="Disabled", return_last_frame="Disabled",
                  storage_mode="Temporary", use_cache="Disabled", media_name="",
                  filename="", region="ap-guangzhou", endpoint="", input_region="",
                  poll_interval=3, timeout=60)
    try:
        nodes.TencentVODVSVideoTask().generate("人物：@1=皇后、@2=祺贵人",
                                               ref_image_urls="https://x/1.png\nhttps://x/2.png",
                                               **_vs_kw)
    except RuntimeError:
        pass
    p = _captured.get("payload", {})
    check("vs node: @N expanded in submitted prompt",
          p.get("Prompt") == "人物：图1=皇后、图2=祺贵人", str(p.get("Prompt"))[:60])
    check("vs node: payload FileInfos 2 ref images", len(p.get("FileInfos") or []) == 2)
    try:
        nodes.TencentVODVSVideoTask().generate("人物：@3=谁",
                                               ref_image_urls="https://x/1.png\nhttps://x/2.png",
                                               **_vs_kw)
        check("vs node: out-of-range @N blocks submit", False)
    except ValueError as e:
        check("vs node: out-of-range @N blocks submit", "2 张参考图" in str(e), str(e))
finally:
    nodes._call_api = _orig_call

# ---- 汇总 ----
print()
print()
if failures:
    print(f"RESULT: {len(failures)} FAILED: {failures}")
    sys.exit(1)
print("RESULT: ALL PASS")
