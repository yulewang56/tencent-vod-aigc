"""VS 计价表测试（model_price_tables）：表解析 / 两段求和 / 旧字段兼容 / currency /
台账 has_video_ref 传递。自包含风格，stub 不联网；权威数据源 = tencent-vod-config.example.json
（测试直接从 example 文件加载，保证配置与实现同步）。
"""
import json
import os
import sys
import tempfile
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

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)
import nodes
import vod_aigc_core as core

failures = []

def check(name, cond, detail=""):
    if cond:
        print(f"  PASS  {name}")
    else:
        failures.append(name)
        print(f"  FAIL  {name}  {detail}")

# ---- 0. example 配置即权威数据源 ----
example_cfg = core.load_config(os.path.join(REPO, "tencent-vod-config.example.json"))
check("example: currency cny", example_cfg["currency"] == "cny")
check("example: VS table with 4 versions",
      set((example_cfg["model_price_tables"].get("VS") or {}).get("versions", {}))
      == {"2.5", "2.0", "2.0-fast", "2.0-mini"})

# ---- 1. video_price_for：cny 表查询 ----
def p(v, res, ref):
    return core.video_price_for("VS " + v, v, res, ref, example_cfg)

check("pricing: 2.5 no-ref 720P = 1.512", p("2.5", "720P", False) == 1.512)
check("pricing: 2.5 with-ref 720P = 0.908+0.938", p("2.5", "720P", True) == 0.908 + 0.938)
check("pricing: 2.0 no-ref 1080P = 2.5", p("2.0", "1080P", False) == 2.5)
check("pricing: 2.0 with-ref 720P = 0.610+0.610", p("2.0", "720P", True) == 0.610 + 0.610)
check("pricing: 2.0-fast no-ref 4K = 1.728", p("2.0-fast", "4K", False) == 1.728)
check("pricing: 2.0-mini with-ref 1080P = 0.31+0.465", p("2.0-mini", "1080P", True) == 0.31 + 0.465)
# 版本 / 分辨率 / 币种缺失 → 0
check("pricing: unknown version -> 0", core.video_price_for("VS", "9.9", "720P", False, example_cfg) == 0.0)
check("pricing: unknown resolution -> 0", p("2.5", "768P", False) == 0.0)
cfg_str = {"model_price_tables": {"VS": {"versions": {"2.5": {"cny": {"no_video_ref": {"720P": "1.512"}}}}}}}
check("pricing: string price coerced", core.video_price_for("VS 2.5", "2.5", "720P", False, cfg_str) == 1.512)
check("pricing: VS bare name + separate version",
      core.video_price_for("VS", "2.5", "720P", False, example_cfg) == 1.512)
check("pricing: VS full name wins over empty version",
      core.video_price_for("VS 2.0", "", "1080P", False, example_cfg) == 2.5)

# ---- 2. currency：usd 表 / 不跨币种 ----
cfg_usd = dict(example_cfg)
cfg_usd["currency"] = "usd"
check("pricing: usd no-ref 720P = 0.2912", core.video_price_for("VS 2.5", "2.5", "720P", False, cfg_usd) == 0.2912)
check("pricing: usd with-ref 720P = 0.1383+0.1383",
      core.video_price_for("VS 2.5", "2.5", "720P", True, cfg_usd) == 0.1383 + 0.1383)
cfg_mixed = {"currency": "usd", "model_price_tables": {"VS": {"versions": {"2.5": {"cny": {"no_video_ref": {"720P": 1.512}}}}}}}
check("pricing: no cross-currency fallback", core.video_price_for("VS 2.5", "2.5", "720P", False, cfg_mixed) == 0.0)
# load_config：currency 解析（大写归一、缺失默认 cny、None 空结构）
_tmp = tempfile.mkdtemp()
_cfgp = os.path.join(_tmp, "c.json")
json.dump({"secret_id": "a", "currency": "USD",
           "model_price_tables": {"VS": {"versions": {}}}}, open(_cfgp, "w"))
c_usd = core.load_config(_cfgp)
check("config: currency normalized to usd", c_usd["currency"] == "usd" and "VS" in c_usd["model_price_tables"])
json.dump({"secret_id": "a"}, open(_cfgp, "w"))
check("config: currency defaults cny", core.load_config(_cfgp)["currency"] == "cny")
check("config: empty cfg has currency/tables keys",
      core.load_config(None)["currency"] == "cny" and core.load_config(None)["model_price_tables"] == {})

