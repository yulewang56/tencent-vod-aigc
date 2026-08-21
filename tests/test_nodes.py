"""Verify comfyui-tencent-vod-aigc node pack loads and core logic works."""
import json
import os
import sys
import tempfile
import types

# ---- stub ComfyUI/numpy/PIL dependencies (not needed at import time) ----
# numpy/PIL 优先用真实库（图片压缩是节点功能，需真实 Pillow 验证）；无则降级 stub。
comfy = types.ModuleType("comfy")
folder_paths = types.ModuleType("comfy.folder_paths")
folder_paths.get_output_directory = lambda: "/tmp/comfy_output"
folder_paths.get_input_directory = lambda: "/tmp/comfy_input"
comfy.folder_paths = folder_paths
sys.modules["comfy"] = comfy
sys.modules["comfy.folder_paths"] = folder_paths

class FakeFile3D:
    def __init__(self, source):
        self.source = source

    def get_source(self):
        return self.source


class FakeVideoComponents:
    def __init__(self, images, audio, frame_rate):
        self.images = images
        self.audio = audio
        self.frame_rate = frame_rate


class FakeVideo:
    def __init__(self, components):
        self.components = components

    def save_to(self, path):
        with open(path, "wb") as handle:
            handle.write(b"fake-video")


class FakeInputImpl:
    @staticmethod
    def VideoFromComponents(components):
        return FakeVideo(components)


comfy_api = types.ModuleType("comfy_api")
comfy_api_latest = types.ModuleType("comfy_api.latest")
comfy_api_latest.InputImpl = FakeInputImpl
comfy_api_latest.Types = types.SimpleNamespace(
    File3D=FakeFile3D, VideoComponents=FakeVideoComponents)
sys.modules["comfy_api"] = comfy_api
sys.modules["comfy_api.latest"] = comfy_api_latest

try:
    import numpy  # noqa: F401  真实 numpy（若有）
except ImportError:
    sys.modules["numpy"] = types.ModuleType("numpy")
try:
    import PIL  # noqa: F401  真实 Pillow（若有）
    import PIL.Image  # noqa: F401
except ImportError:
    pil = types.ModuleType("PIL")
    pil.Image = types.SimpleNamespace()
    sys.modules["PIL"] = pil

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))  # 仓库根目录
import editable_scene
import nodes

failures = []

def check(name, cond, detail=""):
    if cond:
        print(f"  PASS  {name}")
    else:
        failures.append(name)
        print(f"  FAIL  {name}  {detail}")

# ---- 1. node registry ----
expected = ["TencentVODH3TextToVideo", "TencentVODH3ImageToVideo",
            "TencentVODH3ReferenceToVideo", "TencentVODVSVideoTask",
            "TencentVODAIGCCreateMaterial", "TencentVODAIGCImageTask",
            "TencentVODAIGCMusicTask", "TencentVODAIGCQueryTask",
            "TencentVODAIGCDownloadVideo", "TencentVODAIGCViewHistory",
            "TencentVODHunyuan3DWorld", "TencentVODImageToEditable3DScene",
            "TencentVOD3DPrevis"]
check("all nodes registered", set(expected) == set(nodes.NODE_CLASS_MAPPINGS),
      f"got {sorted(nodes.NODE_CLASS_MAPPINGS)}")
check("display names cover all", set(expected) == set(nodes.NODE_DISPLAY_NAME_MAPPINGS))

for name, cls in nodes.NODE_CLASS_MAPPINGS.items():
    t = cls.INPUT_TYPES()
    ret = cls.RETURN_TYPES
    fname = cls.FUNCTION
    check(f"{name}: INPUT_TYPES/RETURN_TYPES/FUNCTION",
          t.get("required") is not None and len(ret) == len(cls.RETURN_NAMES) and hasattr(cls, fname))
    check(f"{name}: IS_CHANGED=NaN (no caching)", cls.IS_CHANGED() != cls.IS_CHANGED())

previs_inputs = nodes.TencentVOD3DPrevis.INPUT_TYPES()
check("previs: legacy widget order preserved",
      list(previs_inputs["required"]) == [
          "scene_json", "camera_json", "frame_count", "width", "height"]
      and list(previs_inputs["optional"])[:3] == [
          "background_asset", "background_asset_path", "show_overlay"])
check("previs: extended timeline frame limit",
      previs_inputs["required"]["frame_count"][1]["max"] == 240)
check("previs: legacy outputs remain prefix",
      nodes.TencentVOD3DPrevis.RETURN_NAMES[:4]
      == ("frames", "camera_plan", "scene_plan", "reference_prompt"))
check("previs: integrated scene fields appended",
      list(previs_inputs["optional"])[-4:] == [
          "scene_source", "background_transform", "generated_task_id", "render_cache_path"])
uploaded_asset_path = nodes._previs_uploaded_asset_path(
    r"C:\fakepath\../示例 场景.SPZ", "a" * 32)
check("previs: local asset upload path is confined and sanitized",
      uploaded_asset_path == "/tmp/comfy_input/vod_aigc/previs_assets/"
      + "aaaaaaaaaaaa_示例_场景.spz",
      uploaded_asset_path)
try:
    nodes._previs_uploaded_asset_path("../../scene.fbx", "b" * 32)
    check("previs: unsupported local asset upload rejected", False)
except ValueError as error:
    check("previs: unsupported local asset upload rejected",
          "不支持的 3D 资产格式" in str(error), str(error))

# ---- 2. TC3 signing ----
headers, body = nodes._sign_request("AKIDtest123", "secretKey456", "ap-guangzhou",
                                    "vod.tencentcloudapi.com", "CreateAigcVideoTask",
                                    {"SubAppId": 1500044236, "ModelName": "Hailuo", "ModelVersion": "H3"})
auth = headers["Authorization"]
check("TC3 auth header", auth.startswith("TC3-HMAC-SHA256 Credential=AKIDtest123/") and "SignedHeaders=content-type;host;x-tc-action;x-tc-region;x-tc-timestamp;x-tc-version, Signature=" in auth, auth[:160])
check("required headers", all(h in headers for h in ("X-TC-Action", "X-TC-Version", "X-TC-Timestamp", "X-TC-Region", "Content-Type", "Host")))
check("body is JSON", json.loads(body)["ModelName"] == "Hailuo" and json.loads(body)["ModelVersion"] == "H3")
# deterministic for same timestamp? verify signature hex length
sig = auth.split("Signature=")[-1]
check("signature is 64 hex chars", len(sig) == 64)

# ---- 3. payload builder ----
oc = {"storage_mode": "Temporary", "duration": 5, "resolution": "1080P",
      "aspect_ratio": "16:9", "audio_generation": "Enabled", "media_name": "test"}
payload = nodes._build_payload("1500044236", "hello world", "Disabled", oc,
                               file_infos=[{"Type": "Url", "Category": "Image", "Url": "https://x/1.png", "Usage": "FirstFrame"}],
                               input_region="oversea")
check("payload shape", payload["SubAppId"] == 1500044236 and payload["ModelName"] == "Hailuo"
      and payload["OutputConfig"]["Duration"] == 5 and payload["InputRegion"] == "oversea"
      and payload["FileInfos"][0]["Usage"] == "FirstFrame")
oc2 = dict(oc); oc2["media_name"] = ""
p2 = nodes._build_payload("1", "p", "Disabled", oc2)
check("MediaName omitted when empty", "MediaName" not in p2["OutputConfig"])

# ---- 4. task result extraction ----
detail = {
    "TaskType": "AigcVideoTask",
    "Status": "PROCESSING",
    "AigcVideoTask": {
        "Status": "SUCCESS",
        "ErrCode": 0,
        "Message": "ok",
        "Input": {"FileInfos": [{"FileUrl": "https://cdn/input.png"}]},   # must be ignored
        "Output": {"FileInfos": [
            {"FileUrl": "https://1500044236.vod-qcloud.com/xxx/aigcVideoGenFile.mp4", "FileId": "123", "FileType": "mp4"}
        ]},
    },
}
status, err, err_ext, msg, urls = nodes._extract_task_result(detail)
check("extract: status from task dict", status == "SUCCESS" and msg == "ok")
check("extract: only Output urls", urls == ["https://1500044236.vod-qcloud.com/xxx/aigcVideoGenFile.mp4"], str(urls))

fail_detail = {"TaskType": "AigcVideoTask", "Status": "PROCESSING",
               "AigcVideoTask": {"Status": "FAIL", "ErrCode": 70000, "Message": "task failed with status: FAIL"}}
status, err, err_ext, msg, urls = nodes._extract_task_result(fail_detail)
check("extract: FAIL status", status == "FAIL" and err == 70000)

# ---- 5. polling loop with mocked API ----
calls = []
def fake_call(secret_id, secret_key, region, endpoint, action, payload, version="", service=""):
    calls.append(action)
    if action == "CreateAigcVideoTask":
        return {"TaskId": "1500044236-AigcVideoTask-deadbeef"}
    if len(calls) <= 3:  # first two DescribeTaskDetail -> PROCESSING (真实平铺结构)
        return {"TaskType": "AigcVideoTask", "Status": "PROCESSING",
                "AigcVideoTask": {"Status": "PROCESSING"}}
    return {"TaskType": "AigcVideoTask", "Status": "FINISH",
            "AigcVideoTask": {"Status": "FINISH", "Output": {"FileInfos": [
                {"FileUrl": "https://cdn/aigcVideoGenFile.mp4"}]}}}
nodes._call_api = fake_call
result = nodes._wait_for_task("sid", "skey", "ap-guangzhou", "vod.tencentcloudapi.com",
                              "1500044236", "1500044236-AigcVideoTask-deadbeef", 3, 60)
check("poll loop returns urls", result["urls"] == ["https://cdn/aigcVideoGenFile.mp4"] and result["status"] in ("SUCCESS", "FINISH"))
check("poll loop calls describe 3 times", len(calls) == 4, str(calls))

# ---- 6. credentials resolution ----
orig_load = nodes._load_config_file
nodes._load_config_file = lambda: {}
try:
    nodes._resolve_credentials("", "", "")
    check("missing creds raises", False)
except ValueError as e:
    check("missing creds raises", "SecretId" in str(e) and "tencent-vod-config.json" in str(e), str(e))
nodes._load_config_file = orig_load
sid, skey, sub = nodes._resolve_credentials("AKIDx", "sk", "1500044236")
check("explicit creds win", (sid, skey, sub) == ("AKIDx", "sk", "1500044236"))

try:
    nodes._resolve_credentials("", "", "abc123")
    check("non-numeric sub raises", False)
except ValueError:
    check("non-numeric sub raises", True)

# ---- 7. multiline parsing ----
check("multiline parse", nodes._parse_multiline("a\n\nb\n") == ["a", "b"])

# ---- 8. canonical headers 规范（x-tc-action 小写）----
hdrs = {"Host": "vod.tencentcloudapi.com", "Content-Type": "application/json; charset=utf-8",
        "X-TC-Action": "CreateAigcVideoTask", "X-TC-Version": "2018-07-17",
        "X-TC-Timestamp": "123", "X-TC-Region": "ap-guangzhou"}
ch = nodes._canonical_headers(hdrs, "CreateAigcVideoTask")
check("canonical: x-tc-action lowercase", "x-tc-action:createaigcvideotask\n" in ch, ch)
check("canonical: sorted order", ch.index("content-type:") < ch.index("host:") < ch.index("x-tc-action:"))
check("canonical: other values intact", "x-tc-version:2018-07-17\n" in ch and "x-tc-region:ap-guangzhou\n" in ch)


# ---- 9. 真实响应平铺结构（线上实测）----
real = {
    "TaskType": "AigcVideoTask",
    "Status": "FINISH",
    "CreateTime": "2026-08-14T15:23:21Z",
    "FinishTime": "2026-08-14T15:26:33Z",
    "AigcVideoTask": {
        "TaskId": "x-AigcVideoTask-y",
        "Status": "FINISH",
        "ErrCode": 0,
        "Message": "",
        "Input": {"Prompt": "hello"},
        "Output": {"FileInfos": [{"FileUrl": "http://store.vod-qcloud.com/xxx/aigcVideoGenFile.mp4"}]},
    },
    "RequestId": "abc",
}
status, err, err_ext, msg, urls = nodes._extract_task_result(real)
check("real: flattened FINISH parsed", status == "FINISH" and err == 0 and msg == "")
check("real: url extracted", urls == ["http://store.vod-qcloud.com/xxx/aigcVideoGenFile.mp4"], str(urls))

# _wait_for_task 对平铺结构：detail = response.get("TaskDetail") or response
result2 = nodes._wait_for_task("sid", "skey", "ap-guangzhou", "vod.tencentcloudapi.com",
                               "1500044236", "1500044236-AigcVideoTask-deadbeef", 3, 60)
check("wait: flattened response works", result2["urls"] == ["https://cdn/aigcVideoGenFile.mp4"])

# 兼容：嵌套 TaskDetail 结构
nested = {"TaskDetail": {"TaskType": "AigcVideoTask", "Status": "FINISH",
                         "AigcVideoTask": {"Status": "FINISH", "Output": {"FileInfos": [
                             {"FileUrl": "https://cdn/nested.mp4"}]}}}}
calls2 = []
def fake_call_nested(sid, sk, reg, ep, action, payload, version="", service=""):
    calls2.append(action)
    if action == "CreateAigcVideoTask":
        return {"TaskId": "t1"}
    return nested
