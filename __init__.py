"""ComfyUI custom nodes for Tencent Cloud VOD AIGC (MiniMax Hailuo H3).

Registers VOD AIGC nodes under the "Tencent VOD AIGC" category.
Pure-stdlib API client (TC3-HMAC-SHA256 signing) — no extra pip packages needed.
"""

import importlib.util
import os
import sys

_ENTRY = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _ENTRY)  # 供本包 nodes.py 内部绝对导入 vod_aigc_core

# ComfyUI 主程序本身就是 nodes.py，已占用顶层模块名 "nodes"（sys.modules["nodes"]），
# 且本包目录名含连字符（tencent-vod-aigc）无法包式导入：
#   - 相对导入（from .nodes import ...）在 spec_from_file_location 的无点模块名下失败
#   - 绝对导入（from nodes import ...）会静默拿到 ComfyUI 全局注册表而非本包节点
# 因此用独立模块名显式从本目录加载 nodes.py。
_SPEC = importlib.util.spec_from_file_location("tencent_vod_aigc_nodes",
                                               os.path.join(_ENTRY, "nodes.py"))
_NODES = importlib.util.module_from_spec(_SPEC)
sys.modules[_SPEC.name] = _NODES
_SPEC.loader.exec_module(_NODES)

NODE_CLASS_MAPPINGS = _NODES.NODE_CLASS_MAPPINGS
NODE_DISPLAY_NAME_MAPPINGS = _NODES.NODE_DISPLAY_NAME_MAPPINGS
WEB_DIRECTORY = "web"

__all__ = ["NODE_CLASS_MAPPINGS", "NODE_DISPLAY_NAME_MAPPINGS"]
