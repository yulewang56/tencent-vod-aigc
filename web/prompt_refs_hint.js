// @N 引用提示：在 VS 视频生成 / H3 多模态参考节点的 prompt 输入框输入 @ 时，
// 弹出可引用的参考图候选（@1..@N），N 从图上 ref_images 上游节点推断（1 基，
// BatchImagesNode 的 image0 = 第 1 张 = @1）。点击候选插入到光标位置。
import { app } from "../../scripts/app.js";

const REF_NODES = new Set(["TencentVODVSVideoTask", "TencentVODH3ReferenceToVideo"]);

app.registerExtension({
  name: "TencentVODAIGC.PromptRefsHint",

  nodeCreated(node) {
    const cls = node.comfyClass || node.type || "";
    if (!REF_NODES.has(cls)) return;
    const widget = (node.widgets || []).find((w) => w.name === "prompt");
    if (!widget) return;

    // 兜底：包装 widget.callback（ComfyUI 在输入同步时调用，不依赖 DOM 创建时机）
    const origCallback = widget.callback;
    widget.callback = function (value, ...args) {
      onPromptChange(node, widget, null);
      return origCallback ? origCallback.apply(this, [value, ...args]) : undefined;
    };

    // 主路径：inputEl 就绪后绑定 DOM input 事件（提供精确光标位置）
    const bindDom = () => {
      const el = widget.inputEl;
      if (!el) return false;
      el.addEventListener("input", () => {
        onPromptChange(node, widget, el);
      });
      return true;
    };
    if (!bindDom()) {
      const timer = setInterval(() => {
        if (bindDom()) clearInterval(timer);
      }, 200);
      setTimeout(() => clearInterval(timer), 10000); // 10s 后放弃（DOM 仍未创建则靠 callback 兜底）
    }

    node.onRemoved = (() => {
      const orig = node.onRemoved;
      return (...args) => {
        closeHint();
        return orig ? orig.apply(node, args) : undefined;
      };
    })();
  },
});

let hintEl = null;

function closeHint() {
  if (hintEl) {
    hintEl.remove();
    hintEl = null;
  }
}

// 输入变化：光标前是「@」或「@数字」（输入中）时显示候选；否则关闭
function onPromptChange(node, widget, el) {
  const text = el ? el.value : (widget.value || "");
  const caret = el ? el.selectionStart : text.length;
  const before = text.slice(0, caret);
  const m = /@(\d*)$/.exec(before);
  if (!m) {
    closeHint();
    return;
  }
  const names = inferRefImageNames(node);
  if (names == null) {
    showHint(el || widget.inputEl, m, "无法推断参考图数量：ref_images 未连接或上游为动态数量（可手动输入 @1..@N）");
    return;
  }
  showCandidates(el || widget.inputEl, m, names);
}

// 从图上推断 ref_images 上游的参考图文件名列表（推断不出用 null 占位；无上游返回 null）
function inferRefImageNames(node) {
  const graph = node.graph;
  if (!graph) return null;
  const input = (node.inputs || []).find((i) => i.name === "ref_images");
  if (!input || input.link == null) return null;
  const links = graph.links instanceof Map ? graph.links : new Map(graph.links || []);
  const link = links.get(input.link);
  if (!link) return null;
  const src = graph.getNodeById(link.origin_id);
  if (!src) return null;
  const cls = src.comfyClass || src.type || "";

  const nameOf = (up) => {
    if (!up) return null;
    return (up.comfyClass || up.type || "") === "LoadImage" ? (up.widgets_values?.[0] || null) : null;
  };

  if (cls === "LoadImage") return [src.widgets_values?.[0] || null];
  if (cls === "BatchImagesNode" || cls === "ImageBatch" || cls === "ImageConcatenate") {
    // 端口命名：BatchImagesNode=images.imageN；内置 ImageBatch=imageN
    const names = [];
    for (let i = 0; i < 32; i++) {
      const inp = (src.inputs || []).find((x) => x.name === `images.image${i}` || x.name === `image${i}`);
      if (!inp || inp.link == null) break; // 顺序端口：遇未连接即停（假定连续连接）
      const upLink = links.get(inp.link);
      const up = upLink ? graph.getNodeById(upLink.origin_id) : null;
      names.push(nameOf(up));
    }
    return names.length ? names : null;
  }
  // LoadImages 等动态数量节点：返回 null，由运行时确定
  return null;
}

function showCandidates(el, m, names) {
  closeHint();
  const items = [];
  for (let i = 1; i <= names.length; i++) {
    const label = names[i - 1] ? `@${i}（${names[i - 1]}）` : `@${i}（第 ${i} 张参考图）`;
    items.push(label);
  }
  buildHint(el, m, items, (idx) => `@${idx + 1}`);
}

function showHint(el, m, text) {
  closeHint();
  buildHint(el, m, [text], null);
}

function buildHint(el, m, items, makeValue) {
  if (!el) return;
  const rect = el.getBoundingClientRect();
  const box = document.createElement("div");
  box.style.cssText =
    "position:fixed;z-index:99999;min-width:200px;max-height:260px;overflow:auto;" +
    "background:#1e1e22;color:#e8e8e8;border:1px solid #4a4a52;border-radius:8px;" +
    "padding:4px 0;font:12px/1.5 -apple-system,'PingFang SC','Microsoft YaHei',sans-serif;" +
    "box-shadow:0 8px 30px rgba(0,0,0,.55)";
  box.style.left = Math.min(rect.left, window.innerWidth - 220) + "px";
  box.style.top = rect.bottom + 4 + "px";

  items.forEach((label, idx) => {
    const item = document.createElement("div");
    item.textContent = label;
    item.style.cssText =
      "padding:5px 12px;cursor:pointer;white-space:nowrap;" +
      "display:flex;justify-content:space-between;gap:16px";
    item.addEventListener("mouseenter", () => {
      item.style.background = "#2d2d33";
    });
    item.addEventListener("mouseleave", () => {
      item.style.background = "transparent";
    });
    if (makeValue) {
      item.addEventListener("mousedown", (ev) => {
        ev.preventDefault(); // 保持 textarea 焦点
        insertRef(el, m, makeValue(idx));
      });
    } else {
      item.style.cursor = "default";
      item.style.color = "#9a9aa2";
    }
    box.appendChild(item);
  });

  document.body.appendChild(box);
  hintEl = box;
}

// 替换光标前未完成的「@数字」前缀并插入引用
function insertRef(el, m, value) {
  const start = el.selectionStart;
  const replaceStart = start - m[0].length;
  const valuePart = value.split("（")[0]; // "@N"
  el.value = el.value.slice(0, replaceStart) + valuePart + el.value.slice(start);
  const caret = replaceStart + valuePart.length;
  el.selectionStart = el.selectionEnd = caret;
  el.dispatchEvent(new Event("input", { bubbles: true }));
  closeHint();
}