nodes._call_api = fake_call_nested
r3 = nodes._wait_for_task("sid", "skey", "ap-guangzhou", "", "1", "t1", 3, 60)
check("wait: nested TaskDetail still works", r3["urls"] == ["https://cdn/nested.mp4"])


# ---- 10. 执行台账（v1.1）----
hpath = "/tmp/comfy_output/vod_aigc/execution_history.jsonl"
if os.path.exists(hpath):
    os.remove(hpath)

def fake_success_api(sid, sk, reg, ep, action, payload, version="", service=""):
    if action == "CreateAigcVideoTask":
        return {"TaskId": "t-ledger-1"}
    return {"TaskType": "AigcVideoTask", "Status": "FINISH",
            "AigcVideoTask": {"Status": "FINISH", "Output": {"FileInfos": [
                {"FileUrl": "https://cdn/x.mp4"}]}}}
nodes._call_api = fake_success_api
nodes._download_video = lambda url, task_id, on_progress=None, name_hint=None: "/tmp/comfy_output/vod_aigc/t_ledger_1.mp4"

params = {"secret_id": "x", "secret_key": "y", "sub_app_id": "1500044236",
          "duration": 5, "resolution": "1080P", "aspect_ratio": "16:9",
          "audio_generation": "Enabled", "storage_mode": "Temporary",
          "enhance_prompt": "Disabled", "region": "ap-guangzhou", "endpoint": "",
          "input_region": "", "poll_interval": 3, "timeout": 60, "media_name": ""}

nodes.TencentVODH3TextToVideo().generate("ledger test", **params)
lines = open(hpath).read().strip().splitlines()
check("ledger: success line written", len(lines) == 1, str(lines))
rec = json.loads(lines[0])
check("ledger: success fields", rec["status"] == "success" and rec["task_id"] == "t-ledger-1"
      and rec["mode"] == "t2v" and rec["resolution"] == "1080P"
      and rec["video_path"] == "/tmp/comfy_output/vod_aigc/t_ledger_1.mp4")
check("ledger: billing fields", rec["seconds_billed"] == 5 and rec["estimated_cost"] == 0.0
      and rec["view_url"] == "/view?filename=t_ledger_1.mp4&subfolder=vod_aigc&type=output", rec)

try:
    nodes.TencentVODH3TextToVideo().generate("", **params)  # 空 prompt 必然失败
    check("ledger: failure recorded", False)
except ValueError:
    pass
lines = open(hpath).read().strip().splitlines()
check("ledger: failure line written", len(lines) == 2)
rec2 = json.loads(lines[1])
check("ledger: failure fields", rec2["status"] == "failure" and "Prompt" in rec2["error"])


# ---- 11. 台账查看节点 ----
view_result = nodes.TencentVODAIGCViewHistory().view()
check("viewer: ui protocol", "ui" in view_result and "result" in view_result)
text = view_result["result"][0]
ledger_path = view_result["result"][1]
check("viewer: returns ledger text", "✅" in text and "ledger test" in text, text[:100])
check("viewer: failure row shown", "❌" in text)
check("viewer: ledger path returned", ledger_path.endswith("execution_history.jsonl"))


# ---- 12. 计费估算 ----
sec, cost = nodes._estimate_cost("1080P", 3)
check("billing: min 5s", sec == 5 and cost == 0.0)
sec, cost = nodes._estimate_cost("2K", 10)
check("billing: duration pass-through", sec == 10)
sec, cost = nodes._estimate_cost("", 7)
check("billing: unknown res rate 0", cost == 0.0)
check("billing: view url", nodes._view_url_for("/tmp/comfy_output/vod_aigc/x.mp4")
      == "/view?filename=x.mp4&subfolder=vod_aigc&type=output")

# ---- 11. 内容审核拒绝（v1.4.1）----
reject_calls = []
def fake_reject(secret_id, secret_key, region, endpoint, action, payload, version="", service=""):
    reject_calls.append(action)
    if action == "CreateAigcVideoTask":
        return {"TaskId": "1500044236-AigcVideoTask-rejected01t"}
    return {"TaskType": "AigcVideoTask", "Status": "FINISH",
            "AigcVideoTask": {"Status": "FINISH", "ErrCode": 70000,
                              "ErrCodeExt": "InvalidParameter.ViolationContent",
                              "Message": "Input Prompt violates policy",
                              "Output": {"FileInfos": []}}}
old_call = nodes._call_api
nodes._call_api = fake_reject
try:
    nodes._wait_for_task("sid", "skey", "ap-guangzhou", "vod.tencentcloudapi.com",
                         "1500044236", "1500044236-AigcVideoTask-rejected01t", 3, 60)
    check("reject: raises", False)
except nodes.TaskError as e:
    check("reject: TaskError with task_id", e.task_id == "1500044236-AigcVideoTask-rejected01t")
    check("reject: message names violation", "ViolationContent" in str(e) and "Input Prompt violates policy" in str(e), str(e))
except Exception as e:
    check("reject: TaskError type", False, repr(e))
nodes._call_api = old_call

# 台账失败记录回填 task_id
import functools
ledger_records = []
orig_append = nodes._append_history
nodes._append_history = lambda rec: ledger_records.append(rec)
try:
    nodes._call_api = fake_reject
    node_obj = type("N", (), {})()
    def failing_generate(self, prompt, **kwargs):
        raise nodes.TaskError("1500044236-AigcVideoTask-rejected01t", "H3 任务被拒绝（ErrCode=70000）TaskId: 1500044236-AigcVideoTask-rejected01t")
    wrapped = nodes._ledger("r2v")(failing_generate)
    try:
        wrapped(node_obj, "测试 prompt", **{"duration": 8, "resolution": "1080P", "aspect_ratio": "16:9",
                                            "audio_generation": "Enabled", "storage_mode": "Temporary",
                                            "enhance_prompt": "Disabled", "media_name": ""})
    except RuntimeError:
        pass
    rec = ledger_records[-1]
    check("ledger: failure records task_id", rec["task_id"] == "1500044236-AigcVideoTask-rejected01t", str(rec.get("task_id")))
    check("ledger: failure status", rec["status"] == "failure")
finally:
    nodes._append_history = orig_append

# ---- 12. 配置文件回退（v1.5.0，v1.8.0 起仅 tencent-vod-config.json）----
file_creds = {"secret_id": "AKIDfile", "secret_key": "sk-file", "sub_app_id": "1500044236"}
nodes._load_config_file = lambda: file_creds
try:
    sid, skey, sub = nodes._resolve_credentials("", "", "")
    check("creds: file fallback", (sid, skey, sub) == ("AKIDfile", "sk-file", "1500044236"))
    # 节点输入 > 文件
    sid, skey, sub = nodes._resolve_credentials("AKIDwidget", "sk-widget", "123")
    check("creds: widget beats file", (sid, skey, sub) == ("AKIDwidget", "sk-widget", "123"))
    # 文件缺失 → 报缺密钥
    nodes._load_config_file = lambda: {"secret_id": "only-id"}
    try:
        nodes._resolve_credentials("", "", "")
        check("creds: partial file raises", False)
    except ValueError:
        check("creds: partial file raises", True)
finally:
    nodes._load_config_file = orig_load

# ---- 13. 凭据状态与保存（v1.6.0）----
nodes._load_config_file = lambda: {"secret_id": "AKIDf", "secret_key": "sk-f", "sub_app_id": "1500044236"}
check("creds-status: file configured", nodes._credentials_configured() is True)
nodes._load_config_file = lambda: {}
check("creds-status: nothing configured", nodes._credentials_configured() is False)

import tempfile
tmpdir = tempfile.mkdtemp()
p = nodes._save_config_file("AKIDsave", "sk-save", "1500044236", path=os.path.join(tmpdir, "tencent-vod-config.json"))
check("creds-save: writes file", os.path.isfile(p))
saved = json.load(open(p))
check("creds-save: round-trip", saved == {"secret_id": "AKIDsave", "secret_key": "sk-save",
                                          "sub_app_id": "1500044236", "prices": {},
                                          "image_prices": {}}, str(saved))
try:
    nodes._save_config_file("", "sk", "1500044236", path=os.path.join(tmpdir, "x.json"))
    check("creds-save: empty rejects", False)
except ValueError:
    check("creds-save: empty rejects", True)
try:
    nodes._save_config_file("AKIDa", "sk", "abc", os.path.join(tmpdir, "x.json"))
    check("creds-save: non-digit sub rejects", False)
except ValueError:
    check("creds-save: non-digit sub rejects", True)
nodes._load_config_file = orig_load

# ---- 14. 统一配置文件与单价（v1.7.0）----
import tempfile as _tf
_tmp = _tf.mkdtemp()
# 14.1 读取含 prices 的配置文件
_nodes_cfg = os.path.join(_tmp, "tencent-vod-config.json")
open(_nodes_cfg, "w").write(json.dumps({"secret_id": "AKIDcf", "secret_key": "sk-cf", "sub_app_id": "1500044236",
                                        "prices": {"768P": 0.1, "2K": "0.35"}}))
cfg = nodes._load_config_file(_tmp)
check("config: loads creds", cfg["secret_id"] == "AKIDcf" and cfg["sub_app_id"] == "1500044236")
check("config: loads prices (str->float)", cfg["prices"] == {"768P": 0.1, "2K": 0.35}, str(cfg["prices"]))
# 14.2 单价：配置文件 > 0（无环境变量通道）
_orig_cfg = nodes._load_config_file
nodes._load_config_file = lambda: {"secret_id": "x", "secret_key": "y", "sub_app_id": "1",
                                   "prices": {"768P": 0.1, "2K": 0.35}}
check("price: from config", nodes._price_for("768P") == 0.1)
check("price: unset -> 0", nodes._price_for("4K") == 0.0)
nodes._load_config_file = _orig_cfg
# 14.4 保存合并单价（已存在价格保留）
p2 = nodes._save_config_file("AKIDsave2", "sk-save2", "1500044236", prices={"1080P": 0.2},
                             path=os.path.join(_tmp, "tencent-vod-config.json"))
merged = json.load(open(p2))
check("config-save: merges prices", merged["prices"] == {"768P": 0.1, "2K": 0.35, "1080P": 0.2}, str(merged["prices"]))
check("config-save: creds written", merged["secret_id"] == "AKIDsave2")
# 14.5 非法单价拒绝
try:
    nodes._save_config_file("AKIDa", "sk", "1500044236", prices={"768P": "abc"},
                            path=os.path.join(_tmp, "x.json"))
    check("config-save: bad price raises", False)
except ValueError as e:
    check("config-save: bad price raises", "单价" in str(e))
# 14.6 空 prices 保留现有值
p3 = nodes._save_config_file("AKIDa", "sk", "1500044236", prices={},
                             path=os.path.join(_tmp, "tencent-vod-config.json"))
merged2 = json.load(open(p3))
check("config-save: empty prices keeps existing", merged2["prices"] == merged["prices"])

# ---- 15. 文生图/图生图节点（v1.9.0，文档 3.3.2）----
# 15.1 注册与 payload
check("t2i: registered", "TencentVODAIGCImageTask" in nodes.NODE_CLASS_MAPPINGS)
img_payload = nodes._build_image_payload("1500044236", "一只猫", "Jimeng 4.0",
                                         {"storage_mode": "Temporary", "resolution": "1080P", "aspect_ratio": "16:9"})
check("t2i: payload t2i (no FileInfos)", img_payload == {
    "SubAppId": 1500044236, "ModelName": "Jimeng", "ModelVersion": "4.0", "Prompt": "一只猫",
    "OutputConfig": {"StorageMode": "Temporary", "Resolution": "1080P", "AspectRatio": "16:9"}}, str(img_payload))
img_payload2 = nodes._build_image_payload("1500044236", "p", "GEM 3.0",
                                          {"storage_mode": "Temporary", "resolution": "768P", "aspect_ratio": "1:1"},
                                          file_infos=[{"Type": "Url", "Url": "https://x/a.png"}])
check("t2i: payload i2i (with FileInfos)", img_payload2["FileInfos"][0]["Url"] == "https://x/a.png"
      and img_payload2["ModelName"] == "GEM", str(img_payload2))

# 15.2 批量 IMAGE → 每帧一张 FileInfos；全流程 mock
class FakeTensor:
    shape = (3, 1, 1, 3)
    def __getitem__(self, i):
        return self
captured2 = {}
orig_b64 = nodes._image_tensor_to_base64
orig_dl = nodes._download_video
nodes._image_tensor_to_base64 = lambda t, i: f"b64-{i}"
nodes._download_video = lambda url, task_id, on_progress=None, name_hint=None: "/tmp/fake.png"
def fake_img_call(secret_id, secret_key, region, endpoint, action, payload, version="", service=""):
    if action == "CreateAigcImageTask":
        captured2["payload"] = payload
        return {"TaskId": "1500044236-AigcImageTask-abc123t"}
    return {"TaskType": "AigcImageTask", "Status": "FINISH",
            "AigcImageTask": {"Status": "FINISH", "Output": {"FileInfos": [
                {"FileUrl": "https://cdn/aigcImageGenFile.png"}]}}}