# ---- 3. 旧字段兼容 ----
cfg_legacy = {"prices": {"1080P": 0.2}}
check("pricing: Hailuo falls back to prices", core.video_price_for("Hailuo", "H3", "1080P", False, cfg_legacy) == 0.2)
check("pricing: empty model falls back", core.video_price_for("", "", "1080P", False, cfg_legacy) == 0.2)
check("pricing: has_video_ref ignored for legacy", core.video_price_for("Hailuo", "H3", "1080P", True, cfg_legacy) == 0.2)
check("pricing: VS without table -> 0 (no fallback)", core.video_price_for("VS 2.5", "2.5", "1080P", False, cfg_legacy) == 0.0)
# 旧式配置文件（无 currency/model_price_tables）经 load_config 后仍可用
json.dump({"secret_id": "a", "secret_key": "b", "sub_app_id": "1",
           "prices": {"1080P": 0.2}}, open(_cfgp, "w"))
cfg_old = core.load_config(_cfgp)
check("pricing: old-style config still works",
      cfg_old["currency"] == "cny" and cfg_old["model_price_tables"] == {}
      and core.video_price_for("Hailuo", "H3", "1080P", False, cfg_old) == 0.2)

# ---- 4. estimate_cost / base_record 扩展不破坏旧调用 ----
check("pricing: estimate_cost legacy unchanged", core.estimate_cost("1080P", 3, cfg_legacy) == (5, 1.0))
check("pricing: estimate_cost legacy keyword-safe",
      core.estimate_cost("1080P", 3, cfg_legacy, model_name="", model_version="", has_video_ref=False) == (5, 1.0))
sec, cost = core.estimate_cost("720P", 8, example_cfg, model_name="VS 2.5", model_version="2.5", has_video_ref=True)
check("pricing: estimate_cost VS with-ref 8s", (sec, cost) == (8, round(8 * 1.846, 4)), (sec, cost))
sec2, cost2 = core.estimate_cost("720P", 8, example_cfg, model_name="VS 2.5", model_version="2.5")
check("pricing: estimate_cost VS no-ref 8s", (sec2, cost2) == (8, round(8 * 1.512, 4)), (sec2, cost2))
rec_old = core.base_record("t2v", "p", {"resolution": "1080P", "duration": 8}, cfg=cfg_legacy)
check("pricing: base_record legacy cost unchanged",
      rec_old["estimated_cost"] == 1.6 and rec_old["model"] == "" and rec_old["seconds_billed"] == 8)
rec_vs = core.base_record("t2v", "p", {"resolution": "720P", "duration": 8, "model": "VS 2.5",
                                       "model_version": "2.5", "has_video_ref": True}, cfg=example_cfg)
check("pricing: base_record VS cost + model field",
      rec_vs["estimated_cost"] == round(8 * 1.846, 4) and rec_vs["model"] == "VS 2.5", str(rec_vs))
rec_i2v = core.base_record("i2v", "p", {"resolution": "720P", "duration": 8, "model": "VS 2.5",
                                        "model_version": "2.5", "has_video_ref": False}, cfg=example_cfg)
check("pricing: i2v no-ref uses single segment", rec_i2v["estimated_cost"] == round(8 * 1.512, 4))
check("pricing: base_record last_frame passthrough",
      core.base_record("t2v", "p", {"resolution": "1080P", "duration": 5,
                                    "last_frame_url": "https://x/l.png", "last_frame_path": "/tmp/l.png"},
                       cfg=cfg_legacy)["last_frame_url"] == "https://x/l.png")
rec_h3 = core.base_record("t2v", "p", {"resolution": "1080P", "duration": 5}, cfg=cfg_legacy)
check("pricing: legacy record has no last_frame key", "last_frame_url" not in rec_h3)

# ---- 5. 两个待复核点（example 按上表原值落盘，README 已标注；这里断言表内原值）----
vs_tables = example_cfg["model_price_tables"]["VS"]["versions"]
out_20_cny = vs_tables["2.0"]["cny"]["with_video_ref"]["output"]
check("review#1: 2.0 cny output 1080P == 2K == 1.820 (待复核)",
      out_20_cny["1080P"] == 1.820 and out_20_cny["2K"] == 1.820)
