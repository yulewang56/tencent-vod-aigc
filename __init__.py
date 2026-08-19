"""ComfyUI custom nodes for Tencent Cloud VOD AIGC (MiniMax Hailuo H3).

Registers VOD AIGC nodes under the "Tencent VOD AIGC" category.
Pure-stdlib API client (TC3-HMAC-SHA256 signing) — no extra pip packages needed.
"""

import os
import sys

# ComfyUI 以 spec_from_file_location 加载本文件（模块名 = 目录名 tencent-vod-aigc，
# 无点、非合法包名），相对导入会失败；这里显式把本目录加入 sys.path 后绝对导入。
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

WEB_DIRECTORY = "web"

try:
    from nodes import NODE_CLASS_MAPPINGS, NODE_DISPLAY_NAME_MAPPINGS
except Exception as _err:
    print("[comfyui-tencent-vod-aigc] 节点包加载失败，请检查 custom_nodes/tencent-vod-aigc 目录内容")
    print(f"[comfyui-tencent-vod-aigc] 错误详情: {_err}")
    raise

__all__ = ["NODE_CLASS_MAPPINGS", "NODE_DISPLAY_NAME_MAPPINGS"]