orig_call2 = nodes._call_api
nodes._call_api = fake_img_call
try:
    node_obj = nodes.TencentVODAIGCImageTask()
    res = node_obj.generate("一只猫在窗边", secret_id="AKIDx", secret_key="sk",
                                       sub_app_id="1500044236", ref_image=FakeTensor(),
                                       model="Jimeng 4.0", storage_mode="Temporary",
                                       resolution="1080P", aspect_ratio="16:9",
                                       poll_interval=3, timeout=60)
    check("t2i: flow returns", res["result"][0] == "1500044236-AigcImageTask-abc123t" and res["result"][2] == "/tmp/fake.png")
    check("t2i: ui images present", res["ui"]["images"][0]["filename"] == "fake.png", str(res))
    fis = captured2["payload"]["FileInfos"]
    check("t2i: batch 3 frames -> 3 FileInfos", len(fis) == 3 and all(f["Type"] == "Base64" for f in fis), str(fis))
    check("t2i: FileInfos has no Category (v1.9.1)", all("Category" not in f for f in fis), str(fis))
    check("t2i: no duration in OutputConfig", "Duration" not in captured2["payload"]["OutputConfig"])
finally:
    nodes._call_api = orig_call2
    nodes._image_tensor_to_base64 = orig_b64
    nodes._download_video = orig_dl

# 15.3 台账：生图费用归零
rec_img = nodes._base_record("t2i", "猫", {"resolution": "1080P"})
check("t2i: ledger cost zero", rec_img["seconds_billed"] == 0 and rec_img["estimated_cost"] == 0.0, str(rec_img))
rec_img2 = nodes._base_record("i2i", "猫", {"resolution": "1080P"})
check("t2i: ledger i2i cost zero", rec_img2["estimated_cost"] == 0.0)

# 15.4 AigcImageTask 响应解析（与视频同构）
img_detail = {"TaskType": "AigcImageTask", "Status": "FINISH",
              "AigcImageTask": {"Status": "FINISH", "Output": {"FileInfos": [
                  {"FileUrl": "https://cdn/aigcImageGenFile.png"}]}}}
status, err, err_ext, msg, urls = nodes._extract_task_result(img_detail)
check("t2i: extract image urls", status == "FINISH" and urls == ["https://cdn/aigcImageGenFile.png"], str(urls))


# ---- 22. 结果缓存（v1.13.0）----
hpath = "/tmp/comfy_output/vod_aigc/execution_history.jsonl"
if os.path.exists(hpath):
    os.remove(hpath)
cache_file = "/tmp/comfy_output/vod_aigc/cache_hit.mp4"
if os.path.exists(cache_file):
    os.remove(cache_file)
with open(cache_file, "wb") as f:
    f.write(b"fake-video-bytes")

cache_calls = {"n": 0}
def fake_cache_api(sid, sk, reg, ep, action, payload, version="", service=""):
    if action == "CreateAigcVideoTask":
        cache_calls["n"] += 1
        return {"TaskId": "t-cache-1"}
    return {"TaskType": "AigcVideoTask", "Status": "FINISH",
            "AigcVideoTask": {"Status": "FINISH", "Output": {"FileInfos": [
                {"FileUrl": "https://cdn/cache.mp4"}]}}}
nodes._call_api = fake_cache_api
nodes._download_video = lambda url, task_id, on_progress=None, name_hint=None: cache_file

# 22.1 首次运行 → 调 API，落台账
out1 = nodes.TencentVODH3TextToVideo().generate("缓存测试", **params)
check("cache: first run calls API", cache_calls["n"] == 1, cache_calls["n"])

# 22.2 同参数再次 → 命中缓存：零 API 调用、返回同产物、台账标记 cached
cache_calls["n"] = 0
out2 = nodes.TencentVODH3TextToVideo().generate("缓存测试", **params)
check("cache: second run zero API calls", cache_calls["n"] == 0, cache_calls["n"])
check("cache: second run same result", out2 == out1, (out1, out2))
rec = json.loads(open(hpath).read().strip().splitlines()[-1])
check("cache: hit flagged in ledger", rec.get("cached") is True and bool(rec.get("cache_key")), rec)

# 22.3 不同 prompt → 不命中
cache_calls["n"] = 0
nodes.TencentVODH3TextToVideo().generate("缓存测试换个词", **params)
check("cache: different prompt misses", cache_calls["n"] == 1, cache_calls["n"])

# 22.4 产物文件丢失 → 不命中（允许重新生成）
cache_calls["n"] = 0
os.remove(cache_file)
nodes.TencentVODH3TextToVideo().generate("缓存测试", **params)
check("cache: missing artifact misses", cache_calls["n"] == 1, cache_calls["n"])
with open(cache_file, "wb") as f:
    f.write(b"fake-video-bytes")

# 22.5 use_cache=Disabled → 不命中
cache_calls["n"] = 0
p_disable = dict(params)
p_disable["use_cache"] = "Disabled"
nodes.TencentVODH3TextToVideo().generate("缓存测试", **p_disable)
check("cache: disabled misses", cache_calls["n"] == 1, cache_calls["n"])

# 22.6 失败记录不参与命中（装饰器级：失败落账后同键查不到）
def fail_gen(self, prompt, **kwargs):
    raise nodes.TaskError("t-fail", "H3 任务被拒绝（ErrCode=70000）")
wrapped_fail = nodes._ledger("t2v")(fail_gen)
try:
    wrapped_fail(type("N", (), {})(), "缓存测试失败场景", **params)
    check("cache: failing wrapped raises", False)
except nodes.TaskError:
    check("cache: failing wrapped raises", True)
ck_f = nodes._cache_key("t2v", "缓存测试失败场景", params)
check("cache: failed record not hit", nodes._find_cached_record(ck_f) is None)

# 22.7 生图节点缓存命中：dict 返回 + preview 协议、零 API 调用
img_path = "/tmp/comfy_output/vod_aigc/cache_hit.png"
with open(img_path, "wb") as f:
    f.write(b"fake-png")
img_params = {"secret_id": "x", "secret_key": "y", "sub_app_id": "1500044236",
              "model": "Jimeng 4.0", "output_image_count": 1, "output_format": "",
              "filename": "", "ref_image_urls": "", "resolution": "1080P",
              "aspect_ratio": "16:9", "storage_mode": "Temporary",
              "region": "ap-guangzhou", "endpoint": "", "poll_interval": 3, "timeout": 60}
ck_img = nodes._cache_key("t2i", "缓存生图", img_params)
rec_img = nodes._base_record("t2i", "缓存生图", img_params, task_id="t-img-cache",
                             url="https://cdn/i.png", path=img_path, cache_key=ck_img)
with open(hpath, "a", encoding="utf-8") as f:
    f.write(json.dumps(rec_img, ensure_ascii=False) + "\n")
img_calls = {"n": 0}
def fake_img_cache_api(sid, sk, reg, ep, action, payload):
    img_calls["n"] += 1
    raise AssertionError("缓存命中不应调用 API")
nodes._call_api = fake_img_cache_api
out_img = nodes.TencentVODAIGCImageTask().generate("缓存生图", **img_params)
check("cache: image node hit returns dict", isinstance(out_img, dict) and "ui" in out_img and "result" in out_img, out_img)
check("cache: image node zero API calls", img_calls["n"] == 0, img_calls["n"])
r_img = out_img["result"]
check("cache: image node 4-tuple", len(r_img) == 4 and r_img[0] == "t-img-cache" and r_img[2] == img_path, r_img)
rec = json.loads(open(hpath).read().strip().splitlines()[-1])
check("cache: image hit flagged", rec.get("cached") is True, rec)

# ---- 23. 图片本地路径输入（v1.14.0）----
import base64 as _b64
_tmp_p = _tf.mkdtemp()
ref_png = os.path.join(_tmp_p, "ref.png")
# 真实 PNG 夹具（图片路径走 PIL 压缩路径，假字节会被 UnidentifiedImageError 拒绝）
if "PIL" in sys.modules and hasattr(sys.modules["PIL"], "Image"):
    _PIL_Image = sys.modules["PIL"].Image
    _PIL_Image.new("RGB", (64, 64), (200, 30, 30)).save(ref_png, format="PNG")
else:
    with open(ref_png, "wb") as f:
        f.write(b"\x89PNG fake bytes")
bad_txt = os.path.join(_tmp_p, "ref.txt")
with open(bad_txt, "wb") as f:
    f.write(b"not an image")

# 23.1 路径加载与扩展名白名单（图片素材压缩为 JPEG 后编码）
b64_png = nodes._file_to_base64(ref_png, nodes._MAX_IMAGE_BYTES, "参考图", nodes._ALLOWED_IMAGE_EXTS, image=True)
_decoded_png = _b64.b64decode(b64_png)
check("path: image loads to compressed jpeg base64",
      _decoded_png[:2] == b"\xff\xd8" and len(_decoded_png) < 20000, str(len(_decoded_png)))
try:
    nodes._file_to_base64(bad_txt, nodes._MAX_IMAGE_BYTES, "参考图", nodes._ALLOWED_IMAGE_EXTS)
    check("path: bad image ext rejected", False)
except ValueError as e:
    check("path: bad image ext rejected", ".txt" in str(e) and ".png" in str(e), str(e))
try:
    nodes._file_to_base64(bad_txt, nodes._MAX_VIDEO_BYTES, "参考视频", nodes._ALLOWED_VIDEO_EXTS)
    check("path: bad video ext rejected", False)
except ValueError:
    check("path: bad video ext rejected", True)
try:
    nodes._file_to_base64(os.path.join(_tmp_p, "none.png"), 1024, "参考图", nodes._ALLOWED_IMAGE_EXTS)
    check("path: missing file raises", False)
except ValueError:
    check("path: missing file raises", True)

# 23.2 图生视频：路径与 URL 冲突报错
try:
    nodes.TencentVODH3ImageToVideo().generate("p", first_frame_path=ref_png, first_frame_url="https://x/a.png",
                                              **params)
    check("path: url+path conflict raises", False)
except ValueError as e:
    check("path: url+path conflict raises", "同时提供" in str(e), str(e))

# 23.3 图生视频：first_frame_path 全流程（payload FileInfos）
captured_i2v = {}
orig_call_p = nodes._call_api
orig_dl_p = nodes._download_video
def fake_i2v_path_call(sid, sk, reg, ep, action, payload, version="", service=""):
    if action == "CreateAigcVideoTask":
        captured_i2v["payload"] = payload
        return {"TaskId": "t-i2v-path1"}
    return {"TaskType": "AigcVideoTask", "Status": "FINISH",
            "AigcVideoTask": {"Status": "FINISH", "Output": {"FileInfos": [
                {"FileUrl": "https://cdn/out.mp4"}]}}}
nodes._call_api = fake_i2v_path_call
nodes._download_video = lambda url, task_id, on_progress=None, name_hint=None: "/tmp/comfy_output/vod_aigc/out.mp4"
try:
    p_i2v = dict(params)
    p_i2v["first_frame_path"] = ref_png
    res_i2v = nodes.TencentVODH3ImageToVideo().generate("首尾帧路径测试", **p_i2v)
    check("path: i2v flow returns", res_i2v[0] == "t-i2v-path1" and res_i2v[2].endswith("out.mp4"), res_i2v)
    fis = captured_i2v["payload"]["FileInfos"]
    check("path: i2v FirstFrame FileInfo", len(fis) == 1 and fis[0]["Usage"] == "FirstFrame"
          and fis[0]["Category"] == "Image" and fis[0]["Type"] == "Base64", str(fis))
    check("path: i2v b64 is compressed jpeg", _b64.b64decode(fis[0]["Base64"])[:2] == b"\xff\xd8")
finally:
    nodes._call_api = orig_call_p
    nodes._download_video = orig_dl_p

# 23.4 参考生视频：ref_image_paths 全流程（与 URL/IMAGE 可并存）
captured_r2v = {}
orig_call_p2 = nodes._call_api
orig_dl_p2 = nodes._download_video
def fake_r2v_path_call(sid, sk, reg, ep, action, payload, version="", service=""):
    if action == "CreateAigcVideoTask":
        captured_r2v["payload"] = payload
        return {"TaskId": "t-r2v-path1"}
    return {"TaskType": "AigcVideoTask", "Status": "FINISH",
            "AigcVideoTask": {"Status": "FINISH", "Output": {"FileInfos": [
                {"FileUrl": "https://cdn/out.mp4"}]}}}
nodes._call_api = fake_r2v_path_call
nodes._download_video = lambda url, task_id, on_progress=None, name_hint=None: "/tmp/comfy_output/vod_aigc/out.mp4"
try:
    p_r2v = dict(params)
    p_r2v["ref_image_paths"] = ref_png + "\n" + ref_png
    p_r2v["ref_image_urls"] = "https://x/a.png"
    res_r2v = nodes.TencentVODH3ReferenceToVideo().generate("参考图路径测试", **p_r2v)
    check("path: r2v flow returns", res_r2v[0] == "t-r2v-path1", res_r2v)
    fis = captured_r2v["payload"]["FileInfos"]
    check("path: r2v path+url coexist", len(fis) == 3 and
          sum(1 for f in fis if f["Usage"] == "Reference" and f["Category"] == "Image") == 3 and
          sum(1 for f in fis if f["Type"] == "Url") == 1, str(fis))
finally:
    nodes._call_api = orig_call_p2
    nodes._download_video = orig_dl_p2

