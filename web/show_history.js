// 台账查看节点的前端显示扩展：执行完成后把台账文本显示在右下角浮窗。
// 文本字段做 HTML 转义，视频 URL 与本地文件输出为可点击链接。
import { app } from "../../scripts/app.js";

app.registerExtension({
  name: "TencentVODAIGC.ViewHistory",

  async setup() {
    const POPUP_ID = "vod-aigc-history-popup";

    const escapeHtml = (value) =>
      String(value ?? "").replace(/[&<>"']/g, (ch) => ({
        "&": "&amp;",
        "<": "&lt;",
        ">": "&gt;",
        '"': "&quot;",
        "'": "&#39;",
      })[ch]);

    const link = (href, label) =>
      `<a href="${escapeHtml(href)}" target="_blank" rel="noopener" ` +
      `style="color:#5da9ff;text-decoration:underline">${escapeHtml(label)}</a>`;

    const showText = (text) => {
      let div = document.getElementById(POPUP_ID);
      if (!div) {
        div = document.createElement("div");
        div.id = POPUP_ID;
        div.style.cssText =
          "position:fixed;right:16px;bottom:56px;z-index:9999;" +
          "max-width:720px;max-height:65vh;overflow:auto;" +
          "background:rgba(24,24,27,.97);color:#e8e8e8;" +
          "border:1px solid #4a4a52;border-radius:8px;" +
          "padding:10px 14px;font:12px/1.65 ui-monospace,Menlo,monospace;" +
          "white-space:pre-wrap;box-shadow:0 6px 24px rgba(0,0,0,.55)";

        const closeBtn = document.createElement("button");
        closeBtn.textContent = "×";
        closeBtn.style.cssText =
          "position:sticky;top:0;float:right;margin-left:8px;" +
          "background:transparent;border:none;color:#9a9aa2;cursor:pointer;" +
          "font:bold 16px/1 sans-serif";
        closeBtn.addEventListener("click", () => div.remove());
        div.appendChild(closeBtn);

        const body = document.createElement("div");
        body.id = POPUP_ID + "-body";
        div.appendChild(body);

        document.body.appendChild(div);
      }
      const body = document.getElementById(POPUP_ID + "-body");
      if (body) body.innerHTML = text;
      div.scrollTop = 0;
    };

    // 把台账纯文本渲染成带链接的 HTML：识别每条记录中的 URL 字段
    const renderLedgerHtml = (rawText) => {
      const lines = String(rawText).split("\n");
      return lines
        .map((line) => {
          const trimmed = line.trim();
          if (!trimmed) return "";
          const urlMatch = trimmed.match(/(https?:\/\/[^\s|]+)/);
          const viewMatch = trimmed.match(/\| (\/view\?[^\s|]+)$/);
          const escaped = escapeHtml(trimmed);
          if (urlMatch && viewMatch) {
            return escaped
              .replace(escapeHtml(urlMatch[1]), link(urlMatch[1], "视频URL"))
              .replace(escapeHtml(viewMatch[1]), link(viewMatch[1], "本地文件"));
          }
          if (urlMatch) {
            return escaped.replace(escapeHtml(urlMatch[1]), link(urlMatch[1], "视频URL"));
          }
          return escaped;
        })
        .join("\n");
    };

    app.api.addEventListener("executed", (event) => {
      const detail = event.detail;
      if (!detail || !detail.output) return;
      const text = detail.output.text;
      if (!text) return;
      const joined = Array.isArray(text) ? text.join("\n") : String(text);
      if (joined && joined.trim()) showText(renderLedgerHtml(joined));
    });
  },
});