out_mini_usd = vs_tables["2.0-mini"]["usd"]["with_video_ref"]["output"]
check("review#2: 2.0-mini usd output 2K=0.0521 < 1080P=0.0584 (待复核)",
      out_mini_usd["2K"] == 0.0521 and out_mini_usd["1080P"] == 0.0584)

# ---- 6. VS 节点台账：has_video_ref 传递（带视频参考 vs 不带）----
hpath = "/tmp/comfy_output/vod_aigc/execution_history.jsonl"
if os.path.exists(hpath):
    os.remove(hpath)
records = []
orig_load = nodes._load_config_file
orig_call = nodes._call_api
orig_dl = nodes._download_video
orig_append = nodes._append_history
nodes._load_config_file = lambda: dict(example_cfg)
nodes._append_history = lambda rec: records.append(rec)
def fake_pricing_api(sid, sk, reg, ep, action, payload, version="", service=""):
    if action == "CreateAigcVideoTask":
        return {"TaskId": "1500044236-AigcVideoTask-price001t"}
    return {"TaskType": "AigcVideoTask", "Status": "FINISH",
            "AigcVideoTask": {"Status": "FINISH", "Output": {"FileInfos": [
                {"FileUrl": "https://cdn/p.mp4", "UsageType": ""}]}}}
nodes._call_api = fake_pricing_api
nodes._download_video = lambda url, task_id, on_progress=None, name_hint=None: "/tmp/comfy_output/vod_aigc/p.mp4"
base_params = {"secret_id": "x", "secret_key": "y", "sub_app_id": "1500044236",
               "model_version": "2.5", "duration": 8, "resolution": "720P", "aspect_ratio": "16:9",
               "audio_generation": "Enabled", "seed": -1, "logo_add": "Disabled",
               "high_bitrate": "Disabled", "return_last_frame": "Disabled",
               "storage_mode": "Temporary", "use_cache": "Disabled", "media_name": "",
               "filename": "", "region": "ap-guangzhou", "endpoint": "",
               "input_region": "", "poll_interval": 3, "timeout": 60}
try:
    # 带视频参考 → 两段求和：8 × (0.908+0.938) = 14.768
    nodes.TencentVODVSVideoTask().generate("带视频参考", **{**base_params, "ref_video_urls": "https://x/v.mp4"})
    rec = records[-1]
    check("pricing node: with video ref two-segment sum",
          rec["estimated_cost"] == round(8 * 1.846, 4) and rec["model"] == "VS 2.5"
          and rec["mode"] == "r2v", json.dumps(rec, ensure_ascii=False))
    # 只有图片参考 → 无参考视频单价：8 × 1.512 = 12.096
    nodes.TencentVODVSVideoTask().generate("图片参考", **{**base_params, "ref_image_urls": "https://x/i.png"})
    rec2 = records[-1]
    check("pricing node: image-only ref single segment",
          rec2["estimated_cost"] == round(8 * 1.512, 4), str(rec2["estimated_cost"]))
    # 无素材（文生视频）→ 无参考视频单价
    nodes.TencentVODVSVideoTask().generate("文生视频", **base_params)
    rec3 = records[-1]
    check("pricing node: t2v single segment",
          rec3["estimated_cost"] == round(8 * 1.512, 4) and rec3["mode"] == "t2v",
          str(rec3["estimated_cost"]))
    # 视频参考 + 首帧并存 → 仍两段求和
    nodes.TencentVODVSVideoTask().generate("视频+首帧", **{**base_params,
                                                         "ref_video_urls": "https://x/v.mp4",
                                                         "first_frame_url": "https://x/f.png"})
    rec4 = records[-1]
    check("pricing node: video ref + first frame still two-segment",
          rec4["estimated_cost"] == round(8 * 1.846, 4), str(rec4["estimated_cost"]))
finally:
    nodes._load_config_file = orig_load
    nodes._call_api = orig_call
    nodes._download_video = orig_dl
    nodes._append_history = orig_append

# ---- 汇总 ----
print()
print()
if failures:
    print(f"RESULT: {len(failures)} FAILED: {failures}")
    sys.exit(1)
print("RESULT: ALL PASS")