# 23.5 生图节点：ref_image_paths（FileInfos 无 Category/Usage；台账 mode=i2i）
ledger_img = []
orig_append_img = nodes._append_history
orig_call_p3 = nodes._call_api
orig_dl_p3 = nodes._download_video
nodes._append_history = lambda rec: ledger_img.append(rec)
captured_imgp = {}
def fake_img_path_call(sid, sk, reg, ep, action, payload, version="", service=""):
    if action == "CreateAigcImageTask":
        captured_imgp["payload"] = payload
        return {"TaskId": "t-img-path1"}
    return {"TaskType": "AigcImageTask", "Status": "FINISH",
            "AigcImageTask": {"Status": "FINISH", "Output": {"FileInfos": [
                {"FileUrl": "https://cdn/o.png"}]}}}
nodes._call_api = fake_img_path_call
nodes._download_video = lambda url, task_id, on_progress=None, name_hint=None: "/tmp/comfy_output/vod_aigc/o.png"
try:
    p_imgp = dict(img_params)
    p_imgp["ref_image_paths"] = ref_png
    p_imgp["use_cache"] = "Disabled"
    res_imgp = nodes.TencentVODAIGCImageTask().generate("图生图路径测试", **p_imgp)
    fis = captured_imgp["payload"]["FileInfos"]
    check("path: image node FileInfo no Category/Usage", len(fis) == 1 and fis[0]["Type"] == "Base64"
          and "Category" not in fis[0] and "Usage" not in fis[0], str(fis))
    check("path: image node ledger mode=i2i", ledger_img and ledger_img[-1]["mode"] == "i2i",
          str(ledger_img[-1].get("mode") if ledger_img else None))
finally:
    nodes._append_history = orig_append_img
    nodes._call_api = orig_call_p3
    nodes._download_video = orig_dl_p3

# 23.6 缓存键包含路径参数
ck_p1 = nodes._cache_key("r2v", "p", {"ref_image_paths": "input/a.png"})
ck_p2 = nodes._cache_key("r2v", "p", {"ref_image_urls": "https://x/a.png"})
ck_p3 = nodes._cache_key("r2v", "p", {"ref_image_paths": "input/b.png"})
check("cache: ref_image_paths in key", len({ck_p1, ck_p2, ck_p3}) == 3)
ck_p4 = nodes._cache_key("i2v", "p", {"first_frame_path": "input/a.png"})
ck_p5 = nodes._cache_key("i2v", "p", {"first_frame_url": "https://x/a.png"})
ck_p6 = nodes._cache_key("i2v", "p", {"last_frame_path": "input/a.png"})
check("cache: first/last_frame_path in key", len({ck_p4, ck_p5, ck_p6}) == 3)


# ---- 24. MPS 音乐生成节点（v1.14.0）----
music_inputs = nodes.TencentVODAIGCMusicTask.INPUT_TYPES()
check("music: model dropdown", nodes.TencentVODAIGCMusicTask._MUSIC_MODELS ==
      ["GL 2.0", "GL 3.0-clip", "GL 3.0-pro", "MiniMaxMusic 2.0", "MiniMaxMusic 2.5", "MiniMaxMusic 2.6"])
check("music: prompt/lyrics multiline", music_inputs["required"]["prompt"][1].get("multiline")
      and music_inputs["optional"]["lyrics"][1].get("multiline"))
check("music: endpoint default mps", music_inputs["optional"]["endpoint"][1]["default"] == "mps.tencentcloudapi.com")

# 24.1 签名 version / service 参数
h_mps, _b = nodes._sign_request("AKIDt", "sk", "ap-guangzhou", "mps.tencentcloudapi.com",
                                "CreateAigcAudioTask", {"Prompt": "p"}, version="2019-06-12", service="mps")
check("music: X-TC-Version=2019-06-12", h_mps["X-TC-Version"] == "2019-06-12")
check("music: credential scope /mps/", "/mps/tc3_request" in h_mps["Authorization"], h_mps["Authorization"][:120])
h_vod, _b2 = nodes._sign_request("a", "b", "", "vod.tencentcloudapi.com", "DescribeTaskDetail", {"TaskId": "t"})
check("music: default version stays vod", h_vod["X-TC-Version"] == "2018-07-17" and "/vod/tc3_request" in h_vod["Authorization"])

# 24.2 payload 构造：歌词 / 纯音乐 / GL / 参考音频 / 无 SubAppId
p_lyric = nodes._build_music_payload("一首歌", "MiniMaxMusic 2.6",
                                     {"additional_parameters": json.dumps({"lyric": "啦啦啦"}, ensure_ascii=False),
                                      "output_format": "mp3"})
check("music: payload lyric form", p_lyric["ModelName"] == "MiniMaxMusic" and p_lyric["ModelVersion"] == "2.6"
      and p_lyric["SceneType"] == "music"
      and json.loads(p_lyric["AdditionalParameters"]) == {"lyric": "啦啦啦"}
      and p_lyric["OutputAudioFormat"] == "mp3", str(p_lyric))
p_inst = nodes._build_music_payload("纯音乐", "MiniMaxMusic 2.6",
                                    {"additional_parameters": '{"is_instrumental": true}', "output_format": ""})
check("music: payload instrumental form", json.loads(p_inst["AdditionalParameters"]) == {"is_instrumental": True}
      and "OutputAudioFormat" not in p_inst, str(p_inst))
p_gl = nodes._build_music_payload("p", "GL 3.0-pro", {"additional_parameters": "", "output_format": ""})
check("music: GL model parse", p_gl["ModelName"] == "GL" and p_gl["ModelVersion"] == "3.0-pro"
      and "AdditionalParameters" not in p_gl, str(p_gl))
p_ref = nodes._build_music_payload("p", "MiniMaxMusic 2.0", {"additional_parameters": "", "output_format": ""},
                                   file_infos=[{"Type": "Url", "Url": "https://x/a.mp3"}])
check("music: AudioInfos passed, no SubAppId", p_ref["AudioInfos"][0]["Url"] == "https://x/a.mp3"
      and "SubAppId" not in p_ref, str(p_ref))

# 24.3 AigcAudioTask 结果解析：平铺（AudioInfos[].Url）与嵌套（AigcAudioTask 键）
flat_audio = {"Status": "DONE", "AudioInfos": [{"Url": "https://cdn/song.mp3", "Duration": 120}], "RequestId": "r1"}
st, _, _, _, urls = nodes._extract_task_result(flat_audio)
check("music: flat DONE parsed", st == "DONE" and urls == ["https://cdn/song.mp3"], str(urls))
nested_audio = {"TaskType": "AigcAudioTask", "Status": "RUN",
                "AigcAudioTask": {"Status": "DONE", "Output": {"AudioInfos": [{"Url": "https://cdn/s.wav"}]}}}
st2, _, _, _, urls2 = nodes._extract_task_result(nested_audio)
check("music: nested AigcAudioTask parsed", st2 == "DONE" and urls2 == ["https://cdn/s.wav"], str(urls2))
fail_audio = {"Status": "FAIL", "Message": "tme audio url is empty"}
st3, _, _, msg3, urls3 = nodes._extract_task_result(fail_audio)
check("music: flat FAIL parsed", st3 == "FAIL" and msg3 == "tme audio url is empty" and urls3 == [])

# 24.4 轮询：MPS action/version/查询无 SubAppId、DONE 判定完成
music_calls = []
orig_call_m = nodes._call_api
orig_dl_m = nodes._download_video
def fake_music_api(sid, sk, reg, ep, action, payload, version="", service=""):
    music_calls.append({"action": action, "version": version, "service": service, "payload": payload})
    if action == "CreateAigcAudioTask":
        return {"TaskId": "24000145-AigcAudio-abcdef0t"}
    return {"Status": "DONE", "AudioInfos": [{"Url": "https://cdn/song.mp3", "Duration": 120}]}
nodes._call_api = fake_music_api
nodes._download_video = lambda url, task_id, on_progress=None, name_hint=None: "/tmp/comfy_output/vod_aigc/song_abcf.mp3"
try:
    res_m = nodes.TencentVODAIGCMusicTask().generate(
        "轻快的钢琴曲", secret_id="AKIDx", secret_key="sk", model="MiniMaxMusic 2.6",
        is_instrumental="Enabled", output_format="mp3", poll_interval=3, timeout=60, use_cache="Disabled")
    check("music: flow returns tuple", res_m == ("24000145-AigcAudio-abcdef0t",
                                                 "https://cdn/song.mp3", "/tmp/comfy_output/vod_aigc/song_abcf.mp3"), res_m)
    create = music_calls[0]
    check("music: create action/version/service", create["action"] == "CreateAigcAudioTask"
          and create["version"] == nodes.MPS_API_VERSION and create["service"] == "mps"
          and create["payload"]["SceneType"] == "music"
          and json.loads(create["payload"]["AdditionalParameters"]) == {"is_instrumental": True}
          and "SubAppId" not in create["payload"], str(create))
    describe = music_calls[-1]
    check("music: describe query no SubAppId", describe["action"] == "DescribeAigcAudioTask"
          and describe["version"] == "2019-06-12"
          and describe["payload"] == {"TaskId": "24000145-AigcAudio-abcdef0t"}, str(describe))
finally:
    nodes._call_api = orig_call_m
    nodes._download_video = orig_dl_m

# 24.5 歌词与纯音乐互斥、Prompt ≤2000
try:
    nodes.TencentVODAIGCMusicTask().generate("p", secret_id="AKIDx", secret_key="sk",
                                             lyrics="歌词", is_instrumental="Enabled",
                                             poll_interval=3, timeout=60, use_cache="Disabled")
    check("music: lyrics+instrumental conflict", False)
except ValueError as e:
    check("music: lyrics+instrumental conflict", "互斥" in str(e), str(e))
try:
    nodes.TencentVODAIGCMusicTask().generate("长" * 2001, secret_id="AKIDx", secret_key="sk",
                                             poll_interval=3, timeout=60, use_cache="Disabled")
    check("music: prompt>2000 rejected", False)
except ValueError as e:
    check("music: prompt>2000 rejected", "2000" in str(e), str(e))

# 24.6 台账 t2a：不计秒不计费，url/path 照记；视频计费逻辑不受影响
rec_t2a = nodes._base_record("t2a", "音乐", {"model": "MiniMaxMusic 2.6"})
check("music: ledger t2a no billing", rec_t2a["seconds_billed"] == 0 and rec_t2a["estimated_cost"] == 0.0
      and rec_t2a["model"] == "MiniMaxMusic 2.6" and rec_t2a["image_count"] == 0, str(rec_t2a))
rec_t2v = nodes._base_record("t2v", "视频", {"resolution": "1080P", "duration": 8})
check("music: video billing unchanged", rec_t2v["seconds_billed"] == 8, str(rec_t2v))
# 查看节点 t2a 显示
rec_t2a2 = nodes._base_record("t2a", "音乐测试", {"model": "MiniMaxMusic 2.6"},
                              task_id="t-view-music", url="https://cdn/song.mp3",
                              path="/tmp/comfy_output/vod_aigc/song.mp3")
rec_t2a2["time"] = "2026-08-17T10:00:00+0800"
with open(hpath, "a", encoding="utf-8") as f:
    f.write(json.dumps(rec_t2a2, ensure_ascii=False) + "\n")
vres_m = nodes.TencentVODAIGCViewHistory().view()
check("music: viewer shows t2a row", "t2a" in vres_m["result"][0] and "音乐测试" in vres_m["result"][0],
      vres_m["result"][0][-200:])

# 24.7 音乐节点缓存键：歌词/纯音乐/参考音频参与
ck_m1 = nodes._cache_key("t2a", "p", {"model": "MiniMaxMusic 2.6", "lyrics": "词A", "is_instrumental": "Disabled"})
ck_m2 = nodes._cache_key("t2a", "p", {"model": "MiniMaxMusic 2.6", "lyrics": "词B", "is_instrumental": "Disabled"})
ck_m3 = nodes._cache_key("t2a", "p", {"model": "MiniMaxMusic 2.6", "lyrics": "词A", "is_instrumental": "Enabled"})
ck_m4 = nodes._cache_key("t2a", "p", {"model": "MiniMaxMusic 2.6", "lyrics": "词A", "is_instrumental": "Disabled",
                                      "ref_audio_paths": "input/a.mp3"})
check("music: lyrics/instrumental/ref_audio in key", len({ck_m1, ck_m2, ck_m3, ck_m4}) == 4)


# ---- 25. 混元 3D 世界 + WebGL 白模预演（v1.18.0）----
p3d = nodes._build_3d_world_payload(
    "1500044236", "古代宫殿庭院", "Permanent",
    file_infos=[{"Type": "Url", "Category": "Image", "Url": "https://x/ref.png"}],
    input_region="oversea")
check("3d: payload exact model/scene",
      p3d["SubAppId"] == 1500044236
      and p3d["ModelName"] == "Hunyuan"
      and p3d["ModelVersion"] == "3d_2.0"
      and p3d["SceneType"] == "3d_scene"
      and p3d["OutputConfig"] == {"StorageMode": "Permanent"}
      and p3d["FileInfos"][0]["Category"] == "Image"
      and p3d["InputRegion"] == "oversea", str(p3d))

captured_3d = {}
ledger_3d = []
orig_call_3d = nodes._call_api
orig_dl_3d = nodes._download_video
orig_append_3d = nodes._append_history
def fake_3d_call(sid, sk, reg, ep, action, payload, version="", service=""):
    if action == "CreateAigcVideoTask":
        captured_3d["payload"] = payload
        return {"TaskId": "t-hunyuan-3d"}
    return {"TaskType": "AigcVideoTask", "Status": "FINISH",
            "AigcVideoTask": {"Status": "FINISH", "Output": {"FileInfos": [
                {"FileUrl": "https://cdn/world.spz"}]}}}
