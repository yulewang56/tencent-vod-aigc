// 首次使用弹窗：添加 VOD 节点且凭据未配置时，引导填写并保存到 credentials.json。
// 密钥只经 HTTP 写入服务端节点包目录（gitignore 排除），不进入工作流 JSON。
import { app } from "../../scripts/app.js";

const REQUIRES_CREDS = /^TencentVOD/;
const NO_CREDS_NEEDED = new Set(["TencentVODAIGCViewHistory", "TencentVODAIGCDownloadVideo"]);

app.registerExtension({
  name: "TencentVODAIGC.CredentialsSetup",

  async nodeCreated(node) {
    const cls = node.comfyClass || node.type || "";
    if (!REQUIRES_CREDS.test(cls) || NO_CREDS_NEEDED.has(cls)) return;
    try {
      const resp = await fetch("/tencent-vod-aigc/credentials/status");
      const data = await resp.json();
      if (!data.configured) showSetupModal();
    } catch (e) {
      /* 接口不可用时静默跳过，不影响节点使用 */
    }
  },
});

let prompted = false; // 每个会话只弹一次，避免打扰

function showSetupModal() {
  if (prompted) return;
  prompted = true;

  const overlay = document.createElement("div");
  overlay.style.cssText =
    "position:fixed;inset:0;z-index:99999;background:rgba(0,0,0,.55);" +
    "display:flex;align-items:center;justify-content:center";

  const box = document.createElement("div");
  box.style.cssText =
    "width:460px;max-width:92vw;background:#1e1e22;color:#e8e8e8;" +
    "border:1px solid #4a4a52;border-radius:10px;padding:18px 20px;" +
    "font:13px/1.6 -apple-system,'PingFang SC','Microsoft YaHei',sans-serif;" +
    "box-shadow:0 10px 40px rgba(0,0,0,.6)";

  const title = document.createElement("div");
  title.textContent = "首次使用：配置腾讯云 VOD 凭据";
  title.style.cssText = "font-weight:600;font-size:15px;margin-bottom:8px";

  const desc = document.createElement("div");
  desc.style.cssText = "color:#a8a8b0;margin-bottom:14px";
  desc.innerHTML =
    "节点上的 SecretId / SecretKey / SubAppId 均为选填（留空自动读取 credentials.json 或环境变量）。" +
    "建议在这里填写一次，密钥只会保存到 <code>custom_nodes/tencent-vod-aigc/credentials.json</code>" +
    "（已 gitignore，不会进入工作流 JSON），无需在每个节点重复填写。";

  const label = (text) => {
    const el = document.createElement("div");
    el.textContent = text;
    el.style.cssText = "margin:10px 0 4px;color:#c8c8d0";
    return el;
  };

  const input = (type, placeholder) => {
    const el = document.createElement("input");
    el.type = type;
    el.placeholder = placeholder;
    el.style.cssText =
      "width:100%;box-sizing:border-box;background:#26262c;color:#e8e8e8;" +
      "border:1px solid #4a4a52;border-radius:6px;padding:7px 10px;font:13px monospace";
    return el;
  };

  const secretId = input("text", "SecretId（AKID…）");
  const secretKey = input("password", "SecretKey");
  const subAppId = input("text", "SubAppId（纯数字）");

  const errorLine = document.createElement("div");
  errorLine.style.cssText = "color:#ff7b7b;margin-top:8px;min-height:18px;font-size:12px";

  const buttons = document.createElement("div");
  buttons.style.cssText = "display:flex;gap:8px;justify-content:flex-end;margin-top:14px";

  const makeBtn = (text, primary) => {
    const btn = document.createElement("button");
    btn.textContent = text;
    btn.style.cssText =
      (primary
        ? "background:#2d6cdf;color:#fff;border:1px solid #2d6cdf;"
        : "background:transparent;color:#a8a8b0;border:1px solid #4a4a52;") +
      "border-radius:6px;padding:7px 16px;cursor:pointer;font:13px sans-serif";
    return btn;
  };

  const saveBtn = makeBtn("保存凭据", true);
  const laterBtn = makeBtn("稍后再说", false);

  saveBtn.addEventListener("click", async () => {
    saveBtn.disabled = true;
    saveBtn.textContent = "保存中…";
    try {
      const resp = await fetch("/tencent-vod-aigc/credentials", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          secret_id: secretId.value.trim(),
          secret_key: secretKey.value.trim(),
          sub_app_id: subAppId.value.trim(),
        }),
      });
      const data = await resp.json();
      if (data.ok) {
        errorLine.style.color = "#7bd88f";
        errorLine.textContent = "✅ 已保存到 " + data.path.split("/custom_nodes/")[1] + "，现在可以直接运行节点了。";
        laterBtn.textContent = "关闭";
      } else {
        errorLine.style.color = "#ff7b7b";
        errorLine.textContent = data.error || "保存失败（HTTP " + resp.status + "）";
        saveBtn.disabled = false;
        saveBtn.textContent = "保存凭据";
      }
    } catch (e) {
      errorLine.textContent = "保存失败：" + e.message;
      saveBtn.disabled = false;
      saveBtn.textContent = "保存凭据";
    }
  });

  laterBtn.addEventListener("click", () => overlay.remove());

  box.append(title, desc, label("SecretId"), secretId, label("SecretKey"), secretKey,
    label("SubAppId"), subAppId, errorLine, buttons);
  buttons.append(saveBtn, laterBtn);
  overlay.appendChild(box);
  document.body.appendChild(overlay);
}
