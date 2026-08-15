"""Verify comfyui-tencent-vod-aigc node pack loads and core logic works."""
import json
import os
import sys
import types

# ---- stub ComfyUI/numpy/PIL dependencies (not needed at import time) ----
comfy = types.ModuleType("comfy")
folder_paths = types.ModuleType("comfy.folder_paths")
folder_paths.get_output_directory = lambda: "/tmp/comfy_output"
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

# ---- 1. node registry ----
expected = ["TencentVODH3TextToVideo", "TencentVODH3ImageToVideo",
            "TencentVODH3ReferenceToVideo", "TencentVODAIGCImageTask",
            "TencentVODAIGCQueryTask", "TencentVODAIGCDownloadVideo",
            "TencentVODAIGCViewHistory"]
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
def fake_call(secret_id, secret_key, region, endpoint, action, payload):
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
def fake_call_nested(sid, sk, reg, ep, action, payload):
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

def fake_success_api(sid, sk, reg, ep, action, payload):
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
def fake_reject(secret_id, secret_key, region, endpoint, action, payload):
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
def fake_img_call(secret_id, secret_key, region, endpoint, action, payload):
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
    tid, url, path, preview = node_obj.generate("一只猫在窗边", secret_id="AKIDx", secret_key="sk",
                                       sub_app_id="1500044236", ref_image=FakeTensor(),
                                       model="Jimeng 4.0", storage_mode="Temporary",
                                       resolution="1080P", aspect_ratio="16:9",
                                       poll_interval=3, timeout=60)
    check("t2i: flow returns", tid == "1500044236-AigcImageTask-abc123t" and path == "/tmp/fake.png")
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

# ---- 汇总 ----
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
def fake_img_call3(secret_id, secret_key, region, endpoint, action, payload):
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
    tid, url_out, path_out, preview3 = node_obj.generate("p", secret_id="AKIDx", secret_key="sk",
                                               sub_app_id="1500044236", model="OG image2_medium",
                                               storage_mode="Temporary", resolution="1K",
                                               aspect_ratio="9:16", output_image_count=3,
                                               poll_interval=3, timeout=60)
    check("og: 3 urls downloaded", len(dl_calls) == 3, str(dl_calls))
    check("og: outputs joined by newline", url_out.count("\n") == 2 and path_out.count("\n") == 2, repr(url_out))
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
check("name: hint keeps ext", n2 == "我的图.png", n2)
n3 = nodes._resolve_save_name("http://cdn/a/v.mp4", "t", "clip", _tmp3)
check("name: hint appends ext", n3 == "clip.mp4", n3)
open(os.path.join(_tmp3, "clip.mp4"), "w").close()
n4 = nodes._resolve_save_name("http://cdn/a/v.mp4", "t", "clip", _tmp3)
check("name: dup gets suffix", n4 == "clip_1.mp4", n4)
# 18.2 图片张量：测试环境（stub）下优雅返回 None
check("tensor: graceful None in stub env", nodes._paths_to_image_tensor(["/tmp/nonexist.png"]) is None)
# 18.3 图片节点 4 输出（preview_image 槽位）
img_node_meta = nodes.TencentVODAIGCImageTask
check("tensor: 4 outputs declared", len(img_node_meta.RETURN_TYPES) == 4 and img_node_meta.RETURN_NAMES[-1] == "preview_image")