nodes._call_api = fake_3d_call
nodes._download_video = lambda url, task_id, on_progress=None, name_hint=None: "/tmp/comfy_output/vod_aigc/world.spz"
nodes._append_history = lambda record: ledger_3d.append(record)
try:
    result_3d = nodes.TencentVODHunyuan3DWorld().generate(
        "古代宫殿庭院", secret_id="AKIDx", secret_key="sk", sub_app_id="1500044236",
        image_url="https://x/ref.png", storage_mode="Permanent",
        poll_interval=3, timeout=60, use_cache="Disabled")
    check("3d: flow returns spz",
          result_3d[:3] == ("t-hunyuan-3d", "https://cdn/world.spz",
                            "/tmp/comfy_output/vod_aigc/world.spz")
          and isinstance(result_3d[3], FakeFile3D), str(result_3d))
    check("3d: request includes single image",
          captured_3d["payload"]["FileInfos"] == [
              {"Type": "Url", "Category": "Image", "Url": "https://x/ref.png"}],
          str(captured_3d["payload"]))
    check("3d: ledger i23d no per-second charge",
          ledger_3d[-1]["mode"] == "i23d"
          and ledger_3d[-1]["seconds_billed"] == 0
          and ledger_3d[-1]["estimated_cost"] == 0.0, str(ledger_3d[-1]))
    nodes.TencentVODHunyuan3DWorld().generate(
        "同一空间三视图", secret_id="AKIDx", secret_key="sk", sub_app_id="1500044236",
        image_url="https://x/front.png\nhttps://x/left.png\nhttps://x/right.png",
        storage_mode="Temporary", poll_interval=3, timeout=60, use_cache="Disabled")
    check("3d: up to three reference views",
          [item["Url"] for item in captured_3d["payload"]["FileInfos"]]
          == ["https://x/front.png", "https://x/left.png", "https://x/right.png"],
          str(captured_3d["payload"]))
finally:
    nodes._call_api = orig_call_3d
    nodes._download_video = orig_dl_3d
    nodes._append_history = orig_append_3d

try:
    nodes.TencentVODHunyuan3DWorld().generate(
        "冲突", image_path=ref_png, image_url="https://x/ref.png")
    check("3d: image inputs exclusive", False)
except ValueError as e:
    check("3d: image inputs exclusive", "只保留一种" in str(e), str(e))
try:
    nodes.TencentVODHunyuan3DWorld().generate(
        "过多图片", image_url="\n".join(f"https://x/{i}.png" for i in range(4)))
    check("3d: more than three views rejected", False)
except ValueError as e:
    check("3d: more than three views rejected", "最多 3 张" in str(e), str(e))

# ---- 25.1 图片转结构化可编辑白模（v1.24.0）----
editable_raw = {
    "room": {"width": 8, "depth": 10, "height": 3, "confidence": 0.82},
    "camera": {
        "position": [0, 1.7, -7], "target": [0, 1.2, 1], "fov_degrees": 56,
    },
    "objects": [
        {
            "name": "教师讲台", "category": "desk",
            "position": [0, 0, 3.5], "size": [2, 0.9, 0.8],
            "yaw_degrees": 0, "confidence": 0.9, "evidence": "observed",
            "movable": False,
        },
        {
            "name": "学生椅", "category": "chair",
            "position": [-1.5, 0, 0.5], "size": [0.5, 0.85, 0.5],
            "yaw_degrees": 5, "confidence": 0.75, "evidence": "inferred",
            "movable": True,
        },
    ],
}
extracted_editable = nodes._extract_json_object(
    "```json\n" + json.dumps(editable_raw, ensure_ascii=False) + "\n```")
editable_prompt = nodes._build_reconstruction_prompt("教室", 0, 36, "", 1)
check("editable 3d: prompt avoids room/camera numeric anchoring",
      '"width": 8.0' not in editable_prompt
      and '"position": [0.0, 1.7, -7.0]' not in editable_prompt
      and "wall must be left/right/back/front" in editable_prompt
      and "image_bbox" in editable_prompt
      and "floor_contact" in editable_prompt)
normalized_editable = nodes._normalize_reconstruction_layout(
    extracted_editable, known_room_width_m=12, max_objects=12)
check("editable 3d: known width rescales layout",
      normalized_editable["room"]["width"] == 12
      and normalized_editable["room"]["depth"] == 15
      and normalized_editable["objects"][0]["category"] == "table")
editable_without_camera = dict(editable_raw)
editable_without_camera.pop("camera")
normalized_without_camera = nodes._normalize_reconstruction_layout(
    editable_without_camera, known_room_width_m=16, max_objects=12)
check("editable 3d: default camera scales exactly once",
      normalized_without_camera["camera"]["position"][2] == -16)
out_of_bounds_layout = dict(editable_raw)
out_of_bounds_layout["objects"] = [{
    "name": "越界桌", "category": "table",
    "position": [200, 0, 200], "size": [2, 1, 2],
    "yaw_degrees": 45, "confidence": 0.7, "evidence": "observed",
}]
bounded_editable = nodes._normalize_reconstruction_layout(
    out_of_bounds_layout, known_room_width_m=0, max_objects=12)
bounded_object = bounded_editable["objects"][0]
yaw_radians = numpy.deg2rad(bounded_object["yaw_degrees"])
bounded_half_x = (
    abs(numpy.cos(yaw_radians)) * bounded_object["size"][0] * 0.5
    + abs(numpy.sin(yaw_radians)) * bounded_object["size"][2] * 0.5)
bounded_half_z = (
    abs(numpy.sin(yaw_radians)) * bounded_object["size"][0] * 0.5
    + abs(numpy.cos(yaw_radians)) * bounded_object["size"][2] * 0.5)
check("editable 3d: rotated footprint stays inside room",
      abs(bounded_object["position"][0]) + bounded_half_x <= 4.0 + 1e-6
      and abs(bounded_object["position"][2]) + bounded_half_z <= 5.0 + 1e-6)
prior_layout = dict(editable_raw)
prior_layout["objects"] = [
    {
        "name": "table_slab", "category": "table",
        "position": [0, 0, 0], "size": [1, 0.1, 0.6],
        "confidence": 0.8, "evidence": "observed",
    },
    {
        "name": "chair_seat", "category": "chair",
        "position": [1, 0, 0], "size": [0.5, 0.1, 0.6],
        "confidence": 0.8, "evidence": "observed",
    },
    {
        "name": "blackboard", "category": "blackboard", "wall": "right",
        "position": [3.5, 2.5, 0], "size": [3, 1, 2],
        "confidence": 0.9, "evidence": "observed", "movable": False,
    },
]
prior_normalized = nodes._normalize_reconstruction_layout(
    prior_layout, known_room_width_m=0, max_objects=12)
check("editable 3d: furniture heights use physical priors",
      prior_normalized["objects"][0]["size"][1] == 0.75
      and prior_normalized["objects"][1]["size"][1] == 0.85
      and prior_normalized["objects"][0]["position"][1] == 0
      and prior_normalized["objects"][1]["position"][1] == 0)
check("editable 3d: boards classify and attach to declared wall",
      prior_normalized["objects"][2]["category"] == "board"
      and prior_normalized["objects"][2]["wall"] == "right"
      and prior_normalized["objects"][2]["position"][0] == 4
      and prior_normalized["objects"][2]["size"][0] <= 0.14
      and prior_normalized["objects"][2]["movable"] is False)
projection_layout = {
    "room": {"width": 12, "depth": 12, "height": 3, "confidence": 0.8},
    "camera": {
        "position": [0, 1.7, -5], "target": [0, 1, 2], "fov_degrees": 55,
    },
    "objects": [
        {
            "name": "左桌", "category": "table",
            "position": [0, 0, 0], "size": [1, 0.75, 0.6],
            "confidence": 0.8, "evidence": "observed",
            "image_bbox": [0.2, 0.45, 0.4, 0.8],
            "floor_contact": [0.3, 0.8],
        },
        {
            "name": "右桌", "category": "table",
            "position": [0, 0, 0], "size": [1, 0.75, 0.6],
            "confidence": 0.8, "evidence": "observed",
            "image_bbox": [0.6, 0.45, 0.8, 0.8],
            "floor_contact": [0.7, 0.8],
        },
        {
            "name": "左桌重复", "category": "table",
            "position": [0, 0, 0], "size": [1, 0.75, 0.6],
            "confidence": 0.8, "evidence": "observed",
            "image_bbox": [0.2, 0.45, 0.4, 0.8],
            "floor_contact": [0.3, 0.8],
        },
        {
            "name": "左桌配套椅", "category": "chair",
            "position": [0, 0, 0], "size": [0.5, 0.85, 0.5],
            "confidence": 0.8, "evidence": "observed",
            "image_bbox": [0.25, 0.5, 0.4, 0.8],
            "floor_contact": [0.3, 0.8],
        },
        {
            "name": "侧窗", "category": "window", "wall": "back",
            "position": [0, 2.4, 5.5], "size": [2, 1.2, 0.1],
            "confidence": 0.8, "evidence": "observed",
            "image_bbox": [0.2, 0.15, 0.45, 0.45],
            "sill_height_m": 0.9,
        },
    ],
}
projection_normalized = nodes._normalize_reconstruction_layout(
    projection_layout, known_room_width_m=0, max_objects=12,
    image_aspect_ratio=16 / 9)
check("editable 3d: photographed contacts preserve furniture spacing",
      projection_normalized["objects"][0]["position"][0]
      != projection_normalized["objects"][1]["position"][0]
      and projection_normalized["objects"][0]["position"][0] < 0
      < projection_normalized["objects"][1]["position"][0]
      and all(item["projection_source"] == "image_contact"
              for item in projection_normalized["objects"][:2]))
check("editable 3d: duplicate observations drop and chair clears table",
      len(projection_normalized["objects"]) == 4
      and projection_normalized["objects"][2]["category"] == "chair"
      and projection_normalized["objects"][2]["position"][2]
      != projection_normalized["objects"][0]["position"][2]
      and projection_normalized["objects"][2]["interaction_anchor"]["position"][2]
      == projection_normalized["objects"][2]["position"][2])
check("editable 3d: window image anchor corrects bad model Y",
      0.45 <= projection_normalized["objects"][3]["position"][1] <= 1.5
      and projection_normalized["objects"][3]["position"][1] != 2.4
      and projection_normalized["objects"][3]["position"][2] == 6
      and projection_normalized["objects"][3]["image_bbox"]
      == [0.2, 0.15, 0.45, 0.45])
edge_layout = {
    "room": {"width": 12, "depth": 12, "height": 3, "confidence": 0.8},
    "camera": projection_layout["camera"],
    "objects": [
        {
            "name": "边界桌1", "category": "table",
            "position": [6, 0, 0], "size": [1, 0.75, 0.6],
            "confidence": 0.8, "evidence": "observed",
            "image_bbox": [0, 0.45, 0.04, 0.65],
            "floor_contact": [0.02, 0.65],
        },
        {
            "name": "边界桌2", "category": "table",
            "position": [6, 0, 0], "size": [1, 0.75, 0.6],
            "confidence": 0.8, "evidence": "observed",
            "image_bbox": [0.02, 0.45, 0.06, 0.65],
            "floor_contact": [0.04, 0.65],
        },
    ],
}
edge_normalized = nodes._normalize_reconstruction_layout(
    edge_layout, known_room_width_m=0, max_objects=12,
    image_aspect_ratio=16 / 9)
check("editable 3d: distinct edge observations do not collapse",
      edge_normalized["objects"][0]["position"][0]
      != edge_normalized["objects"][1]["position"][0]
      and all(abs(item["position"][0]) <= 5.5
              for item in edge_normalized["objects"]))
edge_samples = [
    editable_scene._inset_bound(value, 5.5)
    for value in (5.44, 5.45, 5.5, 5.500001, 6.0)
]
check("editable 3d: soft edge bound stays continuous and monotonic",
      edge_samples == sorted(edge_samples)
      and max(edge_samples) <= 5.5
      and edge_samples[3] - edge_samples[2] < 0.001)
dense_objects = []
for row_index, contact_y in enumerate((0.85, 0.72, 0.59, 0.46)):
    for column_index, contact_x in enumerate((0.3, 0.7)):
        instance = row_index * 2 + column_index + 1
        table_bbox = [
            contact_x - 0.11, contact_y - 0.18,
            contact_x + 0.11, contact_y,
        ]
        chair_bbox = [
            contact_x - 0.06, contact_y - 0.14,
            contact_x + 0.06, min(0.98, contact_y + 0.03),
        ]
        dense_objects.extend([
            {
                "name": f"课桌 {instance}", "category": "table",
                "instance_id": f"table-{instance}",
                "position": [0, 0, 0], "size": [1.2, 0.75, 0.6],
                "confidence": 0.9, "evidence": "observed",
                "image_bbox": table_bbox,
                "floor_contact": [contact_x, contact_y],
            },
            {
                "name": f"椅子 {instance}", "category": "chair",
                "instance_id": f"chair-{instance}",
                "paired_instance_id": f"table-{instance}",
                "position": [0, 0, 0], "size": [0.5, 0.85, 0.5],
                "confidence": 0.88, "evidence": "observed",
                "image_bbox": chair_bbox,
                "floor_contact": [contact_x, min(0.98, contact_y + 0.03)],
            },
        ])
for duplicate_index in range(6):
    source_table = dense_objects[duplicate_index * 2]
    source_chair = dense_objects[duplicate_index * 2 + 1]
    for source, suffix in ((source_table, "桌"), (source_chair, "椅")):
        duplicate = dict(source)
        duplicate["name"] = f"重复{suffix} {duplicate_index + 1}"
        duplicate["confidence"] = 0.55
        duplicate["image_bbox"] = [
            source["image_bbox"][0] + 0.004,
            source["image_bbox"][1] + 0.004,
            source["image_bbox"][2] + 0.004,
            source["image_bbox"][3] + 0.004,
        ]
        duplicate["floor_contact"] = [
            source["floor_contact"][0] + 0.004,
            source["floor_contact"][1] + 0.004,
        ]
        dense_objects.append(duplicate)
dense_layout = {
    "room": {"width": 8, "depth": 8, "height": 3, "confidence": 0.82},
    "camera": projection_layout["camera"],
    "objects": dense_objects,
}
dense_normalized = nodes._normalize_reconstruction_layout(
    dense_layout, known_room_width_m=0, max_objects=36,
    image_aspect_ratio=16 / 9)
dense_tables = [
    item for item in dense_normalized["objects"] if item["category"] == "table"]
dense_chairs = [
    item for item in dense_normalized["objects"] if item["category"] == "chair"]
dense_quality = dense_normalized["layout_quality"]
check("editable 3d: dense furniture observations deduplicate by image instance",
      len(dense_tables) == 8
      and len(dense_chairs) == 8
      and dense_quality["input_furniture"] == 28
      and dense_quality["output_furniture"] == 16
      and dense_quality["deduplicated_furniture"] == 12)
check("editable 3d: dense furniture uses one-to-one table-chair pairs",
      dense_quality["table_chair_pairs"] == 8
      and len({item["pair_id"] for item in dense_tables}) == 8
      and all(item.get("paired_entity_id") for item in dense_chairs))
explicit_cross_layout = {
    "room": {"width": 8, "depth": 8, "height": 3, "confidence": 0.8},
    "camera": projection_layout["camera"],
    "objects": [
        {
            "name": "显式桌 1", "category": "table",
            "instance_id": "table-1",
            "position": [-2, 0, 0], "size": [1.2, 0.75, 0.6],
            "confidence": 0.8, "evidence": "observed",
            "floor_contact": [0.2, 0.7],
        },
        {
            "name": "显式桌 2", "category": "table",
            "instance_id": "table-2",
            "position": [2, 0, 0], "size": [1.2, 0.75, 0.6],
            "confidence": 0.8, "evidence": "observed",
            "floor_contact": [0.8, 0.7],
        },
        {
            "name": "显式椅 1", "category": "chair",
            "instance_id": "chair-1", "paired_instance_id": "table-1",
            "position": [2, 0, 0], "size": [0.5, 0.85, 0.5],
            "confidence": 0.8, "evidence": "observed",
            "floor_contact": [0.8, 0.72],
        },
        {
            "name": "显式椅 2", "category": "chair",
            "instance_id": "chair-2", "paired_instance_id": "table-2",
            "position": [-2, 0, 0], "size": [0.5, 0.85, 0.5],
            "confidence": 0.8, "evidence": "observed",
            "floor_contact": [0.2, 0.72],
        },
    ],
}
explicit_cross_normalized = nodes._normalize_reconstruction_layout(
    explicit_cross_layout, max_objects=12, image_aspect_ratio=16 / 9)
explicit_cross = {
    item["instance_id"]: item for item in explicit_cross_normalized["objects"]
}
check("editable 3d: unique chair-declared pairs override geometric proximity",
      explicit_cross["table-1"]["paired_entity_id"]
      == explicit_cross["chair-1"]["id"]
      and explicit_cross["table-2"]["paired_entity_id"]
      == explicit_cross["chair-2"]["id"])
oversized_pair_layout = {
    "room": {"width": 1, "depth": 2, "height": 3, "confidence": 0.8},
    "camera": projection_layout["camera"],
    "objects": [
        {
            "name": "超宽桌", "category": "table",
            "instance_id": "wide-table", "paired_instance_id": "wide-chair",
            "position": [0, 0, 0], "size": [1, 0.75, 0.6],
            "yaw_degrees": 45,
            "confidence": 0.8, "evidence": "observed",
            "floor_contact": [0.5, 0.7],
        },
        {
            "name": "超宽椅", "category": "chair",
            "instance_id": "wide-chair", "paired_instance_id": "wide-table",
            "position": [0, 0, 0], "size": [0.5, 0.85, 0.5],
            "yaw_degrees": 45,
            "confidence": 0.8, "evidence": "observed",
            "floor_contact": [0.9, 0.72],
        },
    ],
}
oversized_pair = nodes._normalize_reconstruction_layout(
    oversized_pair_layout, max_objects=12, image_aspect_ratio=1)
check("editable 3d: oversized furniture pair is scaled inside room",
      all(
          abs(item["position"][0])
          + editable_scene._rotated_footprint(
              item["size"], item["yaw_degrees"])[0] <= 0.5 + 1e-6
          and abs(item["position"][2])
          + editable_scene._rotated_footprint(
              item["size"], item["yaw_degrees"])[1] <= 1.0 + 1e-6
          for item in oversized_pair["objects"]
      )
      and any("桌椅组合超出房间" in warning
              for warning in oversized_pair["warnings"]))
check("editable 3d: global furniture solver removes non-pair overlaps",
      dense_quality["initial_furniture_overlaps"] > 0
      and dense_quality["residual_furniture_overlaps"] == 0)
dense_rows = {}
for table in dense_tables:
    row = round(table["floor_contact"][1], 2)
    dense_rows.setdefault(row, []).append(table)
check("editable 3d: global furniture solver preserves image ordering",
      all(
          sorted(row_tables, key=lambda item: item["floor_contact"][0])[0]["position"][0]
          < sorted(row_tables, key=lambda item: item["floor_contact"][0])[1]["position"][0]
          for row_tables in dense_rows.values()
      )
      and [
          sum(item["position"][2] for item in row_tables) / len(row_tables)
          for _, row_tables in sorted(dense_rows.items(), reverse=True)
      ] == sorted(
          sum(item["position"][2] for item in row_tables) / len(row_tables)
          for row_tables in dense_rows.values()
      ))
check("editable 3d: global furniture solver stays inside room",
      all(
          abs(item["position"][0])
          + editable_scene._rotated_footprint(
              item["size"], item["yaw_degrees"])[0] <= 4.0 + 1e-6
          and abs(item["position"][2])
          + editable_scene._rotated_footprint(
              item["size"], item["yaw_degrees"])[1] <= 4.0 + 1e-6
          for item in dense_normalized["objects"]
      ))
conflicting_id_layout = {
    "room": {"width": 8, "depth": 8, "height": 3, "confidence": 0.8},
    "camera": projection_layout["camera"],
    "objects": [
        {
            "name": "左侧同名桌", "category": "table", "instance_id": "table-1",
            "position": [-2, 0, -2], "size": [1.2, 0.75, 0.6],
            "confidence": 0.8, "evidence": "observed",
            "image_bbox": [0.05, 0.65, 0.25, 0.85],
            "floor_contact": [0.15, 0.85],
        },
        {
            "name": "右侧同名桌", "category": "table", "instance_id": "table-1",
            "position": [2, 0, 2], "size": [1.2, 0.75, 0.6],
            "confidence": 0.8, "evidence": "observed",
            "image_bbox": [0.65, 0.35, 0.85, 0.55],
            "floor_contact": [0.75, 0.55],
        },
    ],
}
conflicting_id_normalized = nodes._normalize_reconstruction_layout(
    conflicting_id_layout, max_objects=12, image_aspect_ratio=16 / 9)
check("editable 3d: conflicting duplicate IDs do not delete distant furniture",
      len(conflicting_id_normalized["objects"]) == 2
      and any("instance_id 重复但图像位置冲突" in warning
              for warning in conflicting_id_normalized["warnings"]))
unobserved_layout = {
    "room": {"width": 8, "depth": 8, "height": 3, "confidence": 0.8},
    "camera": projection_layout["camera"],
    "objects": [
        {
            "name": "无观测前桌", "category": "table",
            "position": [-2, 0, -2], "size": [1.2, 0.75, 0.6],
            "confidence": 0.7, "evidence": "inferred",
        },
        {
            "name": "无观测后桌", "category": "table",
            "position": [2, 0, 2], "size": [1.2, 0.75, 0.6],
            "confidence": 0.7, "evidence": "inferred",
        },
    ],
}
unobserved_normalized = nodes._normalize_reconstruction_layout(
    unobserved_layout, max_objects=12, image_aspect_ratio=16 / 9)
check("editable 3d: unobserved furniture keeps model depth separation",
      unobserved_normalized["objects"][0]["position"][2] == -2
      and unobserved_normalized["objects"][1]["position"][2] == 2
      and unobserved_normalized["layout_quality"]["furniture_rows"] == 0)
overfull_layout = {
    "room": {"width": 2, "depth": 2, "height": 3, "confidence": 0.8},
    "camera": projection_layout["camera"],
    "objects": [
        {
            "name": f"拥挤桌 {index}", "category": "table",
            "position": [0, 0, 0], "size": [1.2, 0.75, 0.6],
            "confidence": 0.8, "evidence": "observed",
            "image_bbox": [0.2, 0.1 * index, 0.8, 0.1 * index + 0.08],
            "floor_contact": [0.5, 0.1 * index + 0.08],
        }
        for index in range(5)
    ],
}
overfull_normalized = nodes._normalize_reconstruction_layout(
    overfull_layout, max_objects=12, image_aspect_ratio=1)
check("editable 3d: residual overlaps report non-positive clearance",
      overfull_normalized["layout_quality"]["residual_furniture_overlaps"] > 0
      and overfull_normalized["layout_quality"]["minimum_furniture_clearance_m"] <= 0)
ray_camera = {
    "position": [0, 1.7, -5],
    "target": [0, 1.2, 0],
    "fov_degrees": 55,
}
left_ray = editable_scene._image_ray([0.25, 0.75], ray_camera, 16 / 9)
right_ray = editable_scene._image_ray([0.75, 0.75], ray_camera, 16 / 9)
check("editable 3d: image axes map rightward and downward",
      left_ray[0] < 0 < right_ray[0]
      and left_ray[1] < 0 and right_ray[1] < 0)
rotated_pair_layout = {
    "room": {"width": 12, "depth": 12, "height": 3, "confidence": 0.8},
    "camera": projection_layout["camera"],
    "objects": [
        {
            "name": "边界桌", "category": "table",
            "position": [0, 0, 5], "size": [1, 0.75, 0.6],
            "yaw_degrees": 0, "confidence": 0.8, "evidence": "observed",
        },
        {
            "name": "旋转边界椅", "category": "chair",
            "position": [0, 0, 5], "size": [1.2, 0.85, 0.4],
            "yaw_degrees": 90, "confidence": 0.8, "evidence": "observed",
        },
    ],
}
rotated_pair = nodes._normalize_reconstruction_layout(
    rotated_pair_layout, known_room_width_m=0, max_objects=12)
rotated_chair = rotated_pair["objects"][1]
rotated_chair_half_z = rotated_chair["size"][0] * 0.5
check("editable 3d: separated rotated chair remains in room",
      abs(rotated_chair["position"][2]) + rotated_chair_half_z <= 6 + 1e-6)
editable_scene, editable_camera, editable_manifest = nodes._build_scene_documents(
    normalized_editable, "hunyuan-vision-1.5-instruct", "req-editable", ["abc"])
check("editable 3d: room shell and semantic objects generated",
      len(editable_scene["objects"]) == 6
      and len(editable_manifest["entities"]) == 6
      and editable_manifest["interaction_anchors"][0]["type"] == "surface"
      and editable_scene["appearance"]["export_mode"] == "semantic")
parsed_editable_scene = nodes._parse_previs_scene(
    json.dumps(editable_scene, ensure_ascii=False))
check("editable 3d: semantic category survives previs parsing",
      parsed_editable_scene["objects"][4]["semantic"]["category"] == "table"
      and "projection_source" in parsed_editable_scene["objects"][4]["semantic"]
      and editable_manifest["layout_quality"]
      == normalized_editable["layout_quality"])
check("editable 3d: reference camera generated",
      editable_camera["active_camera"] == "camera-reference"
      and len(editable_camera["cameras"][0]["keyframes"]) == 2)
editable_glb = nodes._build_editable_scene_glb(editable_manifest["entities"])
check("editable 3d: valid GLB header and declared length",
      editable_glb[:4] == b"glTF"
      and int.from_bytes(editable_glb[4:8], "little") == 2
      and int.from_bytes(editable_glb[8:12], "little") == len(editable_glb))
captured_box_vertices = []
orig_previs_project = nodes._previs_project
nodes._previs_project = lambda point, camera, width, height: (
    captured_box_vertices.append(point) or (point[0], point[1], 1.0))
try:
    fake_draw = types.SimpleNamespace(polygon=lambda *args, **kwargs: None)
    nodes._previs_draw_box(
        fake_draw,
        {"scale": [2, 2, 4], "rotation": [0, numpy.pi / 2, 0]},
        [0, 0, 0], {}, 100, 100, (128, 128, 128), False)
finally:
    nodes._previs_project = orig_previs_project
check("editable 3d: fallback renderer uses base origin and yaw",
      min(point[1] for point in captured_box_vertices) == 0
      and max(point[1] for point in captured_box_vertices) == 2
      and round(max(abs(point[0]) for point in captured_box_vertices), 6) == 2
      and round(max(abs(point[2]) for point in captured_box_vertices), 6) == 1)
cleanup_dir = tempfile.mkdtemp(prefix="vod-previs-cleanup-")
listed_upload = os.path.join(cleanup_dir, "listed.jpg")
partial_upload = os.path.join(cleanup_dir, "partial.jpg")
for cleanup_path in (listed_upload, partial_upload):
    with open(cleanup_path, "wb") as handle:
        handle.write(b"test")
nodes._previs_cleanup_uploads([listed_upload], cleanup_dir)
check("previs: upload cleanup removes untracked partial files",
      not os.path.exists(cleanup_dir))
with nodes._PREVIS_JOB_LOCK:
    nodes._PREVIS_RECONSTRUCTION_JOBS.clear()
    for index in range(20):
        nodes._PREVIS_RECONSTRUCTION_JOBS[f"{index:032x}"] = {
            "status": "complete",
            "scene_json": "{}",
            "_updated_at": float(index + 1),
        }
    nodes._previs_prune_reconstruction_jobs_locked(now=20)
    retained_reconstruction_jobs = len(nodes._PREVIS_RECONSTRUCTION_JOBS)
nodes._PREVIS_RECONSTRUCTION_JOBS.clear()
check("previs: completed reconstruction jobs stay memory-bounded",
      retained_reconstruction_jobs == nodes._MAX_PREVIS_RECONSTRUCTION_JOBS)

class FakeImageFrame:
    def __init__(self, value):
        self.value = value

    def cpu(self):
        return self

    def numpy(self):
        return self.value


class FakeImageBatch:
    def __init__(self, values):
        self.values = values
        self.shape = (len(values),) + values[0].shape

    def __getitem__(self, index):
        return FakeImageFrame(self.values[index])


try:
    nodes.TencentVODImageToEditable3DScene().reconstruct(
        FakeImageBatch([numpy.zeros((32, 48, 3), dtype=numpy.float32)]),
        "教室", 0, 12, "Disabled")
    check("editable 3d: explicit paid confirmation required", False)
except ValueError as error:
    check("editable 3d: explicit paid confirmation required",
          "confirm_paid_request" in str(error), str(error))

captured_editable = {}
orig_call_editable = nodes._call_api
orig_resolve_editable = nodes._resolve_secret_pair
nodes._resolve_secret_pair = lambda secret_id, secret_key: ("AKIDx", "sk")
def fake_editable_call(sid, sk, reg, ep, action, payload, version="", service="", timeout=60):
    captured_editable.update({
        "endpoint": ep, "action": action, "payload": payload,
        "version": version, "service": service, "timeout": timeout,
    })
    return {
        "RequestId": "req-editable-flow",
        "Choices": [{
            "FinishReason": "stop",
            "Message": {"Content": json.dumps(editable_raw, ensure_ascii=False)},
        }],
    }
nodes._call_api = fake_editable_call
try:
    editable_result = nodes.TencentVODImageToEditable3DScene().reconstruct(
        FakeImageBatch([numpy.zeros((32, 48, 3), dtype=numpy.float32)]),
        "教室", 8, 12, "Enabled", filename="test_editable_scene")
    check("editable 3d: Hunyuan Vision TC3 request",
          captured_editable["action"] == "ChatCompletions"
          and captured_editable["version"] == "2023-09-01"
          and captured_editable["service"] == "hunyuan"
          and captured_editable["timeout"] == 240
          and captured_editable["endpoint"] == "hunyuan.ai.tencentcloudapi.com"
          and captured_editable["payload"]["Messages"][0]["Contents"][1][
              "ImageUrl"]["Url"].startswith("data:image/jpeg;base64,"))
    check("editable 3d: node emits scene/camera/manifest and collision GLB",
          isinstance(editable_result[0], FakeFile3D)
          and os.path.isfile(editable_result[1])
          and json.loads(editable_result[2])["version"] == 3
          and json.loads(editable_result[3])["version"] == 3
          and json.loads(editable_result[4])["generator"].endswith("structured-whitebox")
          and os.path.isfile(editable_result[5])
          and isinstance(editable_result[6], FakeFile3D)
          and "可直接连接 3D 白模预演台" in editable_result[7])
finally:
    nodes._call_api = orig_call_editable
    nodes._resolve_secret_pair = orig_resolve_editable

scene_3d = nodes._parse_previs_scene(nodes._DEFAULT_PREVIS_SCENE)
camera_3d = nodes._parse_previs_camera(nodes._DEFAULT_PREVIS_CAMERA)
check("previs: default scene parsed",
      scene_3d["version"] == 3
      and len(scene_3d["objects"]) == 3
      and {item["type"] for item in scene_3d["objects"]} == {"actor", "box"}
      and all("motion_track" in item for item in scene_3d["objects"])
      and scene_3d["appearance"]["export_mode"] == "semantic"
      and all("appearance" in item for item in scene_3d["objects"]))
styled_scene = nodes._parse_previs_scene(json.dumps({
    "appearance": {
        "preview_mode": "wireframe",
        "export_mode": "director",
        "sky_color": "#123456",
        "auto_actor_colors": False,
    },
    "objects": [{
        "id": "actor-styled", "type": "actor",
        "appearance": {"color": "#ABCDEF", "opacity": 0.5},
    }],
}))
check("previs: appearance normalized",
      styled_scene["appearance"]["preview_mode"] == "wireframe"
      and styled_scene["appearance"]["sky_color"] == "#123456"
      and not styled_scene["appearance"]["auto_actor_colors"]
      and styled_scene["objects"][0]["appearance"]
      == {"color": "#abcdef", "opacity": 0.5})
try:
    nodes._parse_previs_scene(json.dumps({
        "appearance": {"sky_color": "red"},
        "objects": [],
    }))
    check("previs: invalid appearance color rejected", False)
except ValueError as e:
    check("previs: invalid appearance color rejected", "RRGGBB" in str(e), str(e))
path_scene = nodes._parse_previs_scene(json.dumps({"objects": [{
    "id": "actor-path", "name": "曲线路径人物", "type": "actor",
    "position": [0, 0, 0], "end": [4, 0, 4], "scale": [1, 1, 1],
    "path": [
        {"time": 0, "position": [0, 0, 0]},
        {"time": 0.5, "position": [2, 0, 0]},
        {"time": 1, "position": [4, 0, 4]},
    ],
}]}))
check("previs: object path interpolates piecewise",
      nodes._previs_object_position(path_scene["objects"][0], 0.25) == [1.0, 0.0, 0.0]
      and nodes._previs_object_position(path_scene["objects"][0], 0.75) == [3.0, 0.0, 2.0])
bezier_scene = nodes._parse_previs_scene(json.dumps({"objects": [{
    "type": "actor", "scale": [1, 1, 1],
    "motion_track": {
        "interpolation": "bezier", "speed_mode": "keyframed",
        "points": [
            {"time": 0, "position": [0, 0, 0], "out_handle": [0, 0, 2]},
            {"time": 1, "position": [2, 0, 0], "in_handle": [2, 0, 2]},
        ],
    },
}]}))
check("previs: cubic bezier track",
      nodes.np.allclose(nodes._previs_object_position(
          bezier_scene["objects"][0], 0.5), [1.0, 0.0, 1.5]))
constant_scene = nodes._parse_previs_scene(json.dumps({"objects": [{
    "type": "box", "scale": [1, 1, 1],
    "motion_track": {
        "interpolation": "linear", "speed_mode": "constant",
        "speed_description": "匀速",
        "points": [
            {"time": 0, "position": [0, 0, 0]},
            {"time": 0.9, "position": [1, 0, 0]},
            {"time": 1, "position": [10, 0, 0]},
        ],
    },
}]}))
check("previs: constant speed uses arc length",
      nodes.np.allclose(nodes._previs_object_position(
          constant_scene["objects"][0], 0.5), [5.0, 0.0, 0.0], atol=0.02))
try:
    nodes._parse_previs_scene(json.dumps({"objects": [{
        "type": "box", "path": [
            {"time": 0.5, "position": [0, 0, 0]},
            {"time": 0.5, "position": [1, 0, 1]},
        ],
    }]}))
    check("previs: duplicate path times rejected", False)
except ValueError as e:
    check("previs: duplicate path times rejected", "严格递增" in str(e), str(e))
mid_camera = nodes._previs_camera_at(camera_3d, 0.5)
check("previs: camera interpolates",
      mid_camera["position"] == [5.25, 3.65, 7.25]
      and mid_camera["target"] == [0.25, 1.0, 0.0]
      and mid_camera["fov"] == 45.0, str(mid_camera))
legacy_camera = nodes._parse_previs_camera(json.dumps({
    "keyframes": [{"time": 0, "position": [1, 2, 3], "target": [0, 0, 0], "fov": 50}]
}))
check("previs: legacy camera migrates to v3",
      legacy_camera["version"] == 3
      and legacy_camera["active_camera"] == "camera-1"
      and legacy_camera["cameras"][0]["keyframes"][0]["position"] == [1.0, 2.0, 3.0]
      and "position_track" in legacy_camera["cameras"][0]
      and legacy_camera["cuts"] == [{"time": 0.0, "camera_id": "camera-1"}],
      str(legacy_camera))
multi_camera = nodes._parse_previs_camera(json.dumps({
    "version": 2,
    "active_camera": "wide",
    "cameras": [
        {"id": "wide", "name": "广角", "keyframes": [
            {"time": 0, "position": [8, 5, 9], "target": [0, 1, 0], "fov": 55}]},
        {"id": "close", "name": "近景", "keyframes": [
            {"time": 0.5, "position": [2, 2, 3], "target": [0, 1, 0], "fov": 32},
            {"time": 1, "position": [1, 2, 2], "target": [0, 1, 0], "fov": 28}]},
    ],
    "cuts": [{"time": 0, "camera_id": "wide"}, {"time": 0.5, "camera_id": "close"}],
}))
check("previs: cuts select camera",
      nodes._previs_camera_for_time(multi_camera, 0.25)["id"] == "wide"
      and nodes._previs_camera_for_time(multi_camera, 0.75)["id"] == "close"
      and nodes._previs_camera_at(multi_camera, 0.75)["position"] == [1.5, 2.0, 2.5])
check("previs: multi-camera prompt includes cuts",
      "切镜计划" in nodes._previs_reference_prompt(scene_3d, multi_camera)
      and "近景" in nodes._previs_reference_prompt(scene_3d, multi_camera))
try:
    nodes._parse_previs_scene('{"objects":[{"type":"mesh"}]}')
    check("previs: unsupported object rejected", False)
except ValueError as e:
    check("previs: unsupported object rejected", "type" in str(e), str(e))
if hasattr(nodes.Image, "new") and hasattr(nodes.np, "asarray"):
    previs_frames = nodes._render_previs_images(
        scene_3d, camera_3d, 320, 180, 3, background_asset="world.spz")
    check("previs: renders image sequence",
          len(previs_frames) == 3 and all(frame.size == (320, 180) for frame in previs_frames))
    check("previs: reference prompt generated",
          "镜头" in nodes._previs_reference_prompt(scene_3d, camera_3d))
    empty_styled_scene = nodes._parse_previs_scene(json.dumps({
        "appearance": {"sky_color": "#123456", "ground_visible": False},
        "objects": [],
    }))
    styled_frame = nodes._render_previs_images(
        empty_styled_scene, camera_3d, 64, 64, 2, show_overlay=False)[0]
    check("previs: fallback renderer applies sky style",
          styled_frame.getpixel((0, 0)) == (18, 52, 86),
          str(styled_frame.getpixel((0, 0))))
    changed_appearance_scene = json.loads(json.dumps(scene_3d))
    changed_appearance_scene["appearance"]["actor_color"] = "#112233"
    preview_only_scene = json.loads(json.dumps(scene_3d))
    preview_only_scene["appearance"]["preview_mode"] = "wireframe"
    preview_only_scene["appearance"]["preset"] = "custom"
    check("previs: appearance participates in cache signature",
          nodes._previs_manifest_signature(
              scene_3d, camera_3d, {"source": "Blank"})
          != nodes._previs_manifest_signature(
              changed_appearance_scene, camera_3d, {"source": "Blank"}))
    check("previs: preview style excluded from cache signature",
          nodes._previs_manifest_signature(
              scene_3d, camera_3d, {"source": "Blank"})
          == nodes._previs_manifest_signature(
              preview_only_scene, camera_3d, {"source": "Blank"}))
    try:
        import torch as _torch  # noqa: F401
    except ImportError:
        pass
    else:
        rendered = nodes.TencentVOD3DPrevis().render(
            nodes._DEFAULT_PREVIS_SCENE, nodes._DEFAULT_PREVIS_CAMERA,
            frame_count=2, width=320, height=176)
        check("previs: node outputs ComfyUI IMAGE batch",
              tuple(rendered[0].shape) == (2, 176, 320, 3)
              and json.loads(rendered[1])["version"] == 3
              and bool(rendered[3])
              and isinstance(rendered[4], FakeVideo)
              and rendered[5] == "", str(tuple(rendered[0].shape)))

        import tempfile as _tempfile_previs
        with _tempfile_previs.TemporaryDirectory() as cache_root:
            original_output = nodes.folder_paths.get_output_directory
            nodes.folder_paths.get_output_directory = lambda: cache_root
            try:
                render_id = "a" * 32
                render_dir = nodes._previs_root("previs_renders", render_id)
                os.makedirs(render_dir)
                frame_names = []
                for index in range(2):
                    name = f"frame_{index:04d}.png"
                    nodes.Image.new("RGB", (320, 176), (index * 20, 30, 40)).save(
                        os.path.join(render_dir, name))
                    frame_names.append(name)
                cached_background = {
                    "source": "Blank",
                    "path": "",
                    "taskId": "",
                    "transform": {
                        "position": [0, 0, 0],
                        "rotation": [0, 0, 0],
                        "scale": 1,
                    },
                }
                manifest_path = os.path.join(render_dir, "manifest.json")
                with open(manifest_path, "w", encoding="utf-8") as manifest_file:
                    json.dump({
                        "version": 1,
                        "frame_count": 2,
                        "width": 320,
                        "height": 176,
                        "fps": 24.0,
                        "signature": nodes._previs_manifest_signature(
                            scene_3d, camera_3d, cached_background),
                        "frames": frame_names,
                    }, manifest_file)
                cached = nodes._previs_load_render_cache(
                    manifest_path, scene_3d, camera_3d, 2, 320, 176, 24,
                    cached_background)
                check("previs: WebGL render cache loads",
                      len(cached) == 2 and all(image.size == (320, 176) for image in cached))
                rendered_cached = nodes.TencentVOD3DPrevis().render(
                    nodes._DEFAULT_PREVIS_SCENE, nodes._DEFAULT_PREVIS_CAMERA,
                    frame_count=2, width=320, height=176, render_cache_path=manifest_path)
                check("previs: node consumes WebGL render cache",
                      tuple(rendered_cached[0].shape) == (2, 176, 320, 3)
                      and float(rendered_cached[0][1, 0, 0, 0]) > 0)
                try:
                    nodes._previs_load_render_cache(
                        manifest_path, {"version": 3, "objects": []},
                        camera_3d, 2, 320, 176, 24, cached_background)
                    check("previs: stale WebGL cache rejected", False)
                except ValueError as error:
                    check("previs: stale WebGL cache rejected", "已过期" in str(error), str(error))
                try:
                    changed_background = dict(cached_background)
                    changed_background["path"] = "/tmp/other.spz"
                    nodes._previs_load_render_cache(
                        manifest_path, scene_3d, camera_3d, 2, 320, 176, 24,
                        changed_background)
                    check("previs: stale background cache rejected", False)
                except ValueError as error:
                    check("previs: stale background cache rejected",
                          "已过期" in str(error), str(error))
            finally:
                nodes.folder_paths.get_output_directory = original_output


# ---- 汇总 ----
print()
print()
if failures:
    print(f"RESULT: {len(failures)} FAILED: {failures}")
    sys.exit(1)
print("RESULT: ALL PASS")

# ---- 16. OG Image2 模型（v1.10.0，文档 3.14）----
# 16.1 model 解析与新增参数
og_payload = nodes._build_image_payload("1500044236", "p", "OG image2_medium",
                                        {"storage_mode": "Permanent", "resolution": "1K", "aspect_ratio": "9:16",
                                         "output_image_count": 3, "output_format": "png"})
check("og: name/version parse", og_payload["ModelName"] == "OG" and og_payload["ModelVersion"] == "image2_medium")
check("og: OutputImageCount when >1", og_payload["OutputConfig"]["OutputImageCount"] == 3, str(og_payload))
check("og: OutputFormat passed", og_payload["OutputConfig"]["OutputFormat"] == "png")
# 16.2 count=1 / format 空 → 不传
og_payload2 = nodes._build_image_payload("1500044236", "p", "OG image2_high",
                                         {"storage_mode": "Temporary", "resolution": "1080P", "aspect_ratio": "1:1",
                                          "output_image_count": 1, "output_format": ""})
check("og: count=1 omitted", "OutputImageCount" not in og_payload2["OutputConfig"], str(og_payload2))
check("og: empty format omitted", "OutputFormat" not in og_payload2["OutputConfig"])
check("og: high tier version", og_payload2["ModelVersion"] == "image2_high")
# 16.3 多图输出流程：3 张 URL → 3 次下载，输出换行拼接
captured3 = {}
dl_calls = []
orig_dl3 = nodes._download_video
nodes._download_video = lambda url, task_id, on_progress=None, name_hint=None: (dl_calls.append(url) or "/tmp/g.png")
def fake_img_call3(secret_id, secret_key, region, endpoint, action, payload, version="", service=""):
    if action == "CreateAigcImageTask":
        return {"TaskId": "1500044236-AigcImageTask-multi01t"}
    return {"TaskType": "AigcImageTask", "Status": "FINISH",
            "AigcImageTask": {"Status": "FINISH", "Output": {"FileInfos": [
                {"FileUrl": "https://cdn/a1.png"}, {"FileUrl": "https://cdn/a2.png"},
                {"FileUrl": "https://cdn/a3.png"}]}}}
orig_call3 = nodes._call_api
nodes._call_api = fake_img_call3
try:
    node_obj = nodes.TencentVODAIGCImageTask()
    res3 = node_obj.generate("p", secret_id="AKIDx", secret_key="sk",
                                               sub_app_id="1500044236", model="OG image2_medium",
                                               storage_mode="Temporary", resolution="1K",
                                               aspect_ratio="9:16", output_image_count=3,
                                               poll_interval=3, timeout=60)
    check("og: 3 urls downloaded", len(dl_calls) == 3, str(dl_calls))
    tid, url_out, path_out, preview3 = res3["result"]
    check("og: outputs joined by newline", url_out.count("\n") == 2 and path_out.count("\n") == 2, repr(url_out))
    check("og: ui images for 3 files", len(res3["ui"]["images"]) == 3, str(res3))
finally:
    nodes._call_api = orig_call3
    nodes._download_video = orig_dl3

# ---- 17. 生图计费（v1.11.0）----
import tempfile as _tf2
_tmp2 = _tf2.mkdtemp()
# 17.1 配置读取 image_prices
open(os.path.join(_tmp2, "tencent-vod-config.json"), "w").write(json.dumps({
    "secret_id": "AKIDp", "secret_key": "sk-p", "sub_app_id": "1500044236",
    "image_prices": {"Jimeng 4.0": 0.1, "OG image2_medium": "0.25"}}))
cfg17 = nodes._load_config_file(_tmp2)
check("img-price: config loads", cfg17["image_prices"] == {"Jimeng 4.0": 0.1, "OG image2_medium": 0.25}, str(cfg17))
# 17.2 单价解析
nodes._load_config_file = lambda: {"secret_id": "x", "secret_key": "y", "sub_app_id": "1",
                                   "prices": {}, "image_prices": {"Jimeng 4.0": 0.1, "OG image2_medium": 0.25}}
check("img-price: per-model", nodes._image_price_for("Jimeng 4.0") == 0.1)
check("img-price: quality tier differs", nodes._image_price_for("OG image2_medium") == 0.25)
check("img-price: unknown -> 0", nodes._image_price_for("OG image2_high") == 0.0)
# 17.3 台账记录：model/image_count + 费用 = 张数 × 单价
rec17 = nodes._base_record("t2i", "猫", {"model": "OG image2_medium", "output_image_count": 3,
                                         "resolution": "1K"})
check("img-price: record fields", rec17["model"] == "OG image2_medium" and rec17["image_count"] == 3)
check("img-price: cost = count x price", rec17["estimated_cost"] == 0.75, str(rec17["estimated_cost"]))
rec17b = nodes._base_record("t2v", "视频", {"resolution": "1080P", "duration": 5})
check("img-price: video record no model", rec17b["model"] == "" and rec17b["image_count"] == 0)
nodes._load_config_file = _orig_cfg
# 17.4 保存合并 image_prices
p17 = nodes._save_config_file("AKIDs", "sk", "1500044236", image_prices={"GEM 3.0": 0.2},
                              path=os.path.join(_tmp2, "tencent-vod-config.json"))
saved17 = json.load(open(p17))
check("img-price: save merges", saved17["image_prices"] == {"Jimeng 4.0": 0.1, "OG image2_medium": 0.25, "GEM 3.0": 0.2},
      str(saved17["image_prices"]))
try:
    nodes._save_config_file("AKIDa", "sk", "1500044236", image_prices={"OG image2_low": "abc"},
                            path=os.path.join(_tmp2, "x.json"))
    check("img-price: bad price raises", False)
except ValueError as e:
    check("img-price: bad price raises", "生图单价" in str(e))

# ---- 18. 文件名与图片张量（v1.12.0）----
import tempfile as _tf3
_tmp3 = _tf3.mkdtemp()
# 18.1 _resolve_save_name：默认 / 自定义 / 补扩展名 / 重名序号
n1 = nodes._resolve_save_name("http://cdn/a/aigcImageGenFile.png", "1500044236-AigcImageTask-abc123t", "", _tmp3)
check("name: default uses task tail", n1.endswith("_aigcImageGenFile.png") and "abc123t" in n1, n1)
n2 = nodes._resolve_save_name("http://cdn/a/aigcImageGenFile.png", "t", "我的图", _tmp3)
check("name: hint keeps ext", n2 == "我的图_t.png", n2)
n3 = nodes._resolve_save_name("http://cdn/a/v.mp4", "t", "clip", _tmp3)
check("name: hint appends ext", n3 == "clip_t.mp4", n3)
open(os.path.join(_tmp3, "clip_t.mp4"), "w").close()
n4 = nodes._resolve_save_name("http://cdn/a/v.mp4", "t", "clip", _tmp3)
check("name: dup gets suffix", n4 == "clip_t_1.mp4", n4)
# 18.2 图片张量：测试环境（stub）下优雅返回 None
check("tensor: graceful None in stub env", nodes._paths_to_image_tensor(["/tmp/nonexist.png"]) is None)
# 18.3 图片节点 4 输出（preview_image 槽位）
img_node_meta = nodes.TencentVODAIGCImageTask
check("tensor: 4 outputs declared", len(img_node_meta.RETURN_TYPES) == 4 and img_node_meta.RETURN_NAMES[-1] == "preview_image")

# ---- 19. 素材路径解析（v1.12.3）----
check("path: absolute passthrough", nodes._resolve_media_path("/abs/a.mp4") == "/abs/a.mp4")
check("path: input prefix", nodes._resolve_media_path("input/ref.png") == "/tmp/comfy_input/ref.png")
check("path: output prefix", nodes._resolve_media_path("output/vod_aigc/x.mp4") == "/tmp/comfy_output/vod_aigc/x.mp4")
check("path: plain relative unchanged", nodes._resolve_media_path("ref.png") == "ref.png")
check("path: empty unchanged", nodes._resolve_media_path("") == "")
try:
    nodes._file_to_base64("input/nonexist.mp4", 1024, "参考视频")
    check("path: missing file raises", False)
except ValueError as e:
    check("path: missing file raises", "input/xxx" in str(e))

# ---- 20. URL 扩展名校验（v1.12.5）----
nodes._validate_media_url("https://x.com/a.mp4", nodes._ALLOWED_VIDEO_EXTS, "参考视频")  # 不抛
nodes._validate_media_url("https://x.com/a.mov?t=1", nodes._ALLOWED_VIDEO_EXTS, "参考视频")
nodes._validate_media_url("https://x.com/noext", nodes._ALLOWED_VIDEO_EXTS, "参考视频")  # 无扩展名不拦
check("url: mp4/mov/noext pass", True)
try:
    nodes._validate_media_url("https://x.com/a.m4s?e=1", nodes._ALLOWED_VIDEO_EXTS, "参考视频")
    check("url: m4s rejected", False)
except ValueError as e:
    check("url: m4s rejected", ".m4s" in str(e) and ".mp4" in str(e), str(e))
check("url: image ext pass", nodes._validate_media_url("https://x.com/a.png", nodes._ALLOWED_IMAGE_EXTS, "参考图") is None)
try:
    nodes._validate_media_url("https://x.com/a.mp4", nodes._ALLOWED_IMAGE_EXTS, "参考图")
    check("url: mp4 as image rejected", False)
except ValueError:
    check("url: mp4 as image rejected", True)
check("url: audio ext pass", nodes._validate_media_url("https://x.com/s.wav", nodes._ALLOWED_AUDIO_EXTS, "参考音频") is None)

# ---- 21. ~ 路径展开（v1.12.6）----
expanded = nodes._resolve_media_path("~/Downloads/青海摇_480p.mp4")
check("tilde: expands to home", expanded == os.path.join(os.path.expanduser("~"), "Downloads/青海摇_480p.mp4"), expanded)
check("tilde: resolves to existing file", os.path.isfile(expanded))
# 真实链路：~ 路径进 _file_to_base64 能加载
b64_tilde = nodes._file_to_base64("~/Downloads/青海摇_480p.mp4", 50 * 1024 * 1024, "参考视频")
check("tilde: loads via file_to_base64", len(b64_tilde) > 1000)
