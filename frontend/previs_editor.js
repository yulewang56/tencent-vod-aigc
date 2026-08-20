import { app } from "../../scripts/app.js";
import * as THREE from "three";
import { OrbitControls } from "three/addons/controls/OrbitControls.js";
import { TransformControls } from "three/addons/controls/TransformControls.js";
import { GLTFLoader } from "three/addons/loaders/GLTFLoader.js";
import { OBJLoader } from "three/addons/loaders/OBJLoader.js";
import { PLYLoader } from "three/addons/loaders/PLYLoader.js";
import { SparkRenderer, SplatMesh } from "@sparkjsdev/spark";

const NODE_TYPE = "TencentVOD3DPrevis";
const STYLE_ID = "tencent-vod-previs-style";
const EPSILON = 1e-6;
const MAX_CAMERAS = 8;
const PREVIS_REQUEST_HEADER = { "X-Tencent-VOD-AIGC": "previs" };
const TRACK_INTERPOLATIONS = ["linear", "catmull_rom", "bezier"];
const SPEED_MODES = ["keyframed", "constant", "ease_in", "ease_out", "ease_in_out", "custom"];
const ARC_LENGTH_LUT_CACHE = new WeakMap();
const DEFAULT_BACKGROUND_TRANSFORM = {
  position: [0, 0, 0],
  rotation: [0, 0, 0],
  scale: 1,
};

const DEFAULT_TRACK = {
  interpolation: "linear",
  speed_mode: "keyframed",
  speed_description: "",
  speed_curve: [{ x: 0, y: 0 }, { x: 1, y: 1 }],
};

const DEFAULT_SCENE = {
  version: 3,
  objects: [{
    id: "actor-1",
    name: "主角",
    type: "actor",
    position: [-1.5, 0, 0],
    end: [1.5, 0, 0],
    scale: [1, 1, 1],
    motion: "walk",
  }],
};

const DEFAULT_KEYFRAMES = [
  { time: 0, position: [7, 4.5, 9], target: [0, 1, 0], fov: 48, roll: 0 },
  { time: 1, position: [3.5, 2.8, 5.5], target: [0.5, 1, 0], fov: 42, roll: 0 },
];

const DEFAULT_CAMERA = {
  version: 3,
  active_camera: "camera-1",
  cameras: [{ id: "camera-1", name: "Camera 1", keyframes: DEFAULT_KEYFRAMES }],
  cuts: [{ time: 0, camera_id: "camera-1" }],
};

app.registerExtension({
  name: "TencentVODAIGC.PrevisEditor",
  beforeRegisterNodeDef(nodeType, nodeData) {
    if (nodeData?.name !== NODE_TYPE) return;
    const originalOnConfigure = nodeType.prototype.onConfigure;
    nodeType.prototype.onConfigure = function (...args) {
      const result = originalOnConfigure?.apply(this, args);
      queueMicrotask(() => migrateNodeDefaults(this));
      return result;
    };
  },
  nodeCreated(node) {
    const cls = node.comfyClass || node.type || "";
    if (cls !== NODE_TYPE) return;
    migrateNodeDefaults(node);
    node.addWidget("button", "打开 3D 预演编辑器", null, () => openEditor(node), {
      serialize: false,
    });
    node.size = [Math.max(node.size?.[0] || 320, 420), node.size?.[1] || 560];
  },
});

function migrateNodeDefaults(node) {
  const fpsWidget = node.widgets?.find((item) => item.name === "fps");
  if (fpsWidget && (fpsWidget.value === null || fpsWidget.value === undefined || fpsWidget.value === "")) {
    fpsWidget.value = 24;
  }
}

function ensureTheme() {
  const param = new URLSearchParams(window.location.search).get("clawpilotTheme");
  const theme =
    param || (window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light");
  document.documentElement.setAttribute("data-theme", theme);
  if (document.getElementById(STYLE_ID)) return;
  const style = document.createElement("style");
  style.id = STYLE_ID;
  style.textContent = `
:root {
  color-scheme: light;
  --cp-bg: #f7f4ef;
  --cp-bg-elevated: #fcfbf8;
  --cp-surface: #ffffff;
  --cp-surface-soft: #f5f5f5;
  --cp-border: #dedede;
  --cp-border-strong: #919191;
  --cp-text: #242424;
  --cp-text-muted: #5c5c5c;
  --cp-text-soft: #6f6f6f;
  --cp-accent: #b11f4b;
  --cp-accent-hover: #9a1a41;
  --cp-accent-soft: rgba(177, 31, 75, 0.08);
  --cp-accent-fg: #ffffff;
  --cp-success: #16a34a;
  --cp-danger: #dc2626;
  --cp-warning: #f59e0b;
  --cp-link: #0078d4;
  --cp-shadow: 0 18px 48px rgba(0, 0, 0, 0.12);
  --cp-overlay: rgba(255, 255, 255, 0.8);
  --cp-panel: rgba(255, 255, 255, 0.86);
  --cp-panel-strong: rgba(255, 255, 255, 0.96);
  --cp-sheen: rgba(255, 255, 255, 0.55);
  --cp-highlight: rgba(177, 31, 75, 0.12);
}
html[data-theme="dark"] {
  color-scheme: dark;
  --cp-bg: #3d3b3a;
  --cp-bg-elevated: #343231;
  --cp-surface: #292929;
  --cp-surface-soft: #2e2e2e;
  --cp-border: #474747;
  --cp-border-strong: #5f5f5f;
  --cp-text: #dedede;
  --cp-text-muted: #919191;
  --cp-text-soft: #b0b0b0;
  --cp-accent: #fd8ea1;
  --cp-accent-hover: #fb7b91;
  --cp-accent-soft: rgba(253, 142, 161, 0.14);
  --cp-accent-fg: #1a1a1a;
  --cp-success: #4ade80;
  --cp-danger: #f87171;
  --cp-warning: #fbbf24;
  --cp-link: #4da6ff;
  --cp-shadow: 0 18px 48px rgba(0, 0, 0, 0.32);
  --cp-overlay: rgba(41, 41, 41, 0.88);
  --cp-panel: rgba(41, 41, 41, 0.72);
  --cp-panel-strong: rgba(41, 41, 41, 0.96);
  --cp-sheen: rgba(255, 255, 255, 0.04);
  --cp-highlight: rgba(253, 142, 161, 0.12);
}
.vod-previs {
  position: fixed;
  inset: 0;
  z-index: 100000;
  padding: 12px;
  box-sizing: border-box;
  background: var(--cp-overlay);
  color: var(--cp-text);
  font-family: "Segoe UI", Aptos, Calibri, -apple-system, BlinkMacSystemFont, sans-serif;
}
.vod-previs * { box-sizing: border-box; }
.vod-previs__window {
  width: 100%;
  height: 100%;
  display: grid;
  grid-template-rows: auto minmax(0, 1fr) auto auto;
  overflow: hidden;
  background: var(--cp-bg-elevated);
  border: 1px solid var(--cp-border);
  border-radius: 14px;
  box-shadow: var(--cp-shadow);
}
.vod-previs__header, .vod-previs__footer {
  min-height: 52px;
  display: flex;
  align-items: center;
  gap: 14px;
  padding: 9px 14px;
  background: var(--cp-surface);
  border-bottom: 1px solid var(--cp-border);
}
.vod-previs__footer {
  min-height: 48px;
  justify-content: flex-end;
  border-top: 1px solid var(--cp-border);
  border-bottom: 0;
}
.vod-previs__title { font-size: 16px; font-weight: 650; }
.vod-previs__subtitle { color: var(--cp-text-muted); font-size: 12px; }
.vod-previs__mode-tabs, .vod-previs__inspector-tabs {
  display: flex;
  align-items: center;
  gap: 4px;
}
.vod-previs__mode-tabs {
  margin-inline: auto;
  padding: 3px;
  background: var(--cp-surface-soft);
  border: 1px solid var(--cp-border);
  border-radius: 0.625rem;
}
.vod-previs__header-actions {
  display: flex;
  align-items: center;
  gap: 6px;
}
.vod-previs__body {
  min-height: 0;
  display: grid;
  grid-template-columns: minmax(560px, 1fr) 380px;
}
.vod-previs__viewport {
  position: relative;
  min-width: 0;
  min-height: 0;
  overflow: hidden;
  background: var(--cp-bg);
  border-right: 1px solid var(--cp-border);
}
.vod-previs__canvas-host, .vod-previs__monitor-host {
  position: absolute;
  inset: 0;
}
.vod-previs__canvas-host canvas, .vod-previs__monitor-host canvas {
  width: 100%;
  height: 100%;
  display: block;
}
.vod-previs__toolbar {
  position: absolute;
  z-index: 3;
  top: 10px;
  left: 84px;
  right: 10px;
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 5px;
  pointer-events: none;
}
.vod-previs__toolbar > * { pointer-events: auto; }
.vod-previs__toolbar-spacer { flex: 1; }
.vod-previs__tool-rail {
  position: absolute;
  z-index: 5;
  top: 10px;
  bottom: 10px;
  left: 10px;
  width: 66px;
  display: flex;
  flex-direction: column;
  gap: 5px;
  padding: 7px;
  overflow-y: auto;
  background: var(--cp-panel-strong);
  border: 1px solid var(--cp-border);
  border-radius: 0.625rem;
  box-shadow: 0 0 2px rgba(0,0,0,0.12), 0 1px 2px rgba(0,0,0,0.14);
}
.vod-previs__tool-rail .vod-previs__button {
  width: 100%;
  min-height: 38px;
  padding: 5px 3px;
  font-size: 10px;
}
.vod-previs__tool-separator {
  height: 1px;
  flex: 0 0 auto;
  margin: 2px 0;
  background: var(--cp-border);
}
.vod-previs__tool-status {
  position: absolute;
  z-index: 5;
  left: 84px;
  bottom: 12px;
  max-width: min(520px, calc(100% - 450px));
  padding: 6px 9px;
  color: var(--cp-text);
  background: var(--cp-panel-strong);
  border: 1px solid var(--cp-border);
  border-radius: 7px;
  font-size: 11px;
  pointer-events: none;
}
.vod-previs__right {
  min-height: 0;
  display: grid;
  grid-template-rows: auto minmax(0, 1fr);
  background: var(--cp-surface);
}
.vod-previs__monitor {
  position: absolute;
  z-index: 6;
  top: 58px;
  right: 12px;
  width: min(34%, 380px);
  aspect-ratio: 16 / 9;
  display: grid;
  grid-template-rows: auto minmax(0, 1fr);
  overflow: hidden;
  background: var(--cp-surface);
  border: 1px solid var(--cp-border-strong);
  border-radius: 0.625rem;
  box-shadow: 0 0 2px rgba(0,0,0,0.12), 0 1px 2px rgba(0,0,0,0.14);
}
.vod-previs__viewport[data-mode="scene"] .vod-previs__monitor,
.vod-previs__viewport[data-mode="camera"] .vod-previs__monitor {
  display: none;
}
.vod-previs__tabs {
  min-height: 38px;
  display: flex;
  gap: 4px;
  align-items: center;
  overflow-x: auto;
  padding: 5px 7px;
  background: var(--cp-surface-soft);
  border-bottom: 1px solid var(--cp-border);
}
.vod-previs__monitor-stage { position: relative; min-height: 0; background: var(--cp-bg); }
.vod-previs__monitor-label {
  position: absolute;
  left: 8px;
  bottom: 8px;
  z-index: 2;
  padding: 4px 7px;
  color: var(--cp-text);
  background: var(--cp-panel-strong);
  border: 1px solid var(--cp-border);
  border-radius: 7px;
  font-size: 11px;
  pointer-events: none;
}
.vod-previs__inspector-tabs {
  min-height: 44px;
  padding: 6px 8px;
  overflow-x: auto;
  background: var(--cp-surface-soft);
  border-bottom: 1px solid var(--cp-border);
}
.vod-previs__side-content {
  min-height: 0;
  overflow: auto;
  padding: 12px;
}
.vod-previs__side-grid {
  display: grid;
  grid-template-columns: minmax(130px, 0.85fr) minmax(0, 1.3fr);
  gap: 10px;
}
.vod-previs__hierarchy {
  min-width: 0;
  padding-right: 9px;
  border-right: 1px solid var(--cp-border);
}
.vod-previs__section { margin-bottom: 14px; }
.vod-previs__section-title {
  margin: 0 0 7px;
  color: var(--cp-text-muted);
  font-size: 11px;
  font-weight: 650;
  text-transform: uppercase;
  letter-spacing: 0.04em;
}
.vod-previs__list { display: grid; gap: 5px; margin-bottom: 7px; }
.vod-previs__item {
  width: 100%;
  min-height: 31px;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 5px;
  padding: 5px 7px;
  overflow: hidden;
  color: var(--cp-text);
  background: var(--cp-surface-soft);
  border: 1px solid var(--cp-border);
  border-radius: 7px;
  cursor: pointer;
  text-align: left;
  font: inherit;
  font-size: 12px;
}
.vod-previs__item > span:first-child { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.vod-previs__item[data-active="true"] {
  color: var(--cp-accent);
  background: var(--cp-accent-soft);
  border-color: var(--cp-accent);
}
.vod-previs__item-meta { color: var(--cp-text-muted); font-size: 10px; white-space: nowrap; }
.vod-previs__button {
  min-height: 30px;
  padding: 5px 9px;
  color: var(--cp-text);
  background: var(--cp-surface);
  border: 1px solid var(--cp-border);
  border-radius: 7px;
  cursor: pointer;
  font: inherit;
  font-size: 12px;
}
.vod-previs__button:hover { border-color: var(--cp-border-strong); }
.vod-previs__button:disabled { color: var(--cp-text-muted); cursor: not-allowed; }
.vod-previs__button[data-active="true"] {
  color: var(--cp-accent);
  background: var(--cp-accent-soft);
  border-color: var(--cp-accent);
}
.vod-previs__button--primary {
  color: var(--cp-accent-fg);
  background: var(--cp-accent);
  border-color: var(--cp-accent);
}
.vod-previs__button--primary:hover {
  background: var(--cp-accent-hover);
  border-color: var(--cp-accent-hover);
}
.vod-previs__button--danger { color: var(--cp-danger); }
.vod-previs__button-row { display: flex; gap: 5px; flex-wrap: wrap; margin-bottom: 7px; }
.vod-previs__field { display: grid; gap: 3px; margin-bottom: 7px; }
.vod-previs__field > span { color: var(--cp-text-muted); font-size: 11px; }
.vod-previs__input, .vod-previs__select, .vod-previs__textarea {
  width: 100%;
  min-height: 30px;
  padding: 4px 7px;
  color: var(--cp-text);
  background: var(--cp-bg-elevated);
  border: 1px solid var(--cp-border);
  border-radius: 7px;
  font: 11px/1.4 Consolas, "Courier New", Courier, monospace;
}
.vod-previs__textarea { min-height: 58px; resize: vertical; }
.vod-previs__vec { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 4px; }
.vod-previs__hint {
  padding: 7px 8px;
  color: var(--cp-text-muted);
  background: var(--cp-surface-soft);
  border: 1px solid var(--cp-border);
  border-radius: 7px;
  font-size: 11px;
  line-height: 1.4;
}
.vod-previs__scene-source {
  border-bottom: 1px solid var(--cp-border);
  padding: 10px 12px;
}
.vod-previs__scene-source summary {
  color: var(--cp-text);
  cursor: pointer;
  font-size: 12px;
  font-weight: 650;
  margin-bottom: 8px;
}
.vod-previs__upload-grid {
  display: grid;
  gap: 5px;
  grid-template-columns: repeat(3, minmax(0, 1fr));
}
.vod-previs__upload {
  border: 1px dashed var(--cp-border-strong);
  border-radius: 0.625rem;
  color: var(--cp-text-muted);
  cursor: pointer;
  font-size: 10px;
  min-height: 52px;
  padding: 6px;
  text-align: center;
}
.vod-previs__upload--asset {
  display: block;
  margin-bottom: 7px;
}
.vod-previs__upload input { display: none; }
.vod-previs__status {
  border-left: 3px solid var(--cp-accent);
  color: var(--cp-text-muted);
  font-size: 11px;
  margin: 7px 0;
  padding: 5px 8px;
}
.vod-previs__status[data-state="error"] {
  border-left-color: var(--cp-danger);
  color: var(--cp-danger);
}
.vod-previs__status[data-state="complete"] {
  border-left-color: var(--cp-success);
  color: var(--cp-success);
}
.vod-previs__confirm {
  align-items: flex-start;
  color: var(--cp-text-muted);
  display: flex;
  font-size: 10px;
  gap: 6px;
  line-height: 1.35;
  margin: 7px 0;
}
.vod-previs__confirm input { margin-top: 2px; }
.vod-previs__asset-notice {
  position: absolute;
  z-index: 4;
  left: 50%;
  top: 56px;
  max-width: 480px;
  transform: translateX(-50%);
}
.vod-previs__timeline {
  min-height: 208px;
  max-height: 32vh;
  display: grid;
  grid-template-columns: 190px minmax(0, 1fr);
  grid-template-rows: 42px minmax(0, 1fr);
  overflow: hidden;
  background: var(--cp-surface);
  border-top: 1px solid var(--cp-border);
  color: var(--cp-text-muted);
  font: 11px/1.3 "Segoe UI", Aptos, Calibri, sans-serif;
}
.vod-previs__transport {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 4px 8px;
  border-right: 1px solid var(--cp-border);
  border-bottom: 1px solid var(--cp-border);
}
.vod-previs__ruler {
  position: relative;
  border-bottom: 1px solid var(--cp-border);
  cursor: pointer;
  background: var(--cp-surface-soft);
}
.vod-previs__timeline-actions {
  position: absolute;
  z-index: 3;
  left: 7px;
  top: 5px;
  display: flex;
  gap: 4px;
}
.vod-previs__timeline-actions .vod-previs__button {
  min-height: 28px;
  padding: 4px 7px;
}
.vod-previs__ruler-ticks {
  position: absolute;
  inset: 0;
  overflow: hidden;
  pointer-events: none;
}
.vod-previs__ruler-tick {
  position: absolute;
  bottom: 2px;
  transform: translateX(-50%);
  color: var(--cp-text-muted);
  font-size: 9px;
}
.vod-previs__ruler-tick::before {
  content: "";
  position: absolute;
  left: 50%;
  bottom: 12px;
  height: 8px;
  border-left: 1px solid var(--cp-border-strong);
}
.vod-previs__time-readout {
  position: absolute;
  right: 8px;
  top: 11px;
  pointer-events: none;
}
.vod-previs__track-labels, .vod-previs__tracks {
  min-height: 0;
  overflow: auto;
}
.vod-previs__track-labels { border-right: 1px solid var(--cp-border); }
.vod-previs__track-label, .vod-previs__track {
  height: 29px;
  display: flex;
  align-items: center;
  padding: 0 8px;
  border-bottom: 1px solid var(--cp-border);
}
.vod-previs__track { position: relative; padding: 0; cursor: pointer; }
.vod-previs__track-label--group, .vod-previs__track--group {
  height: 31px;
  color: var(--cp-text);
  background: var(--cp-bg-elevated);
  font-weight: 650;
}
.vod-previs__track-label--child { padding-left: 24px; }
.vod-previs__track-line {
  position: absolute;
  left: 0;
  right: 0;
  top: 50%;
  border-top: 1px solid var(--cp-border-strong);
}
.vod-previs__marker {
  position: absolute;
  top: 50%;
  width: 10px;
  height: 10px;
  transform: translate(-50%, -50%) rotate(45deg);
  padding: 0;
  background: var(--cp-link);
  border: 1px solid var(--cp-surface);
  cursor: pointer;
}
.vod-previs__marker--cut { background: var(--cp-warning); }
.vod-previs__marker[data-active="true"] { background: var(--cp-accent); }
.vod-previs__clip {
  position: absolute;
  top: 4px;
  bottom: 4px;
  min-width: 20px;
  overflow: hidden;
  padding: 3px 7px;
  color: var(--cp-text);
  background: var(--cp-highlight);
  border: 1px solid var(--cp-accent);
  border-radius: 5px;
  cursor: pointer;
  font: 10px/1.3 "Segoe UI", Aptos, Calibri, sans-serif;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.vod-previs__clip--camera {
  background: var(--cp-accent-soft);
  border-color: var(--cp-link);
}
.vod-previs__clip--cut {
  background: var(--cp-surface-soft);
  border-color: var(--cp-warning);
}
.vod-previs__playhead {
  position: absolute;
  z-index: 5;
  top: 0;
  bottom: 0;
  width: 2px;
  transform: translateX(-1px);
  background: var(--cp-accent);
  pointer-events: none;
}
.vod-previs__playhead::before {
  content: "";
  position: absolute;
  top: 0;
  left: -4px;
  width: 10px;
  height: 7px;
  background: var(--cp-accent);
}
@media (max-width: 1050px) {
  .vod-previs__body { grid-template-columns: minmax(440px, 1fr) 330px; }
  .vod-previs__side-grid { grid-template-columns: 1fr; }
  .vod-previs__hierarchy { border-right: 0; border-bottom: 1px solid var(--cp-border); padding: 0 0 8px; }
  .vod-previs__monitor { width: min(42%, 320px); }
  .vod-previs__timeline { grid-template-columns: 160px minmax(0, 1fr); }
}
`;
  document.head.appendChild(style);
}

function widget(node, name) {
  return (node.widgets || []).find((item) => item.name === name);
}

function safeJson(value, fallback) {
  try {
    const parsed = JSON.parse(value || "");
    return parsed && typeof parsed === "object" ? parsed : structuredClone(fallback);
  } catch {
    return structuredClone(fallback);
  }
}

function finite(value, fallback = 0) {
  if (value === null || value === undefined || value === "") return fallback;
  const number = Number(value);
  return Number.isFinite(number) ? number : fallback;
}

function clamp(value, min, max) {
  return Math.min(max, Math.max(min, value));
}

function vec3(value, fallback = [0, 0, 0]) {
  return [0, 1, 2].map((index) => finite(value?.[index], fallback[index]));
}

function normalizeBackgroundTransform(value) {
  const source = value && typeof value === "object" ? value : DEFAULT_BACKGROUND_TRANSFORM;
  return {
    position: vec3(source.position),
    rotation: vec3(source.rotation),
    scale: clamp(finite(source.scale, 1), 0.001, 1000),
  };
}

function invalidateRenderCache(state) {
  state.renderCachePath = "";
}

function normalizePoint(point, fallback, time) {
  const source = point && typeof point === "object" ? point : {};
  const normalized = {
    ...source,
    time: clamp(finite(source.time, time), 0, 1),
    position: vec3(source.position, fallback),
  };
  if (Array.isArray(source.in_handle)) normalized.in_handle = vec3(source.in_handle, normalized.position);
  if (Array.isArray(source.out_handle)) normalized.out_handle = vec3(source.out_handle, normalized.position);
  return normalized;
}

function normalizeSpeedCurve(value) {
  const source = Array.isArray(value) ? value : DEFAULT_TRACK.speed_curve;
  const byX = new Map();
  for (const point of source) {
    if (!point || typeof point !== "object") continue;
    const x = clamp(finite(point.x), 0, 1);
    const key = x.toFixed(6);
    byX.set(key, { x: Number(key), y: clamp(finite(point.y), 0, 1) });
  }
  byX.set("0.000000", { x: 0, y: 0 });
  byX.set("1.000000", { x: 1, y: 1 });
  const points = [...byX.values()].sort((a, b) => a.x - b.x);
  let previousY = 0;
  return points.map((point, index) => {
    const y = index === 0
      ? 0
      : index === points.length - 1
        ? 1
        : Math.max(previousY, point.y);
    previousY = y;
    return { x: point.x, y };
  });
}

function normalizeTrack(track, fallbackPoints) {
  const source = track && typeof track === "object" ? track : {};
  const rawPoints = Array.isArray(source.points) && source.points.length
    ? source.points
    : fallbackPoints;
  let points = rawPoints.map((point, index) =>
    normalizePoint(point, vec3(fallbackPoints[Math.min(index, fallbackPoints.length - 1)]?.position), index / Math.max(rawPoints.length - 1, 1)));
  points = [...new Map(points.map((point) => [point.time.toFixed(6), point])).values()]
    .sort((a, b) => a.time - b.time);
  if (points.length === 1) {
    points.push({ time: points[0].time < 1 ? 1 : 0, position: [...points[0].position] });
    points.sort((a, b) => a.time - b.time);
  }
  return {
    ...source,
    interpolation: TRACK_INTERPOLATIONS.includes(source.interpolation)
      ? source.interpolation
      : DEFAULT_TRACK.interpolation,
    speed_mode: SPEED_MODES.includes(source.speed_mode) ? source.speed_mode : DEFAULT_TRACK.speed_mode,
    speed_description: String(source.speed_description || ""),
    speed_curve: normalizeSpeedCurve(source.speed_curve),
    points,
  };
}

function normalizeScene(rawValue) {
  const raw = rawValue && typeof rawValue === "object" && !Array.isArray(rawValue)
    ? rawValue
    : structuredClone(DEFAULT_SCENE);
  const objects = Array.isArray(raw.objects) ? raw.objects : [];
  return {
    ...raw,
    version: 3,
    objects: objects.map((item, index) => {
      const source = item && typeof item === "object" ? item : {};
      const position = vec3(source.position);
      const end = vec3(source.end, position);
      const legacyPath = Array.isArray(source.path) && source.path.length
        ? source.path
        : [{ time: 0, position }, { time: 1, position: end }];
      const motionTrack = normalizeTrack(source.motion_track, legacyPath);
      const normalized = {
        ...source,
        id: String(source.id || `object-${index + 1}`),
        name: String(source.name || `对象 ${index + 1}`),
        type: ["actor", "box", "sphere"].includes(source.type) ? source.type : "box",
        scale: vec3(source.scale, [1, 1, 1]).map((value) => Math.max(0.05, value)),
        rotation: vec3(source.rotation),
        motion: String(source.motion || "static"),
        motion_track: motionTrack,
      };
      syncObjectLegacy(normalized);
      return normalized;
    }),
  };
}

function normalizeKeyframe(frame, fallback = DEFAULT_KEYFRAMES[0]) {
  const source = frame && typeof frame === "object" ? frame : {};
  return {
    ...source,
    time: clamp(finite(source.time, fallback.time), 0, 1),
    position: vec3(source.position, fallback.position),
    target: vec3(source.target, fallback.target),
    fov: clamp(finite(source.fov, fallback.fov), 15, 100),
    roll: clamp(finite(source.roll, fallback.roll || 0), -180, 180),
  };
}

function scalarTrack(source, frames) {
  const raw = source && typeof source === "object" ? source : {};
  const points = Array.isArray(raw.points) && raw.points.length
    ? raw.points
    : frames.map((frame) => ({ time: frame.time, value: frame.fov }));
  return {
    interpolation: "linear",
    points: points.map((point, index) => ({
      time: clamp(finite(point?.time, index / Math.max(points.length - 1, 1)), 0, 1),
      value: clamp(finite(point?.value, 48), 15, 100),
    })).sort((a, b) => a.time - b.time),
  };
}

function normalizeCamera(camera, index, usedIds) {
  const source = camera && typeof camera === "object" ? camera : {};
  let id = String(source.id || `camera-${index + 1}`);
  if (usedIds.has(id)) {
    let suffix = 2;
    while (usedIds.has(`${id}-${suffix}`)) suffix += 1;
    id = `${id}-${suffix}`;
  }
  usedIds.add(id);
  const frames = (Array.isArray(source.keyframes) && source.keyframes.length
    ? source.keyframes
    : DEFAULT_KEYFRAMES)
    .map((frame, frameIndex) =>
      normalizeKeyframe(frame, DEFAULT_KEYFRAMES[Math.min(frameIndex, DEFAULT_KEYFRAMES.length - 1)]))
    .sort((a, b) => a.time - b.time);
  const positionTrack = normalizeTrack(
    source.position_track,
    frames.map((frame) => ({ time: frame.time, position: frame.position })),
  );
  const targetTrack = normalizeTrack(
    source.target_track,
    frames.map((frame) => ({ time: frame.time, position: frame.target })),
  );
  const normalized = {
    ...source,
    id,
    name: String(source.name || `Camera ${index + 1}`),
    position_track: positionTrack,
    target_track: targetTrack,
    fov_track: scalarTrack(source.fov_track, frames),
    keyframes: frames,
  };
  if (source.position_track || source.target_track || source.fov_track) {
    syncCameraLegacy(normalized);
  } else {
    syncCameraTracks(normalized);
  }
  return normalized;
}

function normalizeCuts(cuts, cameras, fallbackId) {
  const validIds = new Set(cameras.map((camera) => camera.id));
  const byTime = new Map();
  for (const source of cuts) {
    if (!source || typeof source !== "object") continue;
    const time = clamp(finite(source.time), 0, 1);
    byTime.set(time.toFixed(6), {
      ...source,
      time,
      camera_id: validIds.has(String(source.camera_id)) ? String(source.camera_id) : fallbackId,
    });
  }
  if (!byTime.has("0.000000")) byTime.set("0.000000", { time: 0, camera_id: fallbackId });
  const normalized = [...byTime.values()].sort((a, b) => a.time - b.time);
  normalized[0].time = 0;
  return normalized;
}

function normalizeCameraRig(rawValue) {
  const raw = rawValue && typeof rawValue === "object" ? rawValue : {};
  const sourceCameras = Array.isArray(raw.cameras)
    ? raw.cameras
    : [{ id: raw.id, name: raw.name, keyframes: raw.keyframes }];
  const usedIds = new Set();
  const cameras = sourceCameras.map((camera, index) => normalizeCamera(camera, index, usedIds));
  if (!cameras.length) cameras.push(normalizeCamera(DEFAULT_CAMERA.cameras[0], 0, usedIds));
  const validIds = new Set(cameras.map((camera) => camera.id));
  const requested = String(raw.active_camera || cameras[0].id);
  const activeCamera = validIds.has(requested) ? requested : cameras[0].id;
  const result = { ...raw };
  delete result.id;
  delete result.name;
  delete result.keyframes;
  return {
    ...result,
    version: 3,
    active_camera: activeCamera,
    cameras,
    cuts: normalizeCuts(Array.isArray(raw.cuts) ? raw.cuts : [], cameras, activeCamera),
  };
}

function syncObjectLegacy(item) {
  item.motion_track.points.sort((a, b) => a.time - b.time);
  item.path = item.motion_track.points.map((point) => ({
    time: point.time,
    position: [...point.position],
    ...(point.in_handle ? { in_handle: [...point.in_handle] } : {}),
    ...(point.out_handle ? { out_handle: [...point.out_handle] } : {}),
  }));
  item.position = [...item.path[0].position];
  item.end = [...item.path.at(-1).position];
}

function syncCameraTracks(camera) {
  camera.keyframes.sort((a, b) => a.time - b.time);
  const positionSettings = camera.position_track || DEFAULT_TRACK;
  const targetSettings = camera.target_track || DEFAULT_TRACK;
  const positionPoints = camera.keyframes.map((frame) => {
    const existing = positionSettings.points?.find((point) => Math.abs(point.time - frame.time) < EPSILON);
    return {
      time: frame.time,
      position: frame.position,
      ...(existing?.in_handle ? { in_handle: existing.in_handle } : {}),
      ...(existing?.out_handle ? { out_handle: existing.out_handle } : {}),
    };
  });
  const targetPoints = camera.keyframes.map((frame) => {
    const existing = targetSettings.points?.find((point) => Math.abs(point.time - frame.time) < EPSILON);
    return {
      time: frame.time,
      position: frame.target,
      ...(existing?.in_handle ? { in_handle: existing.in_handle } : {}),
      ...(existing?.out_handle ? { out_handle: existing.out_handle } : {}),
    };
  });
  camera.position_track = normalizeTrack(
    { ...positionSettings, points: positionPoints },
    [],
  );
  camera.target_track = normalizeTrack(
    { ...targetSettings, points: targetPoints },
    [],
  );
  camera.fov_track = scalarTrack(
    { points: camera.keyframes.map((frame) => ({ time: frame.time, value: frame.fov })) },
    camera.keyframes,
  );
}

function syncCameraLegacy(camera) {
  const previousFrames = Array.isArray(camera.keyframes) ? camera.keyframes : [];
  const times = new Set([
    ...camera.position_track.points.map((point) => point.time.toFixed(6)),
    ...camera.target_track.points.map((point) => point.time.toFixed(6)),
    ...camera.fov_track.points.map((point) => point.time.toFixed(6)),
  ]);
  camera.keyframes = [...times].map(Number).sort((a, b) => a - b).map((time) => {
    const positionPoint = camera.position_track.points.find((point) => Math.abs(point.time - time) < EPSILON);
    const targetPoint = camera.target_track.points.find((point) => Math.abs(point.time - time) < EPSILON);
    return {
      time,
      position: positionPoint ? [...positionPoint.position] : evaluateTrack(camera.position_track, time),
      target: targetPoint ? [...targetPoint.position] : evaluateTrack(camera.target_track, time),
      fov: evaluateScalarTrack(camera.fov_track, time),
      roll: clamp(evaluateKeyframeScalar(previousFrames, time, "roll", 0), -180, 180),
    };
  });
}

function speedRemap(mode, amount, curve) {
  const t = clamp(amount, 0, 1);
  if (mode === "ease_in") return t * t;
  if (mode === "ease_out") return 1 - (1 - t) ** 2;
  if (mode === "ease_in_out") return t < 0.5 ? 2 * t * t : 1 - ((-2 * t + 2) ** 2) / 2;
  if (mode !== "custom") return t;
  const points = normalizeSpeedCurve(curve);
  for (let index = 0; index < points.length - 1; index += 1) {
    const a = points[index];
    const b = points[index + 1];
    if (t <= b.x + EPSILON) {
      const span = Math.max(EPSILON, b.x - a.x);
      return a.y + (b.y - a.y) * ((t - a.x) / span);
    }
  }
  return points.at(-1).y;
}

function rawTrackAt(track, amount) {
  const points = track.points;
  if (!points.length) return [0, 0, 0];
  const firstTime = points[0].time;
  const lastTime = points.at(-1).time;
  const time = firstTime + clamp(amount, 0, 1) * Math.max(EPSILON, lastTime - firstTime);
  if (time <= firstTime) return [...points[0].position];
  if (time >= lastTime) return [...points.at(-1).position];
  let index = 0;
  while (index < points.length - 2 && time > points[index + 1].time) index += 1;
  const a = points[index];
  const b = points[index + 1];
  const local = clamp((time - a.time) / Math.max(EPSILON, b.time - a.time), 0, 1);
  if (track.interpolation === "bezier") {
    return cubicBezier(
      a.position,
      a.out_handle || lerp3(a.position, b.position, 1 / 3),
      b.in_handle || lerp3(a.position, b.position, 2 / 3),
      b.position,
      local,
    );
  }
  if (track.interpolation === "catmull_rom") {
    return catmullRom(
      points[Math.max(0, index - 1)].position,
      a.position,
      b.position,
      points[Math.min(points.length - 1, index + 2)].position,
      local,
    );
  }
  return lerp3(a.position, b.position, local);
}

function arcLengthParameter(track, amount) {
  const samples = 180;
  const signature = JSON.stringify([
    track.interpolation,
    track.points.map((point) => [
      point.time,
      point.position,
      point.in_handle || null,
      point.out_handle || null,
    ]),
  ]);
  let lut = ARC_LENGTH_LUT_CACHE.get(track);
  if (!lut || lut.signature !== signature) {
    const cumulative = [0];
    let total = 0;
    let previous = rawTrackAt(track, 0);
    for (let index = 1; index <= samples; index += 1) {
      const current = rawTrackAt(track, index / samples);
      total += distance3(previous, current);
      cumulative.push(total);
      previous = current;
    }
    const fractions = total < EPSILON
      ? cumulative.map((_, index) => index / samples)
      : cumulative.map((length) => length / total);
    lut = { signature, fractions };
    ARC_LENGTH_LUT_CACHE.set(track, lut);
  }
  const target = clamp(amount, 0, 1);
  let low = 0;
  let high = lut.fractions.length - 1;
  while (low + 1 < high) {
    const middle = Math.floor((low + high) / 2);
    if (lut.fractions[middle] < target) low = middle;
    else high = middle;
  }
  if (high < 1) return 0;
  const local = (target - lut.fractions[low])
    / Math.max(EPSILON, lut.fractions[high] - lut.fractions[low]);
  return (low + local) / samples;
}

function evaluateTrack(track, time) {
  const points = track.points;
  if (!points?.length) return [0, 0, 0];
  const first = points[0].time;
  const last = points.at(-1).time;
  const normalized = clamp((time - first) / Math.max(EPSILON, last - first), 0, 1);
  if (track.speed_mode === "keyframed") return rawTrackAt(track, normalized);
  const distanceProgress = speedRemap(track.speed_mode, normalized, track.speed_curve);
  return rawTrackAt(track, arcLengthParameter(track, distanceProgress));
}

function trackTangent(track, time) {
  const delta = 0.002;
  return normalize3(sub3(evaluateTrack(track, clamp(time + delta, 0, 1)), evaluateTrack(track, clamp(time - delta, 0, 1))));
}

function evaluateScalarTrack(track, time) {
  const points = track?.points || [];
  if (!points.length) return 48;
  if (time <= points[0].time) return points[0].value;
  if (time >= points.at(-1).time) return points.at(-1).value;
  for (let index = 0; index < points.length - 1; index += 1) {
    const a = points[index];
    const b = points[index + 1];
    if (time <= b.time) {
      const amount = (time - a.time) / Math.max(EPSILON, b.time - a.time);
      return a.value + (b.value - a.value) * amount;
    }
  }
  return points.at(-1).value;
}

function evaluateKeyframeScalar(frames, time, key, fallback) {
  const sorted = [...frames].sort((a, b) => finite(a.time) - finite(b.time));
  if (!sorted.length) return fallback;
  if (time <= finite(sorted[0].time)) return finite(sorted[0][key], fallback);
  if (time >= finite(sorted.at(-1).time)) return finite(sorted.at(-1)[key], fallback);
  for (let index = 0; index < sorted.length - 1; index += 1) {
    const a = sorted[index];
    const b = sorted[index + 1];
    if (time <= finite(b.time)) {
      const amount = (time - finite(a.time)) / Math.max(EPSILON, finite(b.time) - finite(a.time));
      return finite(a[key], fallback) + (finite(b[key], fallback) - finite(a[key], fallback)) * amount;
    }
  }
  return fallback;
}

function cameraAt(camera, time, scene = null) {
  const frame = {
    time,
    position: evaluateTrack(camera.position_track, time),
    target: evaluateTrack(camera.target_track, time),
    fov: evaluateScalarTrack(camera.fov_track, time),
    roll: clamp(evaluateKeyframeScalar(camera.keyframes, time, "roll", 0), -180, 180),
  };
  const targetObject = scene?.objects?.find((item) => item.id === camera.look_at_object_id);
  if (targetObject) {
    frame.target = evaluateTrack(targetObject.motion_track, time);
    if (targetObject.type === "actor") frame.target[1] += 1.2;
  }
  return frame;
}

function renderCameraAt(rig, time, scene = null) {
  let cut = rig.cuts[0];
  for (const candidate of rig.cuts) {
    if (candidate.time <= time + EPSILON) cut = candidate;
    else break;
  }
  const camera = rig.cameras.find((item) => item.id === cut?.camera_id)
    || rig.cameras.find((item) => item.id === rig.active_camera)
    || rig.cameras[0];
  return camera ? { camera, frame: cameraAt(camera, time, scene), cut } : null;
}

function openEditor(node) {
  ensureTheme();
  const sceneWidget = widget(node, "scene_json");
  const cameraWidget = widget(node, "camera_json");
  const assetWidget = widget(node, "background_asset_path");
  const sceneSourceWidget = widget(node, "scene_source");
  const transformWidget = widget(node, "background_transform");
  const taskIdWidget = widget(node, "generated_task_id");
  const renderCacheWidget = widget(node, "render_cache_path");
  const widthWidget = widget(node, "width");
  const heightWidget = widget(node, "height");
  const scene = normalizeScene(safeJson(sceneWidget?.value, DEFAULT_SCENE));
  const cameraRig = normalizeCameraRig(safeJson(cameraWidget?.value, DEFAULT_CAMERA));
  const fps = clamp(finite(widget(node, "fps")?.value, 24), 1, 120);
  const frameCount = clamp(Math.round(finite(widget(node, "frame_count")?.value, 48)), 2, 120);
  const duration = frameCount / fps;
  const state = {
    scene,
    cameraRig,
    fps,
    frameCount,
    duration,
    time: 0,
    playing: false,
    selectedKind: scene.objects.length ? "object" : "camera",
    selectedObjectId: scene.objects[0]?.id || null,
    selectedPointIndex: 0,
    selectedCameraId: cameraRig.active_camera,
    selectedKeyframe: 0,
    observationCameraId: cameraRig.active_camera,
    followCut: true,
    workspaceMode: "director",
    sideTab: assetWidget?.value ? "selection" : "scene",
    pathTool: null,
    toolMessage: "选择人物或摄影机后，可手绘或逐点创建运动轨迹",
    transformMode: "translate",
    showGrid: true,
    disposed: false,
    exporting: false,
    background: {
      source: String(sceneSourceWidget?.value || (assetWidget?.value ? "Local Asset" : "Blank")),
      path: String(assetWidget?.value || ""),
      taskId: String(taskIdWidget?.value || ""),
      transform: normalizeBackgroundTransform(
        safeJson(transformWidget?.value, DEFAULT_BACKGROUND_TRANSFORM)),
    },
    renderCachePath: String(renderCacheWidget?.value || ""),
    generation: {
      status: "idle",
      message: "",
      prompt: "",
      storageMode: "Temporary",
      confirmed: false,
      images: [null, null, null],
    },
    refreshInspector: null,
    refreshTimeline: null,
    refreshTabs: null,
    refreshTools: null,
    refreshInspectorTabs: null,
    rebuildScene: null,
    loadBackground: null,
  };

  const overlay = element("div", "vod-previs");
  const shell = element("div", "vod-previs__window");
  const header = element("header", "vod-previs__header");
  const heading = element("div");
  heading.append(
    textElement("div", "vod-previs__title", "3D 白模预演台 · V3 WebGL"),
    textElement("div", "vod-previs__subtitle", assetWidget?.value
      ? `背景资产：${String(assetWidget.value).split(/[\\/]/).pop()}`
      : "Three.js 本地渲染 · 轨迹、摄影机关键帧与剪辑"),
  );

  const body = element("div", "vod-previs__body");
  const viewport = element("main", "vod-previs__viewport");
  viewport.dataset.mode = state.workspaceMode;
  const mainHost = element("div", "vod-previs__canvas-host");
  const toolbar = element("div", "vod-previs__toolbar");
  const toolRail = element("div", "vod-previs__tool-rail");
  const toolStatus = textElement("div", "vod-previs__tool-status", state.toolMessage);

  const right = element("aside", "vod-previs__right");
  const monitor = element("section", "vod-previs__monitor");
  const tabs = element("div", "vod-previs__tabs");
  const monitorStage = element("div", "vod-previs__monitor-stage");
  const monitorHost = element("div", "vod-previs__monitor-host");
  const monitorLabel = textElement("div", "vod-previs__monitor-label", "OBSERVATION");
  monitorStage.append(monitorHost, monitorLabel);
  monitor.append(tabs, monitorStage);
  viewport.append(mainHost, toolbar, toolRail, monitor, toolStatus);
  const inspectorTabs = element("div", "vod-previs__inspector-tabs");
  const sideContent = element("div", "vod-previs__side-content");
  right.append(inspectorTabs, sideContent);
  body.append(viewport, right);

  const timeline = buildTimeline(state);
  const footer = element("footer", "vod-previs__footer");
  const modeTabs = element("div", "vod-previs__mode-tabs");
  const headerActions = element("div", "vod-previs__header-actions");
  let runtime = null;
  let animationFrame = 0;
  let previousTime = performance.now();
  let closed = false;
  const close = () => {
    if (closed) return;
    closed = true;
    state.disposed = true;
    cancelAnimationFrame(animationFrame);
    document.removeEventListener("keydown", onKeyDown);
    runtime?.dispose();
    overlay.remove();
  };
  const persistNodeState = () => {
    for (const item of state.scene.objects) syncObjectLegacy(item);
    const objectIds = new Set(state.scene.objects.map((item) => item.id));
    for (const camera of state.cameraRig.cameras) {
      if (camera.look_at_object_id && !objectIds.has(camera.look_at_object_id)) {
        camera.look_at_object_id = "";
      }
      syncCameraLegacy(camera);
    }
    state.cameraRig.cuts = normalizeCuts(
      state.cameraRig.cuts,
      state.cameraRig.cameras,
      state.cameraRig.active_camera,
    );
    setWidgetValue(node, sceneWidget, JSON.stringify(state.scene, null, 2));
    setWidgetValue(node, cameraWidget, JSON.stringify(state.cameraRig, null, 2));
    setWidgetValue(node, assetWidget, state.background.path);
    setWidgetValue(node, sceneSourceWidget, state.background.source);
    setWidgetValue(node, transformWidget, JSON.stringify(state.background.transform));
    setWidgetValue(node, taskIdWidget, state.background.taskId);
    setWidgetValue(node, renderCacheWidget, state.renderCachePath);
  };
  const onKeyDown = (event) => {
    if (event.key === "Escape") {
      if (runtime?.cancelPathDrawing?.()) {
        state.pathTool = null;
        state.toolMessage = "已取消轨迹绘制";
        state.refreshTools?.();
        return;
      }
      close();
    }
    if (event.key === "Enter" && runtime?.finishPathDrawing?.()) {
      state.pathTool = null;
      state.toolMessage = "轨迹已生成，可拖动控制点继续调整";
      state.refreshTools?.();
    }
    if (event.key === " " && !isTypingTarget(event.target)) {
      event.preventDefault();
      state.playing = !state.playing;
      state.refreshTimeline?.();
    }
  };
  document.addEventListener("keydown", onKeyDown);
  for (const [label, mode] of [
    ["场景", "scene"],
    ["导演视角", "director"],
    ["机位视角", "camera"],
  ]) {
    const control = button(label, () => {
      state.workspaceMode = mode;
      viewport.dataset.mode = mode;
      state.refreshTools?.();
    });
    control.dataset.workspaceMode = mode;
    modeTabs.appendChild(control);
  }
  header.append(heading, modeTabs, headerActions);
  const exportStatus = textElement("span", "vod-previs__subtitle", "");
  const exportButton = button("渲染镜头输出", async () => {
    if (state.exporting) return;
    state.exporting = true;
    exportButton.disabled = true;
    exportStatus.textContent = "准备逐帧渲染…";
    try {
      persistNodeState();
      state.renderCachePath = await exportPrevisFrames(
        runtime,
        state,
        Math.round(finite(widthWidget?.value, 768)),
        Math.round(finite(heightWidget?.value, 432)),
        (message) => { exportStatus.textContent = message; },
      );
      setWidgetValue(node, renderCacheWidget, state.renderCachePath);
      exportStatus.textContent = "镜头输出已缓存，执行节点即可得到 IMAGE / VIDEO";
    } catch (error) {
      exportStatus.textContent = `渲染失败：${error.message}`;
    } finally {
      state.exporting = false;
      exportButton.disabled = false;
    }
  }, true);
  headerActions.append(exportButton, button("关闭", close));
  footer.append(
    exportStatus,
    element("span", "vod-previs__toolbar-spacer"),
    button("取消", close),
    button("保存到节点", () => {
      persistNodeState();
      close();
    }, true),
  );
  shell.append(header, body, timeline.root, footer);
  overlay.appendChild(shell);
  document.body.appendChild(overlay);

  runtime = createThreeRuntime(mainHost, monitorHost, state, () => {
    state.refreshInspector?.();
    state.refreshTimeline?.();
  });

  const viewButtons = [];
  for (const [label, view] of [["透视", "perspective"], ["顶", "top"], ["前", "front"], ["侧", "side"]]) {
    const control = button(label, () => {
      runtime.setView(view);
      viewButtons.forEach((item) => { item.dataset.active = String(item === control); });
    });
    control.dataset.active = String(view === "perspective");
    viewButtons.push(control);
    toolbar.appendChild(control);
  }
  const modeButtons = [];
  for (const [label, mode] of [["移动", "translate"], ["旋转", "rotate"], ["缩放", "scale"]]) {
    const control = button(label, () => {
      state.transformMode = mode;
      runtime.setTransformMode(mode);
      modeButtons.forEach((item) => { item.dataset.active = String(item === control); });
    });
    control.dataset.active = String(mode === state.transformMode);
    modeButtons.push(control);
    toolbar.appendChild(control);
  }
  const gridButton = button("网格", () => {
    state.showGrid = !state.showGrid;
    gridButton.dataset.active = String(state.showGrid);
    runtime.setGridVisible(state.showGrid);
  });
  gridButton.dataset.active = "true";
  toolbar.append(gridButton, button("适配", () => runtime.fitView()), element("span", "vod-previs__toolbar-spacer"));

  state.refreshTabs = () => renderCameraTabs(tabs, state, runtime, monitorLabel);
  state.refreshInspector = () => renderSidePanel(sideContent, state, runtime);
  state.refreshTimeline = () => timeline.render();
  state.refreshInspectorTabs = () => renderInspectorTabs(
    inspectorTabs,
    state,
    state.refreshInspector,
  );
  state.refreshTools = () => {
    viewport.dataset.mode = state.workspaceMode;
    toolStatus.textContent = state.toolMessage;
    for (const control of modeTabs.children) {
      control.dataset.active = String(control.dataset.workspaceMode === state.workspaceMode);
    }
    renderDirectorTools(toolRail, state, runtime);
  };
  state.rebuildScene = () => runtime.rebuild();
  state.loadBackground = (path) => loadBackgroundAsset(path, runtime, viewport, state);
  state.refreshTabs();
  state.refreshInspectorTabs();
  state.refreshInspector();
  state.refreshTimeline();
  state.refreshTools();
  runtime.fitView();
  state.loadBackground(state.background.path).catch((error) => {
    state.generation.status = "error";
    state.generation.message = error.message;
    state.refreshInspector?.();
  });

  const animate = (now) => {
    if (state.disposed) return;
    const elapsed = Math.min(0.1, (now - previousTime) / 1000);
    previousTime = now;
    if (state.playing) {
      state.time += elapsed / state.duration;
      if (state.time >= 1) state.time %= 1;
      state.refreshTimeline?.();
    }
    if (!state.exporting) runtime.render(state.time);
    animationFrame = requestAnimationFrame(animate);
  };
  animationFrame = requestAnimationFrame(animate);
}

function createThreeRuntime(mainHost, monitorHost, state, onManipulated) {
  const css = getComputedStyle(document.documentElement);
  const color = (name) => new THREE.Color(css.getPropertyValue(name).trim());
  const mainScene = new THREE.Scene();
  mainScene.background = color("--cp-bg");
  const monitorScene = new THREE.Scene();
  monitorScene.background = color("--cp-bg");
  const mainCamera = new THREE.PerspectiveCamera(50, 1, 0.05, 1000);
  mainCamera.position.set(8, 6, 10);
  const observationCamera = new THREE.PerspectiveCamera(48, 1, 0.05, 1000);
  const mainRenderer = new THREE.WebGLRenderer({ antialias: false, alpha: false });
  const monitorRenderer = new THREE.WebGLRenderer({
    antialias: false,
    alpha: false,
    preserveDrawingBuffer: true,
  });
  mainRenderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2));
  monitorRenderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2));
  mainRenderer.outputColorSpace = THREE.SRGBColorSpace;
  monitorRenderer.outputColorSpace = THREE.SRGBColorSpace;
  mainRenderer.shadowMap.enabled = true;
  mainHost.appendChild(mainRenderer.domElement);
  monitorHost.appendChild(monitorRenderer.domElement);

  const orbit = new OrbitControls(mainCamera, mainRenderer.domElement);
  orbit.enableDamping = true;
  orbit.target.set(0, 1, 0);
  const transform = new TransformControls(mainCamera, mainRenderer.domElement);
  const transformHelper = transform.getHelper();
  mainScene.add(transformHelper);
  let transformBinding = null;
  let disposed = false;
  transform.setMode(state.transformMode);
  transform.addEventListener("dragging-changed", (event) => {
    orbit.enabled = !event.value;
    if (!event.value) {
      commitTransform();
      onManipulated();
    }
  });
  transform.addEventListener("objectChange", () => {
    if (!transformBinding) return;
    if (transformBinding.kind === "point") {
      transformBinding.point.position = transformBinding.object.position.toArray();
      syncObjectLegacy(transformBinding.item);
    } else if (transformBinding.kind === "camera-position") {
      transformBinding.frame.position = transformBinding.object.position.toArray();
      syncCameraTracks(transformBinding.camera);
    } else if (transformBinding.kind === "camera-target") {
      transformBinding.frame.target = transformBinding.object.position.toArray();
      syncCameraTracks(transformBinding.camera);
    }
  });

  const addLights = (scene) => {
    const ambient = new THREE.HemisphereLight(
      color("--cp-surface"), color("--cp-border-strong"), 2.1);
    const key = new THREE.DirectionalLight(color("--cp-surface"), 2.5);
    key.position.set(5, 9, 4);
    key.castShadow = true;
    scene.add(ambient, key);
  };
  addLights(mainScene);
  addLights(monitorScene);
  const mainSpark = new SparkRenderer({ renderer: mainRenderer });
  const monitorSpark = new SparkRenderer({ renderer: monitorRenderer });
  mainScene.add(mainSpark);
  monitorScene.add(monitorSpark);
  const grid = new THREE.GridHelper(30, 30, color("--cp-border-strong"), color("--cp-border"));
  const axes = new THREE.AxesHelper(2);
  mainScene.add(grid, axes);
  const contentRoot = new THREE.Group();
  const monitorContentRoot = new THREE.Group();
  const helperRoot = new THREE.Group();
  const assetRoot = new THREE.Group();
  const monitorAssetRoot = new THREE.Group();
  mainScene.add(contentRoot, helperRoot, assetRoot);
  monitorScene.add(monitorContentRoot, monitorAssetRoot);
  applyBackgroundTransform();

  const objectRoots = new Map();
  const monitorObjectRoots = new Map();
  const pickables = [];
  const handles = [];
  const raycaster = new THREE.Raycaster();
  const pointer = new THREE.Vector2();
  let hoverSelection = null;
  let pathDrawMode = null;
  let pathDrawActive = false;
  let pathDrawPoints = [];
  let pathPreview = null;
  let pathDrawTarget = null;

  function rebuild() {
    transform.detach();
    transformBinding = null;
    disposeChildren(contentRoot);
    disposeChildren(monitorContentRoot);
    disposeChildren(helperRoot);
    objectRoots.clear();
    monitorObjectRoots.clear();
    pickables.length = 0;
    handles.length = 0;
    for (const item of state.scene.objects) {
      const root = createObjectProxy(item, color);
      root.userData = { kind: "object", item };
      root.traverse((child) => {
        if (child.isMesh) {
          child.userData.pickRoot = root;
          pickables.push(child);
        }
      });
      contentRoot.add(root);
      objectRoots.set(item.id, root);
      const monitorRoot = createObjectProxy(item, color);
      monitorContentRoot.add(monitorRoot);
      monitorObjectRoots.set(item.id, monitorRoot);
      buildTrackVisual(item.motion_track, helperRoot, color("--cp-link"), {
        kind: "point",
        item,
      });
    }
    for (const camera of state.cameraRig.cameras) {
      buildCameraVisual(camera);
    }
    updateSelectionHighlight();
  }

  function buildTrackVisual(track, parent, lineColor, bindingBase) {
    const positions = [];
    for (let index = 0; index <= 100; index += 1) positions.push(...rawTrackAt(track, index / 100));
    const geometry = new THREE.BufferGeometry();
    geometry.setAttribute("position", new THREE.Float32BufferAttribute(positions, 3));
    parent.add(new THREE.Line(geometry, new THREE.LineBasicMaterial({ color: lineColor })));
    if (bindingBase.kind === "camera-position-track" || bindingBase.kind === "camera-target-track") return;
    track.points.forEach((point, pointIndex) => {
      const handle = new THREE.Mesh(
        new THREE.SphereGeometry(0.11, 14, 10),
        new THREE.MeshStandardMaterial({
          color: pointIndex === 0
            ? color("--cp-success")
            : pointIndex === track.points.length - 1
              ? color("--cp-warning")
              : lineColor,
          emissive: color("--cp-bg"),
        }),
      );
      handle.position.fromArray(point.position);
      handle.userData = {
        ...bindingBase,
        point,
        pointIndex,
        kind: bindingBase.kind,
        transformObject: handle,
      };
      parent.add(handle);
      pickables.push(handle);
      handles.push(handle);
      if (track.interpolation === "bezier") {
        for (const handleName of ["in_handle", "out_handle"]) {
          if (!point[handleName]) continue;
          const bezierHandle = new THREE.Mesh(
            new THREE.SphereGeometry(0.065, 10, 8),
            new THREE.MeshBasicMaterial({ color: color("--cp-accent") }),
          );
          bezierHandle.position.fromArray(point[handleName]);
          bezierHandle.userData = {
            kind: "bezier-handle",
            point,
            handleName,
            transformObject: bezierHandle,
          };
          parent.add(bezierHandle);
          pickables.push(bezierHandle);
          handles.push(bezierHandle);
        }
      }
    });
  }

  function buildCameraVisual(camera) {
    buildTrackVisual(camera.position_track, helperRoot, color("--cp-accent"), {
      kind: "camera-position-track",
      camera,
    });
    buildTrackVisual(camera.target_track, helperRoot, color("--cp-warning"), {
      kind: "camera-target-track",
      camera,
    });
    camera.keyframes.forEach((frame, frameIndex) => {
      const positionHandle = cameraHandle(color("--cp-accent"), 0.14);
      positionHandle.position.fromArray(frame.position);
      positionHandle.userData = {
        kind: "camera-position",
        camera,
        frame,
        frameIndex,
        transformObject: positionHandle,
      };
      const targetHandle = cameraHandle(color("--cp-warning"), 0.1);
      targetHandle.position.fromArray(frame.target);
      targetHandle.userData = {
        kind: "camera-target",
        camera,
        frame,
        frameIndex,
        transformObject: targetHandle,
      };
      const lineGeometry = new THREE.BufferGeometry().setFromPoints([
        new THREE.Vector3().fromArray(frame.position),
        new THREE.Vector3().fromArray(frame.target),
      ]);
      const lookLine = new THREE.Line(
        lineGeometry,
        new THREE.LineDashedMaterial({ color: color("--cp-border-strong"), dashSize: 0.18, gapSize: 0.1 }),
      );
      lookLine.computeLineDistances();
      helperRoot.add(lookLine);
      const cameraObject = new THREE.PerspectiveCamera(frame.fov, 1.6, 0.2, 1.5);
      cameraObject.position.fromArray(frame.position);
      cameraObject.lookAt(new THREE.Vector3().fromArray(frame.target));
      cameraObject.updateProjectionMatrix();
      const cameraHelper = new THREE.CameraHelper(cameraObject);
      cameraHelper.material.color.copy(color("--cp-link"));
      helperRoot.add(positionHandle, targetHandle, cameraObject, cameraHelper);
      pickables.push(positionHandle, targetHandle);
      handles.push(positionHandle, targetHandle);
    });
  }

  function cameraHandle(handleColor, size) {
    return new THREE.Mesh(
      new THREE.OctahedronGeometry(size),
      new THREE.MeshStandardMaterial({ color: handleColor, emissive: color("--cp-bg") }),
    );
  }

  function pointerDown(event) {
    if (event.button !== 0 || transform.dragging) return;
    if (pathDrawMode) {
      const point = pathPointFromEvent(event);
      if (!point) return;
      event.preventDefault();
      event.stopPropagation();
      if (pathDrawMode === "freehand") {
        pathDrawActive = true;
        pathDrawPoints = [];
        appendPathDrawPoint(point, true);
        mainRenderer.domElement.setPointerCapture?.(event.pointerId);
      } else {
        if (event.detail >= 2 && finishPathDrawing()) {
          state.pathTool = null;
          state.toolMessage = "轨迹已生成，可拖动控制点继续调整";
          state.refreshTools?.();
        } else {
          appendPathDrawPoint(point);
        }
      }
      return;
    }
    const rect = mainRenderer.domElement.getBoundingClientRect();
    pointer.x = ((event.clientX - rect.left) / rect.width) * 2 - 1;
    pointer.y = -((event.clientY - rect.top) / rect.height) * 2 + 1;
    raycaster.setFromCamera(pointer, mainCamera);
    const hit = raycaster.intersectObjects(pickables, false)[0]?.object;
    if (!hit) return;
    const data = hit.userData;
    if (data.pickRoot) {
      const item = data.pickRoot.userData.item;
      state.selectedKind = "object";
      state.selectedObjectId = item.id;
      transformBinding = { kind: "object", item, object: data.pickRoot };
      transform.setMode(state.transformMode);
      transform.attach(data.pickRoot);
    } else if (data.kind === "point") {
      state.selectedKind = "object";
      state.selectedObjectId = data.item.id;
      state.selectedPointIndex = data.pointIndex;
      transformBinding = { ...data, object: hit };
      transform.setMode("translate");
      transform.attach(hit);
    } else if (data.kind === "camera-position" || data.kind === "camera-target") {
      state.selectedKind = "camera";
      state.selectedCameraId = data.camera.id;
      state.selectedKeyframe = data.frameIndex;
      transformBinding = { ...data, object: hit };
      transform.setMode("translate");
      transform.attach(hit);
    } else if (data.kind === "camera-position-track") {
      state.selectedKind = "camera";
      state.selectedCameraId = data.camera.id;
    } else if (data.kind === "bezier-handle") {
      transformBinding = { ...data, object: hit };
      transform.setMode("translate");
      transform.attach(hit);
    }
    updateSelectionHighlight();
    onManipulated();
    event.stopPropagation();
  }

  function pointerMove(event) {
    if (pathDrawMode !== "freehand" || !pathDrawActive) return;
    const point = pathPointFromEvent(event);
    if (!point) return;
    appendPathDrawPoint(point);
  }

  function pointerUp(event) {
    if (pathDrawMode !== "freehand" || !pathDrawActive) return;
    pathDrawActive = false;
    mainRenderer.domElement.releasePointerCapture?.(event.pointerId);
    if (finishPathDrawing()) {
      state.pathTool = null;
      state.toolMessage = "轨迹已生成，可拖动控制点继续调整";
      state.refreshTools?.();
    }
  }

  function selectedPathTarget(reference = null) {
    const kind = reference?.kind || state.selectedKind;
    if (kind === "object") {
      const id = reference?.id || state.selectedObjectId;
      const item = state.scene.objects.find((entry) => entry.id === id);
      return item ? { kind: "object", item, track: item.motion_track } : null;
    }
    const id = reference?.id || state.selectedCameraId;
    const camera = state.cameraRig.cameras.find((entry) => entry.id === id);
    return camera ? { kind: "camera", camera, track: camera.position_track } : null;
  }

  function pathPointFromEvent(event) {
    const target = selectedPathTarget(pathDrawTarget);
    if (!target) return null;
    const rect = mainRenderer.domElement.getBoundingClientRect();
    pointer.x = ((event.clientX - rect.left) / rect.width) * 2 - 1;
    pointer.y = -((event.clientY - rect.top) / rect.height) * 2 + 1;
    raycaster.setFromCamera(pointer, mainCamera);
    const height = finite(target.track.points[0]?.position?.[1], 0);
    return raycaster.ray.intersectPlane(
      new THREE.Plane(new THREE.Vector3(0, 1, 0), -height),
      new THREE.Vector3(),
    );
  }

  function appendPathDrawPoint(point, force = false) {
    const previous = pathDrawPoints.at(-1);
    if (!force && previous && previous.distanceTo(point) < 0.2) return;
    pathDrawPoints.push(point.clone());
    updatePathPreview();
  }

  function updatePathPreview() {
    if (pathPreview) {
      helperRoot.remove(pathPreview);
      pathPreview.geometry.dispose();
      pathPreview.material.dispose();
      pathPreview = null;
    }
    if (pathDrawPoints.length < 2) return;
    pathPreview = new THREE.Line(
      new THREE.BufferGeometry().setFromPoints(pathDrawPoints),
      new THREE.LineBasicMaterial({ color: color("--cp-accent") }),
    );
    helperRoot.add(pathPreview);
  }

  function clearPathPreview() {
    pathDrawActive = false;
    pathDrawPoints = [];
    if (pathPreview) {
      helperRoot.remove(pathPreview);
      pathPreview.geometry.dispose();
      pathPreview.material.dispose();
      pathPreview = null;
    }
  }

  function applyPathDrawPoints() {
    const target = selectedPathTarget(pathDrawTarget);
    if (!target || pathDrawPoints.length < 2) return false;
    const source = pathDrawPoints.length <= 32
      ? pathDrawPoints
      : Array.from({ length: 32 }, (_, index) =>
        pathDrawPoints[Math.round((index / 31) * (pathDrawPoints.length - 1))]);
    const distances = [0];
    for (let index = 1; index < source.length; index += 1) {
      distances.push(distances[index - 1] + source[index].distanceTo(source[index - 1]));
    }
    const total = Math.max(EPSILON, distances.at(-1));
    if (total <= EPSILON) return false;
    const points = source.map((point, index) => ({
      time: distances[index] / total,
      position: point.toArray(),
    }));
    target.track.points = points;
    target.track.interpolation = pathDrawMode === "freehand" ? "catmull_rom" : "linear";
    if (target.kind === "object") {
      syncObjectLegacy(target.item);
      state.selectedPointIndex = 0;
    } else {
      const oldTarget = target.camera.target_track;
      const oldFov = target.camera.fov_track;
      target.camera.target_track = normalizeTrack({
        ...oldTarget,
        points: points.map((point) => ({
          time: point.time,
          position: evaluateTrack(oldTarget, point.time),
        })),
      }, []);
      target.camera.fov_track = scalarTrack({
        points: points.map((point) => ({
          time: point.time,
          value: evaluateScalarTrack(oldFov, point.time),
        })),
      }, []);
      syncCameraLegacy(target.camera);
      state.selectedKeyframe = 0;
    }
    state.time = 0;
    clearPathPreview();
    pathDrawMode = null;
    pathDrawTarget = null;
    orbit.enabled = true;
    rebuild();
    onManipulated();
    return true;
  }

  function finishPathDrawing() {
    return applyPathDrawPoints();
  }

  function cancelPathDrawing() {
    if (!pathDrawMode && !pathDrawPoints.length) return false;
    clearPathPreview();
    pathDrawMode = null;
    pathDrawTarget = null;
    orbit.enabled = true;
    return true;
  }

  function setPathDrawingMode(mode) {
    cancelPathDrawing();
    const target = selectedPathTarget();
    if (!target) return false;
    pathDrawMode = mode;
    pathDrawTarget = {
      kind: target.kind,
      id: target.kind === "object" ? target.item.id : target.camera.id,
    };
    orbit.enabled = false;
    return true;
  }

  function commitTransform() {
    if (!transformBinding) return;
    if (transformBinding.kind === "object") {
      const { item, object } = transformBinding;
      if (state.transformMode === "translate") {
        const point = upsertTrackPoint(item.motion_track, state.time, object.position.toArray());
        state.selectedPointIndex = item.motion_track.points.indexOf(point);
        syncObjectLegacy(item);
      } else if (state.transformMode === "rotate") {
        item.rotation = [object.rotation.x, object.rotation.y, object.rotation.z];
      } else {
        item.scale = object.scale.toArray().map((value) => Math.max(0.05, value));
        object.scale.set(1, 1, 1);
      }
      rebuild();
    } else if (transformBinding.kind === "bezier-handle") {
      transformBinding.point[transformBinding.handleName] = transformBinding.object.position.toArray();
      rebuild();
    } else if (transformBinding.kind === "camera-position" || transformBinding.kind === "camera-target") {
      syncCameraTracks(transformBinding.camera);
      rebuild();
    } else if (transformBinding.kind === "point") {
      syncObjectLegacy(transformBinding.item);
      rebuild();
    }
  }

  function updateSelectionHighlight() {
    for (const [id, root] of objectRoots) {
      const selected = state.selectedKind === "object" && id === state.selectedObjectId;
      root.traverse((child) => {
        if (!child.isMesh || !child.material?.emissive) return;
        child.material.emissive.copy(selected ? color("--cp-accent") : color("--cp-bg"));
        child.material.emissiveIntensity = selected ? 1.5 : 0.05;
      });
    }
  }

  function updateAnimatedObjects(time) {
    for (const item of state.scene.objects) {
      const root = objectRoots.get(item.id);
      const monitorRoot = monitorObjectRoots.get(item.id);
      const position = evaluateTrack(item.motion_track, time);
      const tangent = item.type === "actor" ? trackTangent(item.motion_track, time) : null;
      for (const target of [root, monitorRoot]) {
        if (!target || (target === root && transform.dragging && transformBinding?.object === root)) {
          continue;
        }
        target.position.fromArray(position);
        target.rotation.set(...vec3(item.rotation));
        target.scale.set(...vec3(item.scale, [1, 1, 1]));
        if (item.type === "actor") {
          if (Math.hypot(tangent[0], tangent[2]) > 0.01) {
            target.rotation.y = Math.atan2(tangent[0], tangent[2]);
          }
          animateActor(
            target,
            item.motion === "walk" ? time * state.duration : 0,
            item.motion === "walk",
          );
        }
      }
    }
  }

  function updateObservationCamera(time, aspect) {
    const observation = state.followCut
      ? renderCameraAt(state.cameraRig, time, state.scene)
      : (() => {
          const camera = state.cameraRig.cameras.find(
            (item) => item.id === state.observationCameraId) || state.cameraRig.cameras[0];
          return camera ? { camera, frame: cameraAt(camera, time, state.scene) } : null;
        })();
    if (!observation) return;
    observationCamera.position.fromArray(observation.frame.position);
    observationCamera.fov = observation.frame.fov;
    observationCamera.aspect = Math.max(EPSILON, aspect);
    observationCamera.lookAt(new THREE.Vector3().fromArray(observation.frame.target));
    observationCamera.rotateZ(THREE.MathUtils.degToRad(observation.frame.roll));
    observationCamera.updateProjectionMatrix();
  }

  function render(time) {
    orbit.update();
    updateAnimatedObjects(time);
    if (state.workspaceMode === "camera") {
      resizeRenderer(mainRenderer, observationCamera, mainHost);
      updateObservationCamera(
        time,
        Math.max(1, mainHost.clientWidth) / Math.max(1, mainHost.clientHeight),
      );
      helperRoot.visible = false;
      mainRenderer.render(mainScene, observationCamera);
    } else {
      resizeRenderer(mainRenderer, mainCamera, mainHost);
      helperRoot.visible = state.workspaceMode === "director";
      mainRenderer.render(mainScene, mainCamera);
    }
    resizeRenderer(monitorRenderer, observationCamera, monitorHost);
    updateObservationCamera(
      time,
      Math.max(1, monitorHost.clientWidth) / Math.max(1, monitorHost.clientHeight),
    );
    monitorRenderer.render(monitorScene, observationCamera);
  }

  function setView(view) {
    const distance = Math.max(6, mainCamera.position.distanceTo(orbit.target));
    if (view === "top") mainCamera.position.copy(orbit.target).add(new THREE.Vector3(0, distance, 0.001));
    else if (view === "front") mainCamera.position.copy(orbit.target).add(new THREE.Vector3(0, 0, distance));
    else if (view === "side") mainCamera.position.copy(orbit.target).add(new THREE.Vector3(distance, 0, 0));
    else mainCamera.position.copy(orbit.target).add(new THREE.Vector3(distance * 0.65, distance * 0.5, distance * 0.85));
    mainCamera.lookAt(orbit.target);
    orbit.update();
  }

  function fitView() {
    const box = new THREE.Box3().setFromObject(contentRoot);
    box.expandByObject(assetRoot);
    if (box.isEmpty()) box.setFromCenterAndSize(new THREE.Vector3(0, 1, 0), new THREE.Vector3(6, 4, 6));
    const center = box.getCenter(new THREE.Vector3());
    const size = box.getSize(new THREE.Vector3());
    const radius = Math.max(3, size.length() * 0.75);
    orbit.target.copy(center);
    mainCamera.position.copy(center).add(new THREE.Vector3(radius, radius * 0.7, radius));
    mainCamera.near = Math.max(0.01, radius / 100);
    mainCamera.far = Math.max(100, radius * 20);
    mainCamera.updateProjectionMatrix();
    orbit.update();
  }

  function applyBackgroundTransform() {
    const value = normalizeBackgroundTransform(state.background.transform);
    state.background.transform = value;
    for (const root of [assetRoot, monitorAssetRoot]) {
      root.position.fromArray(value.position);
      root.rotation.set(
        THREE.MathUtils.degToRad(value.rotation[0]),
        THREE.MathUtils.degToRad(value.rotation[1]),
        THREE.MathUtils.degToRad(value.rotation[2]),
      );
      root.scale.setScalar(value.scale);
      root.updateMatrixWorld(true);
    }
    mainSpark.setDirty();
    monitorSpark.setDirty();
  }

  function clearAssets() {
    mainSpark.clearSplats();
    monitorSpark.clearSplats();
    disposeChildren(assetRoot, true);
    disposeChildren(monitorAssetRoot, true);
  }

  function addAsset(object) {
    if (disposed) {
      object.traverse((child) => {
        child.geometry?.dispose?.();
        if (Array.isArray(child.material)) child.material.forEach((material) => disposeMaterial(material));
        else disposeMaterial(child.material);
      });
      return;
    }
    clearAssets();
    const monitorObject = object.clone(true);
    assetRoot.add(object);
    monitorAssetRoot.add(monitorObject);
    applyBackgroundTransform();
    fitView();
  }

  async function addSplatBytes(bytes, fileName, onProgress) {
    if (disposed) return;
    clearAssets();
    const mainSplat = new SplatMesh({
      fileBytes: bytes.slice(0),
      fileName,
      lod: true,
      onProgress,
    });
    const monitorSplat = new SplatMesh({
      fileBytes: bytes.slice(0),
      fileName,
      lod: true,
      onProgress,
    });
    assetRoot.add(mainSplat);
    monitorAssetRoot.add(monitorSplat);
    await Promise.all([mainSplat.initialized, monitorSplat.initialized]);
    if (disposed) return;
    applyBackgroundTransform();
    mainSpark.setDirty();
    monitorSpark.setDirty();
    fitView();
  }

  function alignBackground() {
    const box = new THREE.Box3().setFromObject(assetRoot);
    if (box.isEmpty()) return null;
    const center = box.getCenter(new THREE.Vector3());
    const next = normalizeBackgroundTransform(state.background.transform);
    next.position[0] -= center.x;
    next.position[1] -= box.min.y;
    next.position[2] -= center.z;
    state.background.transform = next;
    applyBackgroundTransform();
    fitView();
    return structuredClone(next);
  }

  async function captureFrame(time, width, height) {
    if (disposed) throw new Error("预演编辑器已关闭");
    updateAnimatedObjects(time);
    updateObservationCamera(time, width / height);
    monitorRenderer.setPixelRatio(1);
    monitorRenderer.setSize(width, height, false);
    await monitorSpark.update({ scene: monitorScene, camera: observationCamera });
    monitorRenderer.render(monitorScene, observationCamera);
    const blob = await new Promise((resolve, reject) => {
      monitorRenderer.domElement.toBlob(
        (value) => value ? resolve(value) : reject(new Error("浏览器无法读取 WebGL 渲染帧")),
        "image/png",
      );
    });
    return blob;
  }

  function dispose() {
    disposed = true;
    mainRenderer.domElement.removeEventListener("pointerdown", pointerDown);
    mainRenderer.domElement.removeEventListener("pointermove", pointerMove);
    mainRenderer.domElement.removeEventListener("pointerup", pointerUp);
    resizeObserver.disconnect();
    transform.detach();
    transform.dispose();
    orbit.dispose();
    disposeChildren(contentRoot);
    disposeChildren(monitorContentRoot);
    disposeChildren(helperRoot);
    disposeChildren(assetRoot, true);
    disposeChildren(monitorAssetRoot, true);
    mainSpark.dispose();
    monitorSpark.dispose();
    mainScene.remove(transformHelper);
    mainRenderer.dispose();
    monitorRenderer.dispose();
    mainRenderer.forceContextLoss();
    monitorRenderer.forceContextLoss();
    mainRenderer.domElement.remove();
    monitorRenderer.domElement.remove();
  }

  const resizeObserver = new ResizeObserver(() => {
    resizeRenderer(mainRenderer, mainCamera, mainHost);
    resizeRenderer(monitorRenderer, observationCamera, monitorHost);
  });
  resizeObserver.observe(mainHost);
  resizeObserver.observe(monitorHost);
  mainRenderer.domElement.addEventListener("pointerdown", pointerDown);
  mainRenderer.domElement.addEventListener("pointermove", pointerMove);
  mainRenderer.domElement.addEventListener("pointerup", pointerUp);
  rebuild();

  return {
    render,
    rebuild,
    dispose,
    fitView,
    setView,
    addAsset,
    addSplatBytes,
    clearAssets,
    alignBackground,
    captureFrame,
    setBackgroundTransform: applyBackgroundTransform,
    setGridVisible: (visible) => {
      grid.visible = visible;
      axes.visible = visible;
    },
    setTransformMode: (mode) => transform.setMode(mode),
    setPathDrawingMode,
    finishPathDrawing,
    cancelPathDrawing,
    getCurrentViewFrame: () => ({
      time: state.time,
      position: mainCamera.position.toArray(),
      target: orbit.target.toArray(),
      fov: mainCamera.fov,
      roll: 0,
    }),
    selectObject: (item) => {
      state.selectedKind = "object";
      state.selectedObjectId = item.id;
      updateSelectionHighlight();
      const root = objectRoots.get(item.id);
      if (root) {
        transformBinding = { kind: "object", item, object: root };
        transform.setMode(state.transformMode);
        transform.attach(root);
      }
    },
    selectObjectPoint: (item, index) => {
      state.selectedKind = "object";
      state.selectedObjectId = item.id;
      state.selectedPointIndex = index;
      const handle = handles.find((candidate) =>
        candidate.userData.kind === "point"
        && candidate.userData.item === item
        && candidate.userData.pointIndex === index);
      if (handle) {
        transformBinding = { ...handle.userData, object: handle };
        transform.setMode("translate");
        transform.attach(handle);
      }
    },
    selectCameraFrame: (camera, index, target = false) => {
      state.selectedKind = "camera";
      state.selectedCameraId = camera.id;
      state.selectedKeyframe = index;
      const kind = target ? "camera-target" : "camera-position";
      const handle = handles.find((candidate) =>
        candidate.userData.kind === kind
        && candidate.userData.camera === camera
        && candidate.userData.frameIndex === index);
      if (handle) {
        transformBinding = { ...handle.userData, object: handle };
        transform.setMode("translate");
        transform.attach(handle);
      }
    },
  };
}

function createObjectProxy(item, color) {
  const root = new THREE.Group();
  root.name = item.name;
  const material = () => new THREE.MeshStandardMaterial({
    color: color("--cp-text-soft"),
    roughness: 0.72,
    metalness: 0.02,
    emissive: color("--cp-bg"),
  });
  if (item.type === "box") {
    const mesh = new THREE.Mesh(new THREE.BoxGeometry(1, 1, 1), material());
    mesh.position.y = 0.5;
    mesh.castShadow = true;
    mesh.receiveShadow = true;
    root.add(mesh);
  } else if (item.type === "sphere") {
    const mesh = new THREE.Mesh(new THREE.SphereGeometry(0.5, 24, 16), material());
    mesh.position.y = 0.5;
    mesh.castShadow = true;
    root.add(mesh);
  } else {
    const torso = new THREE.Mesh(new THREE.CapsuleGeometry(0.28, 0.62, 6, 12), material());
    torso.position.y = 1.22;
    torso.name = "torso";
    const head = new THREE.Mesh(new THREE.SphereGeometry(0.22, 20, 14), material());
    head.position.y = 1.92;
    const hips = new THREE.Mesh(new THREE.BoxGeometry(0.48, 0.22, 0.26), material());
    hips.position.y = 0.78;
    root.add(torso, head, hips);
    for (const [name, x, y, length] of [
      ["arm-left", -0.39, 1.28, 0.62],
      ["arm-right", 0.39, 1.28, 0.62],
      ["leg-left", -0.15, 0.38, 0.72],
      ["leg-right", 0.15, 0.38, 0.72],
    ]) {
      const limb = new THREE.Group();
      limb.name = name;
      limb.position.set(x, y + length * 0.5, 0);
      const segment = new THREE.Mesh(new THREE.CapsuleGeometry(0.095, length, 5, 10), material());
      segment.position.y = -length * 0.5;
      limb.add(segment);
      root.add(limb);
    }
    root.traverse((child) => {
      if (child.isMesh) child.castShadow = true;
    });
  }
  return root;
}

function animateActor(root, seconds, walking) {
  const phase = walking ? Math.sin(seconds * Math.PI * 3.6) : 0;
  const leftArm = root.getObjectByName("arm-left");
  const rightArm = root.getObjectByName("arm-right");
  const leftLeg = root.getObjectByName("leg-left");
  const rightLeg = root.getObjectByName("leg-right");
  if (leftArm) leftArm.rotation.x = phase * 0.55;
  if (rightArm) rightArm.rotation.x = -phase * 0.55;
  if (leftLeg) leftLeg.rotation.x = -phase * 0.45;
  if (rightLeg) rightLeg.rotation.x = phase * 0.45;
  const torso = root.getObjectByName("torso");
  if (torso) torso.position.y = 1.22 + (walking ? Math.abs(phase) * 0.025 : 0);
}

function buildTimeline(state) {
  const root = element("section", "vod-previs__timeline");
  const transport = element("div", "vod-previs__transport");
  const play = button("播放", () => {
    state.playing = !state.playing;
    render();
  });
  const info = textElement(
    "span",
    "",
    `${state.frameCount} 帧 · ${state.fps} FPS · ${state.duration.toFixed(2)}s`,
  );
  transport.append(play, info);
  const ruler = element("div", "vod-previs__ruler");
  const rulerTicks = element("div", "vod-previs__ruler-ticks");
  const actions = element("div", "vod-previs__timeline-actions");
  actions.append(
    button("+ 轨迹点", () => addTimelinePoint(state)),
    button("+ CUT", () => addTimelineCut(state)),
  );
  const readout = textElement("div", "vod-previs__time-readout", "");
  const rulerPlayhead = element("div", "vod-previs__playhead");
  ruler.append(rulerTicks, actions, readout, rulerPlayhead);
  const labels = element("div", "vod-previs__track-labels");
  const tracks = element("div", "vod-previs__tracks");
  root.append(transport, ruler, labels, tracks);
  let syncingScroll = false;
  const syncScroll = (source, target) => {
    if (syncingScroll) return;
    syncingScroll = true;
    target.scrollTop = source.scrollTop;
    requestAnimationFrame(() => { syncingScroll = false; });
  };
  labels.addEventListener("scroll", () => syncScroll(labels, tracks));
  tracks.addEventListener("scroll", () => syncScroll(tracks, labels));

  const scrub = (event, target) => {
    const rect = target.getBoundingClientRect();
    state.time = clamp((event.clientX - rect.left) / Math.max(1, rect.width), 0, 1);
    state.playing = false;
    render();
  };
  ruler.addEventListener("pointerdown", (event) => {
    if (event.target === ruler) scrub(event, ruler);
  });

  function render() {
    play.textContent = state.playing ? "暂停" : "播放";
    const frame = Math.min(state.frameCount - 1, Math.floor(state.time * state.frameCount));
    readout.textContent = `${(state.time * state.duration).toFixed(2)}s · F${frame}`;
    rulerPlayhead.style.left = `${state.time * 100}%`;
    labels.replaceChildren();
    tracks.replaceChildren();
    rulerTicks.replaceChildren();
    const wholeSeconds = Math.max(1, Math.ceil(state.duration));
    for (let second = 0; second <= wholeSeconds; second += 1) {
      const tick = textElement("span", "vod-previs__ruler-tick", `${second}s`);
      tick.style.left = `${clamp(second / state.duration, 0, 1) * 100}%`;
      rulerTicks.appendChild(tick);
    }
    const rows = [];
    for (const item of state.scene.objects) {
      rows.push({
        label: `${item.type === "actor" ? "人物" : "物体"} · ${item.name}`,
        group: true,
        selectRow: () => {
          state.selectedKind = "object";
          state.selectedObjectId = item.id;
          state.refreshInspector?.();
        },
      });
      rows.push({
        label: "动作",
        child: true,
        clips: [{
          start: 0,
          end: 1,
          label: `${item.motion === "walk" ? "行走" : "静止"} · ${state.duration.toFixed(1)}s`,
          select: () => {
            state.selectedKind = "object";
            state.selectedObjectId = item.id;
            state.refreshInspector?.();
          },
        }],
      });
      rows.push({
        label: "空间轨迹",
        child: true,
        points: item.motion_track.points,
        active: (index) => state.selectedKind === "object"
          && state.selectedObjectId === item.id
          && state.selectedPointIndex === index,
        select: (index) => {
          state.selectedKind = "object";
          state.selectedObjectId = item.id;
          state.selectedPointIndex = index;
          state.time = item.motion_track.points[index].time;
          state.refreshInspector?.();
          state.rebuildScene?.();
        },
      });
    }
    for (const camera of state.cameraRig.cameras) {
      rows.push({
        label: `摄影机 · ${camera.name}`,
        group: true,
        selectRow: () => {
          state.selectedKind = "camera";
          state.selectedCameraId = camera.id;
          state.refreshInspector?.();
        },
      });
      const cameraClips = cutSegments(state.cameraRig).filter(
        (segment) => segment.camera_id === camera.id,
      );
      rows.push({
        label: "运镜片段",
        child: true,
        camera: true,
        clips: cameraClips.map((segment) => ({
          start: segment.start,
          end: segment.end,
          label: `${camera.name} · ${((segment.end - segment.start) * state.duration).toFixed(1)}s`,
          select: () => {
            state.selectedKind = "camera";
            state.selectedCameraId = camera.id;
            state.time = segment.start;
            state.refreshInspector?.();
          },
        })),
      });
      rows.push({
        label: "空间轨迹",
        child: true,
        points: camera.keyframes,
        active: (index) => state.selectedKind === "camera"
          && state.selectedCameraId === camera.id
          && state.selectedKeyframe === index,
        select: (index) => {
          state.selectedKind = "camera";
          state.selectedCameraId = camera.id;
          state.selectedKeyframe = index;
          state.time = camera.keyframes[index].time;
          state.refreshInspector?.();
          state.rebuildScene?.();
        },
      });
      if (camera.look_at_object_id) {
        const target = state.scene.objects.find(
          (item) => item.id === camera.look_at_object_id);
        rows.push({
          label: "Look At",
          child: true,
          camera: true,
          clips: [{
            start: 0,
            end: 1,
            label: `始终看向 · ${target?.name || camera.look_at_object_id}`,
            select: () => {
              state.selectedKind = "camera";
              state.selectedCameraId = camera.id;
              state.refreshInspector?.();
            },
          }],
        });
      }
    }
    rows.push({
      label: "剪辑 CUTS",
      clips: cutSegments(state.cameraRig).map((segment) => {
        const camera = state.cameraRig.cameras.find(
          (item) => item.id === segment.camera_id);
        return {
          start: segment.start,
          end: segment.end,
          label: camera?.name || segment.camera_id,
          cut: true,
          select: () => {
            state.time = segment.start;
            state.selectedKind = "camera";
            state.selectedCameraId = segment.camera_id;
            state.refreshInspector?.();
          },
        };
      }),
    });
    rows.forEach((row) => {
      const labelClass = [
        "vod-previs__track-label",
        row.group ? "vod-previs__track-label--group" : "",
        row.child ? "vod-previs__track-label--child" : "",
      ].filter(Boolean).join(" ");
      const label = textElement("div", labelClass, row.label);
      if (row.selectRow) {
        label.addEventListener("click", row.selectRow);
        label.style.cursor = "pointer";
      }
      labels.appendChild(label);
      const track = element(
        "div",
        `vod-previs__track${row.group ? " vod-previs__track--group" : ""}`,
      );
      if (!row.group && row.points) {
        track.appendChild(element("div", "vod-previs__track-line"));
      }
      const playhead = element("div", "vod-previs__playhead");
      playhead.style.left = `${state.time * 100}%`;
      track.appendChild(playhead);
      track.addEventListener("pointerdown", (event) => {
        if (event.target !== track) return;
        scrub(event, track);
      });
      for (const clip of row.clips || []) {
        const clipElement = textElement(
          "button",
          `vod-previs__clip${row.camera ? " vod-previs__clip--camera" : ""}${
            clip.cut ? " vod-previs__clip--cut" : ""}`,
          clip.label,
        );
        clipElement.type = "button";
        clipElement.style.left = `${clamp(clip.start, 0, 1) * 100}%`;
        clipElement.style.width = `${Math.max(0.5, clamp(clip.end - clip.start, 0, 1) * 100)}%`;
        clipElement.addEventListener("click", (event) => {
          event.stopPropagation();
          clip.select?.();
          render();
        });
        track.appendChild(clipElement);
      }
      (row.points || []).forEach((point, index) => {
        const marker = element("button", `vod-previs__marker${row.cut ? " vod-previs__marker--cut" : ""}`);
        marker.type = "button";
        marker.style.left = `${clamp(point.time, 0, 1) * 100}%`;
        marker.dataset.active = String(row.active(index));
        marker.setAttribute(
          "aria-label",
          `${row.label} ${(point.time * state.duration).toFixed(2)}s`,
        );
        marker.addEventListener("click", (event) => {
          event.stopPropagation();
          row.select(index);
          render();
        });
        track.appendChild(marker);
      });
      tracks.appendChild(track);
    });
  }
  return { root, render };
}

function cutSegments(rig) {
  return rig.cuts.map((cut, index) => ({
    start: cut.time,
    end: rig.cuts[index + 1]?.time ?? 1,
    camera_id: cut.camera_id,
  }));
}

function addTimelinePoint(state) {
  if (state.selectedKind === "object") {
    const item = state.scene.objects.find((entry) => entry.id === state.selectedObjectId);
    if (!item || item.motion_track.points.length >= 32) return;
    const point = upsertTrackPoint(
      item.motion_track,
      state.time,
      evaluateTrack(item.motion_track, state.time),
    );
    state.selectedPointIndex = item.motion_track.points.indexOf(point);
    syncObjectLegacy(item);
  } else {
    const camera = getSelectedCamera(state);
    if (!camera) return;
    const frame = cameraAt(camera, state.time, state.scene);
    frame.time = uniqueTime(camera.keyframes, null, state.time);
    camera.keyframes.push(frame);
    camera.keyframes.sort((a, b) => a.time - b.time);
    state.selectedKeyframe = camera.keyframes.indexOf(frame);
    syncCameraTracks(camera);
  }
  state.rebuildScene?.();
  refreshAll(state);
}

function addTimelineCut(state) {
  const camera = getSelectedCamera(state);
  if (!camera) return;
  const existing = state.cameraRig.cuts.find(
    (cut) => Math.abs(cut.time - state.time) < EPSILON);
  if (existing) existing.camera_id = camera.id;
  else state.cameraRig.cuts.push({ time: state.time, camera_id: camera.id });
  state.cameraRig.cuts = normalizeCuts(
    state.cameraRig.cuts,
    state.cameraRig.cameras,
    state.cameraRig.active_camera,
  );
  refreshAll(state);
}

function renderCameraTabs(container, state, runtime, monitorLabel) {
  container.replaceChildren();
  const follow = button("跟随剪辑", () => {
    state.followCut = !state.followCut;
    follow.dataset.active = String(state.followCut);
    state.refreshTabs?.();
  });
  follow.dataset.active = String(state.followCut);
  container.appendChild(follow);
  for (const camera of state.cameraRig.cameras) {
    const tab = button(camera.name, () => {
      state.followCut = false;
      state.observationCameraId = camera.id;
      state.refreshTabs?.();
    });
    tab.dataset.active = String(!state.followCut && state.observationCameraId === camera.id);
    container.appendChild(tab);
  }
  const observed = state.followCut
    ? renderCameraAt(state.cameraRig, state.time)?.camera
    : state.cameraRig.cameras.find((camera) => camera.id === state.observationCameraId);
  monitorLabel.textContent = state.followCut
    ? `OBSERVATION · FOLLOW CUT · ${observed?.name || "NO CAMERA"}`
    : `OBSERVATION · ${observed?.name || "NO CAMERA"}`;
  runtime?.render(state.time);
}

async function responseJson(response) {
  let payload = null;
  try {
    payload = await response.json();
  } catch {
    throw new Error(`服务返回了无法解析的响应（HTTP ${response.status}）`);
  }
  if (!response.ok || payload?.ok === false) {
    throw new Error(payload?.error || `请求失败（HTTP ${response.status}）`);
  }
  return payload;
}

async function submitWorldGeneration(state) {
  const generation = state.generation;
  if (!generation.confirmed) throw new Error("请先确认这会创建真实的 Tencent VOD 付费任务");
  if (!generation.prompt.trim()) throw new Error("请填写场景描述");
  generation.status = "submitting";
  generation.message = "正在上传参考图并创建任务…";
  state.refreshInspector?.();
  const form = new FormData();
  form.set("prompt", generation.prompt.trim());
  form.set("storage_mode", generation.storageMode);
  form.set("confirmed", "true");
  form.set("use_cache", "Enabled");
  generation.images.filter(Boolean).forEach((file) => form.append("image", file, file.name));
  const created = await responseJson(await fetch(
    "/tencent-vod-aigc/previs/world-tasks",
    { method: "POST", headers: PREVIS_REQUEST_HEADER, body: form },
  ));
  generation.status = "running";
  generation.message = "混元 3D 世界生成中…";
  state.refreshInspector?.();
  while (!state.disposed) {
    await new Promise((resolve) => setTimeout(resolve, 2500));
    const job = await responseJson(await fetch(
      `/tencent-vod-aigc/previs/world-tasks/${encodeURIComponent(created.job_id)}`,
    ));
    generation.status = job.status;
    generation.message = job.message || "处理中…";
    if (job.status === "complete") {
      state.background.source = "Tencent VOD Generated";
      state.background.path = job.scene_path;
      state.background.taskId = job.task_id;
      invalidateRenderCache(state);
      await state.loadBackground?.(job.scene_path);
      state.refreshInspector?.();
      return;
    }
    if (job.status === "error") {
      state.refreshInspector?.();
      throw new Error(job.message || "3D 世界生成失败");
    }
    state.refreshInspector?.();
  }
}

async function uploadLocalAsset(file, state) {
  if (!file) return;
  state.generation.status = "submitting";
  state.generation.message = `正在上传本地场景：${file.name}`;
  state.refreshInspector?.();
  try {
    const form = new FormData();
    form.set("asset", file, file.name);
    const uploaded = await responseJson(await fetch(
      "/tencent-vod-aigc/previs/assets",
      { method: "POST", headers: PREVIS_REQUEST_HEADER, body: form },
    ));
    state.background.source = "Local Asset";
    state.background.path = uploaded.path;
    state.background.taskId = "";
    invalidateRenderCache(state);
    state.generation.status = "loading";
    state.generation.message = `正在加载本地场景：${uploaded.name}`;
    state.refreshInspector?.();
    await state.loadBackground?.(uploaded.path);
    state.generation.status = "complete";
    state.generation.message = `已加载本地场景：${uploaded.name}`;
  } catch (error) {
    state.generation.status = "error";
    state.generation.message = error.message;
  }
  state.refreshInspector?.();
}

function renderInspectorTabs(container, state, refresh) {
  container.replaceChildren();
  for (const [label, tab] of [["场景", "scene"], ["对象 / 机位", "selection"]]) {
    const control = button(label, () => {
      state.sideTab = tab;
      renderInspectorTabs(container, state, refresh);
      refresh?.();
    });
    control.dataset.active = String(state.sideTab === tab);
    container.appendChild(control);
  }
}

function renderDirectorTools(container, state, runtime) {
  container.replaceChildren();
  const addTool = (label, handler, active = false, title = label) => {
    const control = button(label, handler);
    control.dataset.active = String(active);
    control.title = title;
    container.appendChild(control);
    return control;
  };
  addTool("选择", () => {
    state.pathTool = null;
    runtime.cancelPathDrawing?.();
    state.toolMessage = "选择场景中的人物、物体、轨迹点或摄影机控制点";
    state.refreshTools?.();
  }, !state.pathTool, "选择对象和控制点");
  for (const [label, mode] of [["移动", "translate"], ["旋转", "rotate"], ["缩放", "scale"]]) {
    addTool(label, () => {
      state.pathTool = null;
      runtime.cancelPathDrawing?.();
      state.transformMode = mode;
      runtime.setTransformMode(mode);
      state.toolMessage = `${label}模式`;
      state.refreshTools?.();
    }, !state.pathTool && state.transformMode === mode, `${label}所选对象`);
  }
  container.appendChild(element("div", "vod-previs__tool-separator"));
  addTool("+ 人物", () => addSceneObject(state, runtime, "actor"), false, "添加人物简模");
  addTool("+ 体块", () => addSceneObject(state, runtime, "box"), false, "添加场景体块");
  addTool("+ 机位", () => addCameraFromCurrentView(state, runtime), false, "从当前导演视角创建机位");
  container.appendChild(element("div", "vod-previs__tool-separator"));
  addTool("手绘轨迹", () => activatePathTool(state, runtime, "freehand"), state.pathTool === "freehand",
    "按住鼠标左键在水平面上绘制轨迹");
  addTool("逐点轨迹", () => activatePathTool(state, runtime, "point"), state.pathTool === "point",
    "逐点点击创建轨迹，按 Enter 完成");
  if (state.pathTool) {
    addTool("完成轨迹", () => {
      if (!runtime.finishPathDrawing?.()) return;
      state.pathTool = null;
      state.toolMessage = "轨迹已生成，可拖动控制点继续调整";
      state.refreshTools?.();
    }, false, "完成当前轨迹");
    addTool("取消绘制", () => {
      runtime.cancelPathDrawing?.();
      state.pathTool = null;
      state.toolMessage = "已取消轨迹绘制";
      state.refreshTools?.();
    }, false, "取消当前轨迹");
  }
}

function activatePathTool(state, runtime, mode) {
  const hasTarget = state.selectedKind === "object"
    ? state.scene.objects.some((item) => item.id === state.selectedObjectId)
    : state.cameraRig.cameras.some((camera) => camera.id === state.selectedCameraId);
  if (!hasTarget) {
    state.toolMessage = "请先选择人物、物体或摄影机";
    state.refreshTools?.();
    return;
  }
  state.workspaceMode = "director";
  state.pathTool = mode;
  runtime.setPathDrawingMode?.(mode);
  state.toolMessage = mode === "freehand"
    ? "手绘轨迹：按住鼠标左键绘制，松开后自动生成关键帧"
    : "逐点轨迹：依次点击路径位置，按 Enter 或“完成轨迹”结束";
  state.refreshTools?.();
}

function addSceneObject(state, runtime, type) {
  const id = uniqueId(state.scene.objects, type);
  const item = {
    id,
    name: type === "actor" ? "人物" : type === "sphere" ? "球体" : "体块",
    type,
    position: [0, 0, 0],
    end: [0, 0, 0],
    path: [{ time: 0, position: [0, 0, 0] }, { time: 1, position: [0, 0, 0] }],
    scale: [1, 1, 1],
    rotation: [0, 0, 0],
    motion: type === "actor" ? "walk" : "static",
    motion_track: normalizeTrack(null, [
      { time: 0, position: [0, 0, 0] },
      { time: 1, position: [0, 0, 0] },
    ]),
  };
  state.scene.objects.push(item);
  state.selectedKind = "object";
  state.selectedObjectId = id;
  state.selectedPointIndex = 0;
  state.sideTab = "selection";
  state.toolMessage = `已添加${item.name}，可直接移动或绘制轨迹`;
  runtime.rebuild();
  runtime.selectObject(item);
  state.refreshInspectorTabs?.();
  refreshAll(state);
}

function addCameraFromCurrentView(state, runtime) {
  if (state.cameraRig.cameras.length >= MAX_CAMERAS) {
    state.toolMessage = `最多支持 ${MAX_CAMERAS} 台摄影机`;
    state.refreshTools?.();
    return;
  }
  const id = uniqueId(state.cameraRig.cameras, "camera");
  const frame = runtime.getCurrentViewFrame();
  frame.time = state.time;
  const camera = normalizeCamera({
    id,
    name: `Camera ${state.cameraRig.cameras.length + 1}`,
    keyframes: [frame],
  }, state.cameraRig.cameras.length, new Set(state.cameraRig.cameras.map((item) => item.id)));
  state.cameraRig.cameras.push(camera);
  state.selectedKind = "camera";
  state.selectedCameraId = camera.id;
  state.selectedKeyframe = 0;
  state.observationCameraId = camera.id;
  state.followCut = false;
  state.sideTab = "selection";
  state.toolMessage = `已从当前视角创建 ${camera.name}`;
  runtime.rebuild();
  runtime.selectCameraFrame(camera, 0);
  state.refreshInspectorTabs?.();
  refreshAll(state);
}

function renderSceneSourcePanel(container, state, runtime) {
  const details = element("details", "vod-previs__scene-source");
  details.open = state.generation.status !== "idle" || !state.background.path;
  const summary = document.createElement("summary");
  summary.textContent = state.background.path
    ? `背景场景 · ${state.background.path.split(/[\\/]/).pop()}`
    : "背景场景 · 空白";
  details.appendChild(summary);
  details.appendChild(selectInput(
    "场景来源",
    ["Blank", "Local Asset", "Tencent VOD Generated"],
    state.background.source,
    (value) => {
      state.background.source = value;
      invalidateRenderCache(state);
      if (value === "Blank") {
        state.background.path = "";
        state.background.taskId = "";
        runtime.clearAssets();
      }
      state.refreshInspector?.();
    },
  ));
  if (state.background.source === "Local Asset") {
    const upload = element("label", "vod-previs__upload vod-previs__upload--asset");
    const uploadInput = document.createElement("input");
    uploadInput.type = "file";
    uploadInput.accept = ".glb,.gltf,.obj,.ply,.spz";
    uploadInput.addEventListener("change", () => {
      const file = uploadInput.files?.[0];
      if (file) uploadLocalAsset(file, state);
    });
    upload.append(
      uploadInput,
      textElement("div", "", "选择并加载本地 3D 文件"),
      textElement("div", "", "GLB / GLTF / OBJ / PLY / SPZ（推荐 GLB 或 SPZ）"),
    );
    details.appendChild(upload);
    details.appendChild(textInput(
      "或填写 ComfyUI input / output / temp 内的资产路径",
      state.background.path,
      (value) => {
        state.background.path = value.trim();
        state.background.taskId = "";
        invalidateRenderCache(state);
      },
    ));
    details.appendChild(button("加载路径中的场景", async () => {
      try {
        if (!state.background.path.trim()) {
          throw new Error("请先选择本地 3D 文件，或填写允许访问的资产路径");
        }
        state.generation.status = "loading";
        state.generation.message = "正在加载本地场景…";
        state.refreshInspector?.();
        await state.loadBackground?.(state.background.path);
        state.generation.status = "complete";
        state.generation.message = `已加载本地场景：${
          state.background.path.split(/[\\/]/).pop()}`;
      } catch (error) {
        state.generation.status = "error";
        state.generation.message = error.message;
      }
      state.refreshInspector?.();
    }, true));
  }
  if (state.background.source === "Tencent VOD Generated") {
    const promptField = element("label", "vod-previs__field");
    promptField.appendChild(textElement("span", "", "场景描述"));
    const prompt = element("textarea", "vod-previs__textarea");
    prompt.placeholder = "描述空间布局、墙面、窗户、地面纵深、时代与材质";
    prompt.value = state.generation.prompt;
    prompt.addEventListener("input", () => { state.generation.prompt = prompt.value; });
    promptField.appendChild(prompt);
    details.appendChild(promptField);
    const uploads = element("div", "vod-previs__upload-grid");
    state.generation.images.forEach((file, index) => {
      const label = element("label", "vod-previs__upload");
      const input = document.createElement("input");
      input.type = "file";
      input.accept = "image/png,image/jpeg,image/webp";
      input.addEventListener("change", () => {
        state.generation.images[index] = input.files?.[0] || null;
        state.refreshInspector?.();
      });
      label.append(
        input,
        textElement("div", "", file ? file.name : `参考图 ${index + 1}`),
        textElement("div", "", file ? "点击替换" : "点击选择"),
      );
      uploads.appendChild(label);
    });
    details.appendChild(uploads);
    details.appendChild(selectInput(
      "存储模式",
      ["Temporary", "Permanent"],
      state.generation.storageMode,
      (value) => { state.generation.storageMode = value; },
    ));
    const confirm = element("label", "vod-previs__confirm");
    const checkbox = document.createElement("input");
    checkbox.type = "checkbox";
    checkbox.checked = state.generation.confirmed;
    checkbox.addEventListener("change", () => {
      state.generation.confirmed = checkbox.checked;
      state.refreshInspector?.();
    });
    confirm.append(
      checkbox,
      textElement(
        "span",
        "",
        "我确认提交会调用 Tencent VOD 混元 3D，并可能产生费用；实际价格以账户配置为准。",
      ),
    );
    details.appendChild(confirm);
    const submit = button("确认并生成 3D 场景", async () => {
      try {
        await submitWorldGeneration(state);
      } catch (error) {
        state.generation.status = "error";
        state.generation.message = error.message;
        state.refreshInspector?.();
      }
    }, true);
    submit.disabled = !state.generation.confirmed
      || !state.generation.prompt.trim()
      || ["submitting", "queued", "running"].includes(state.generation.status);
    details.appendChild(submit);
  }
  if (state.generation.message) {
    const status = textElement("div", "vod-previs__status", state.generation.message);
    status.dataset.state = state.generation.status;
    details.appendChild(status);
  }
  if (state.background.path) {
    details.appendChild(textElement("div", "vod-previs__section-title", "背景坐标校准"));
    details.appendChild(vecInput("Position", state.background.transform.position, () => {
      invalidateRenderCache(state);
      runtime.setBackgroundTransform(state.background.transform);
    }));
    details.appendChild(vecInput("Rotation °", state.background.transform.rotation, () => {
      invalidateRenderCache(state);
      runtime.setBackgroundTransform(state.background.transform);
    }, 1));
    details.appendChild(numberInput("Uniform Scale", state.background.transform.scale, (value) => {
      state.background.transform.scale = clamp(value, 0.001, 1000);
      invalidateRenderCache(state);
      runtime.setBackgroundTransform(state.background.transform);
    }, 0.01));
    details.appendChild(button("按边界居中并落地", () => {
      const aligned = runtime.alignBackground();
      if (aligned) {
        state.background.transform = aligned;
        invalidateRenderCache(state);
        state.refreshInspector?.();
      }
    }));
  }
  container.appendChild(details);
}

function renderSidePanel(container, state, runtime) {
  container.replaceChildren();
  if (state.sideTab === "scene") {
    renderSceneSourcePanel(container, state, runtime);
    return;
  }
  const grid = element("div", "vod-previs__side-grid");
  const hierarchy = element("div", "vod-previs__hierarchy");
  const inspector = element("div");
  grid.append(hierarchy, inspector);
  container.appendChild(grid);
  hierarchy.appendChild(textElement("div", "vod-previs__section-title", "场景层级"));
  const objectList = element("div", "vod-previs__list");
  for (const item of state.scene.objects) {
    const row = hierarchyRow(
      `${item.type === "actor" ? "人物" : item.type === "sphere" ? "球体" : "体块"} · ${item.name}`,
      item.id,
      state.selectedKind === "object" && state.selectedObjectId === item.id,
      () => {
        state.selectedKind = "object";
        state.selectedObjectId = item.id;
        state.selectedPointIndex = clamp(state.selectedPointIndex, 0, item.motion_track.points.length - 1);
        runtime.selectObject(item);
        state.refreshInspector?.();
        state.refreshTimeline?.();
      },
    );
    objectList.appendChild(row);
  }
  hierarchy.appendChild(objectList);
  const addRow = element("div", "vod-previs__button-row");
  for (const [label, type] of [["+ 人", "actor"], ["+ 盒", "box"], ["+ 球", "sphere"]]) {
    addRow.appendChild(button(label, () => addSceneObject(state, runtime, type)));
  }
  hierarchy.appendChild(addRow);
  hierarchy.appendChild(textElement("div", "vod-previs__section-title", `摄影机 · ${state.cameraRig.cameras.length}/${MAX_CAMERAS}`));
  const cameraList = element("div", "vod-previs__list");
  for (const camera of state.cameraRig.cameras) {
    cameraList.appendChild(hierarchyRow(
      camera.name,
      camera.id === state.cameraRig.active_camera ? "ACTIVE" : `${camera.keyframes.length} KF`,
      state.selectedKind === "camera" && state.selectedCameraId === camera.id,
      () => {
        state.selectedKind = "camera";
        state.selectedCameraId = camera.id;
        state.selectedKeyframe = clamp(state.selectedKeyframe, 0, camera.keyframes.length - 1);
        runtime.selectCameraFrame(camera, state.selectedKeyframe);
        state.refreshInspector?.();
        state.refreshTimeline?.();
      },
    ));
  }
  hierarchy.appendChild(cameraList);
  const cameraActions = element("div", "vod-previs__button-row");
  const addCamera = button("+ 当前视角机位", () => addCameraFromCurrentView(state, runtime));
  addCamera.disabled = state.cameraRig.cameras.length >= MAX_CAMERAS;
  const duplicate = button("复制", () => {
    if (state.cameraRig.cameras.length >= MAX_CAMERAS) return;
    const source = getSelectedCamera(state);
    if (!source) return;
    const copy = structuredClone(source);
    copy.id = uniqueId(state.cameraRig.cameras, "camera");
    copy.name = `${source.name} Copy`;
    state.cameraRig.cameras.push(copy);
    state.selectedCameraId = copy.id;
    runtime.rebuild();
    refreshAll(state);
  });
  duplicate.disabled = state.cameraRig.cameras.length >= MAX_CAMERAS;
  cameraActions.append(addCamera, duplicate);
  hierarchy.appendChild(cameraActions);

  inspector.appendChild(textElement("div", "vod-previs__section-title", "所选项目属性"));
  if (state.selectedKind === "object") renderObjectInspector(inspector, state, runtime);
  else renderCameraInspector(inspector, state, runtime);
}

function renderObjectInspector(panel, state, runtime) {
  const item = state.scene.objects.find((entry) => entry.id === state.selectedObjectId);
  if (!item) {
    panel.appendChild(textElement("div", "vod-previs__hint", "选择或添加场景对象。"));
    return;
  }
  panel.appendChild(textInput("名称", item.name, (value) => {
    item.name = value || item.name;
    state.refreshInspector?.();
  }));
  panel.appendChild(selectInput("运动", ["static", "walk"], item.motion, (value) => {
    item.motion = value;
  }));
  appendTrackControls(panel, item.motion_track, () => {
    syncObjectLegacy(item);
    runtime.rebuild();
    refreshAll(state);
  });
  const pointList = element("div", "vod-previs__list");
  item.motion_track.points.forEach((point, index) => {
    pointList.appendChild(hierarchyRow(
      `${index + 1} · ${Math.round(point.time * 100)}%`,
      index === 0 ? "START" : index === item.motion_track.points.length - 1 ? "END" : "POINT",
      index === state.selectedPointIndex,
      () => {
        state.selectedPointIndex = index;
        state.time = point.time;
        runtime.selectObjectPoint(item, index);
        refreshAll(state);
      },
    ));
  });
  panel.append(textElement("div", "vod-previs__section-title", "轨迹点"), pointList);
  state.selectedPointIndex = clamp(state.selectedPointIndex, 0, item.motion_track.points.length - 1);
  const point = item.motion_track.points[state.selectedPointIndex];
  panel.append(
    numberInput("时间 0-1", point.time, (value) => {
      point.time = uniqueTime(item.motion_track.points, point, clamp(value, 0, 1));
      item.motion_track.points.sort((a, b) => a.time - b.time);
      state.selectedPointIndex = item.motion_track.points.indexOf(point);
      state.time = point.time;
      syncObjectLegacy(item);
      runtime.rebuild();
      refreshAll(state);
    }, 0.01, "change"),
    vecInput("Position", point.position, () => {
      syncObjectLegacy(item);
      runtime.rebuild();
    }),
  );
  if (item.motion_track.interpolation === "bezier") {
    initializeBezierHandles(item.motion_track);
    if (state.selectedPointIndex > 0) {
      panel.appendChild(vecInput("Bezier In", point.in_handle, () => runtime.rebuild()));
    }
    if (state.selectedPointIndex < item.motion_track.points.length - 1) {
      panel.appendChild(vecInput("Bezier Out", point.out_handle, () => runtime.rebuild()));
    }
  }
  const actions = element("div", "vod-previs__button-row");
  actions.append(
    button("当前时间加点", () => {
      if (item.motion_track.points.length >= 32) return;
      const pointAtTime = upsertTrackPoint(item.motion_track, state.time, evaluateTrack(item.motion_track, state.time));
      state.selectedPointIndex = item.motion_track.points.indexOf(pointAtTime);
      syncObjectLegacy(item);
      runtime.rebuild();
      refreshAll(state);
    }),
    button("删除点", () => {
      if (item.motion_track.points.length <= 2) return;
      item.motion_track.points.splice(state.selectedPointIndex, 1);
      state.selectedPointIndex = clamp(state.selectedPointIndex, 0, item.motion_track.points.length - 1);
      syncObjectLegacy(item);
      runtime.rebuild();
      refreshAll(state);
    }, false, true),
  );
  panel.appendChild(actions);
  panel.appendChild(vecInput("Scale", item.scale, () => runtime.rebuild(), 0.05));
  panel.appendChild(button("删除对象", () => {
    for (const camera of state.cameraRig.cameras) {
      if (camera.look_at_object_id === item.id) camera.look_at_object_id = "";
    }
    state.scene.objects = state.scene.objects.filter((entry) => entry !== item);
    state.selectedObjectId = state.scene.objects[0]?.id || null;
    state.selectedKind = state.scene.objects.length ? "object" : "camera";
    runtime.rebuild();
    refreshAll(state);
  }, false, true));
}

function renderCameraInspector(panel, state, runtime) {
  const camera = getSelectedCamera(state);
  if (!camera) return;
  panel.appendChild(textInput("摄影机名称", camera.name, (value) => {
    camera.name = value || camera.name;
    refreshAll(state);
  }));
  panel.appendChild(optionSelectInput(
    "Look At 目标",
    [
      { value: "", label: "自由目标点" },
      ...state.scene.objects.map((item) => ({
        value: item.id,
        label: `${item.type === "actor" ? "人物" : "物体"} · ${item.name}`,
      })),
    ],
    camera.look_at_object_id || "",
    (value) => {
      camera.look_at_object_id = value || "";
      runtime.rebuild();
      refreshAll(state);
    },
  ));
  panel.appendChild(button("用当前导演视角更新此关键帧", () => {
    const frame = runtime.getCurrentViewFrame();
    frame.time = camera.keyframes[state.selectedKeyframe]?.time ?? state.time;
    camera.keyframes[state.selectedKeyframe] = frame;
    syncCameraTracks(camera);
    runtime.rebuild();
    refreshAll(state);
  }));
  appendTrackControls(panel, camera.position_track, () => {
    camera.target_track.interpolation = camera.position_track.interpolation;
    if (camera.target_track.interpolation === "bezier") {
      initializeBezierHandles(camera.target_track);
    }
    camera.target_track.speed_mode = camera.position_track.speed_mode;
    camera.target_track.speed_description = camera.position_track.speed_description;
    camera.target_track.speed_curve = structuredClone(camera.position_track.speed_curve);
    syncCameraLegacy(camera);
    runtime.rebuild();
    refreshAll(state);
  }, "摄影机轨迹");
  const activeActions = element("div", "vod-previs__button-row");
  const active = button(camera.id === state.cameraRig.active_camera ? "活动摄影机" : "设为活动", () => {
    state.cameraRig.active_camera = camera.id;
    const firstCut = state.cameraRig.cuts.find((cut) => cut.time < EPSILON);
    if (firstCut) firstCut.camera_id = camera.id;
    refreshAll(state);
  });
  active.disabled = camera.id === state.cameraRig.active_camera;
  activeActions.append(active, button("删除摄影机", () => {
    if (state.cameraRig.cameras.length <= 1) return;
    state.cameraRig.cameras = state.cameraRig.cameras.filter((entry) => entry !== camera);
    const fallback = state.cameraRig.cameras[0];
    if (state.cameraRig.active_camera === camera.id) state.cameraRig.active_camera = fallback.id;
    for (const cut of state.cameraRig.cuts) {
      if (cut.camera_id === camera.id) cut.camera_id = fallback.id;
    }
    state.selectedCameraId = fallback.id;
    state.selectedKeyframe = 0;
    runtime.rebuild();
    refreshAll(state);
  }, false, true));
  panel.appendChild(activeActions);
  panel.appendChild(textElement("div", "vod-previs__section-title", "关键帧"));
  const frameList = element("div", "vod-previs__list");
  camera.keyframes.forEach((frame, index) => {
    frameList.appendChild(hierarchyRow(
      `${index + 1} · ${Math.round(frame.time * 100)}%`,
      `FOV ${Math.round(frame.fov)}°`,
      index === state.selectedKeyframe,
      () => {
        state.selectedKeyframe = index;
        state.time = frame.time;
        runtime.selectCameraFrame(camera, index);
        refreshAll(state);
      },
    ));
  });
  panel.appendChild(frameList);
  state.selectedKeyframe = clamp(state.selectedKeyframe, 0, camera.keyframes.length - 1);
  const frame = camera.keyframes[state.selectedKeyframe];
  panel.append(
    numberInput("时间 0-1", frame.time, (value) => {
      frame.time = uniqueTime(camera.keyframes, frame, clamp(value, 0, 1));
      camera.keyframes.sort((a, b) => a.time - b.time);
      state.selectedKeyframe = camera.keyframes.indexOf(frame);
      state.time = frame.time;
      syncCameraTracks(camera);
      runtime.rebuild();
      refreshAll(state);
    }, 0.01, "change"),
    vecInput("Camera Position", frame.position, () => {
      syncCameraTracks(camera);
      runtime.rebuild();
    }),
    vecInput("Look At", frame.target, () => {
      syncCameraTracks(camera);
      runtime.rebuild();
    }),
    numberInput("FOV", frame.fov, (value) => {
      frame.fov = clamp(value, 15, 100);
      syncCameraTracks(camera);
    }, 1),
    numberInput("Roll", frame.roll, (value) => {
      frame.roll = clamp(value, -180, 180);
    }, 1),
  );
  const frameActions = element("div", "vod-previs__button-row");
  frameActions.append(
    button("当前时间加关键帧", () => {
      const current = cameraAt(camera, state.time);
      current.time = uniqueTime(camera.keyframes, null, state.time);
      camera.keyframes.push(current);
      camera.keyframes.sort((a, b) => a.time - b.time);
      state.selectedKeyframe = camera.keyframes.indexOf(current);
      syncCameraTracks(camera);
      runtime.rebuild();
      refreshAll(state);
    }),
    button("删除关键帧", () => {
      if (camera.keyframes.length <= 1) return;
      camera.keyframes.splice(state.selectedKeyframe, 1);
      state.selectedKeyframe = clamp(state.selectedKeyframe, 0, camera.keyframes.length - 1);
      syncCameraTracks(camera);
      runtime.rebuild();
      refreshAll(state);
    }, false, true),
    button("编辑 Look At", () => runtime.selectCameraFrame(camera, state.selectedKeyframe, true)),
  );
  panel.appendChild(frameActions);
  panel.appendChild(textElement("div", "vod-previs__section-title", "镜头剪辑"));
  const cuts = element("div", "vod-previs__list");
  state.cameraRig.cuts.forEach((cut) => {
    const row = element("div", "vod-previs__button-row");
    const time = element("input", "vod-previs__input");
    time.type = "number";
    time.min = "0";
    time.max = "1";
    time.step = "0.01";
    time.value = String(cut.time);
    time.disabled = cut.time < EPSILON;
    time.addEventListener("change", () => {
      cut.time = clamp(finite(time.value, cut.time), 0, 1);
      state.cameraRig.cuts = normalizeCuts(state.cameraRig.cuts, state.cameraRig.cameras, state.cameraRig.active_camera);
      refreshAll(state);
    });
    const select = element("select", "vod-previs__select");
    for (const optionCamera of state.cameraRig.cameras) {
      const option = document.createElement("option");
      option.value = optionCamera.id;
      option.textContent = optionCamera.name;
      option.selected = optionCamera.id === cut.camera_id;
      select.appendChild(option);
    }
    select.addEventListener("change", () => {
      cut.camera_id = select.value;
      if (cut.time < EPSILON) state.cameraRig.active_camera = cut.camera_id;
      refreshAll(state);
    });
    const remove = button("×", () => {
      state.cameraRig.cuts = state.cameraRig.cuts.filter((entry) => entry !== cut);
      refreshAll(state);
    }, false, true);
    remove.disabled = cut.time < EPSILON;
    row.append(time, select, remove);
    cuts.appendChild(row);
  });
  panel.appendChild(cuts);
  panel.appendChild(button("当前时间切到此摄影机", () => {
    const existing = state.cameraRig.cuts.find((cut) => Math.abs(cut.time - state.time) < EPSILON);
    if (existing) existing.camera_id = camera.id;
    else state.cameraRig.cuts.push({ time: state.time, camera_id: camera.id });
    if (state.time < EPSILON) state.cameraRig.active_camera = camera.id;
    state.cameraRig.cuts = normalizeCuts(state.cameraRig.cuts, state.cameraRig.cameras, state.cameraRig.active_camera);
    refreshAll(state);
  }));
}

function appendTrackControls(panel, track, onChange, label = "运动轨迹") {
  panel.appendChild(textElement("div", "vod-previs__section-title", label));
  panel.appendChild(selectInput("插值", TRACK_INTERPOLATIONS, track.interpolation, (value) => {
    track.interpolation = value;
    if (value === "bezier") initializeBezierHandles(track);
    onChange();
  }));
  panel.appendChild(selectInput("速度", SPEED_MODES, track.speed_mode, (value) => {
    track.speed_mode = value;
    onChange();
  }));
  panel.appendChild(textInput("速度描述", track.speed_description, (value) => {
    track.speed_description = value;
    onChange();
  }));
  if (track.speed_mode === "custom") {
    const field = element("label", "vod-previs__field");
    field.appendChild(textElement("span", "", "自定义速度曲线 JSON"));
    const textarea = element("textarea", "vod-previs__textarea");
    textarea.value = JSON.stringify(track.speed_curve);
    textarea.addEventListener("change", () => {
      try {
        const parsed = JSON.parse(textarea.value);
        if (!Array.isArray(parsed)) throw new Error("Speed curve must be an array");
        track.speed_curve = normalizeSpeedCurve(parsed);
        textarea.value = JSON.stringify(track.speed_curve);
        onChange();
      } catch (error) {
        track.speed_curve = normalizeSpeedCurve(DEFAULT_TRACK.speed_curve);
        textarea.value = JSON.stringify(track.speed_curve);
        onChange();
        textarea.setCustomValidity(`无效 JSON，已恢复默认曲线：${error.message}`);
        textarea.reportValidity();
      }
    });
    textarea.addEventListener("input", () => textarea.setCustomValidity(""));
    field.appendChild(textarea);
    panel.appendChild(field);
  }
}

function initializeBezierHandles(track) {
  for (let index = 0; index < track.points.length - 1; index += 1) {
    const start = track.points[index];
    const end = track.points[index + 1];
    if (!start.out_handle) start.out_handle = lerp3(start.position, end.position, 1 / 3);
    if (!end.in_handle) end.in_handle = lerp3(start.position, end.position, 2 / 3);
  }
}

async function loadBackgroundAsset(path, runtime, viewport, state) {
  viewport.querySelectorAll(".vod-previs__asset-notice").forEach((notice) => notice.remove());
  runtime.clearAssets();
  if (!path?.trim()) return;
  const cleanPath = path;
  const extension = cleanPath.includes(".") ? cleanPath.split(".").pop().toLowerCase() : "";
  if (!["glb", "gltf", "obj", "ply", "spz"].includes(extension)) {
    throw new Error(`不支持的背景格式：${extension || "未知"}`);
  }
  const assetUrl = `/tencent-vod-aigc/asset?path=${encodeURIComponent(path)}`;
  const notice = textElement("div", "vod-previs__hint vod-previs__asset-notice", "正在加载背景资产…");
  viewport.appendChild(notice);
  try {
    if (extension === "spz") {
      const response = await fetch(assetUrl);
      if (!response.ok) {
        const detail = await response.text();
        throw new Error(detail || `HTTP ${response.status}`);
      }
      const bytes = await response.arrayBuffer();
      notice.textContent = "正在解码 SPZ 并创建主视窗与观察窗渲染资源…";
      await runtime.addSplatBytes(
        bytes,
        cleanPath.split(/[\\/]/).pop(),
        (event) => {
          if (!event?.total) return;
          const progress = Math.round((event.loaded / event.total) * 100);
          notice.textContent = `正在加载 SPZ 场景… ${progress}%`;
        },
      );
    } else if (extension === "glb" || extension === "gltf") {
      const gltf = await new GLTFLoader().loadAsync(assetUrl);
      runtime.addAsset(gltf.scene);
    } else if (extension === "obj") {
      runtime.addAsset(await new OBJLoader().loadAsync(assetUrl));
    } else {
      const geometry = await new PLYLoader().loadAsync(assetUrl);
      geometry.computeVertexNormals();
      const themeColor = getComputedStyle(document.documentElement)
        .getPropertyValue("--cp-border-strong")
        .trim();
      const material = new THREE.MeshStandardMaterial({ color: themeColor, roughness: 0.8 });
      runtime.addAsset(new THREE.Mesh(geometry, material));
    }
    state.background.path = path;
    runtime.setBackgroundTransform(state.background.transform);
    notice.remove();
  } catch (error) {
    notice.textContent = `背景资产无法由浏览器加载：${error?.message || "路径不可访问"}`;
    throw error;
  }
}

async function exportPrevisFrames(runtime, state, width, height, onProgress) {
  const created = await responseJson(await fetch("/tencent-vod-aigc/previs/renders", {
    method: "POST",
    headers: { ...PREVIS_REQUEST_HEADER, "Content-Type": "application/json" },
    body: JSON.stringify({
      frame_count: state.frameCount,
      width,
      height,
      fps: state.fps,
      scene: state.scene,
      camera: state.cameraRig,
      background: state.background,
    }),
  }));
  let complete = false;
  try {
    for (let index = 0; index < state.frameCount; index += 1) {
      if (state.disposed) throw new Error("预演编辑器已关闭");
      const time = state.frameCount <= 1 ? 0 : index / (state.frameCount - 1);
      onProgress(`渲染并上传 ${index + 1} / ${state.frameCount} 帧…`);
      const frame = await runtime.captureFrame(time, width, height);
      await responseJson(await fetch(
        `/tencent-vod-aigc/previs/renders/${created.render_id}/frames/${index}`,
        {
          method: "PUT",
          headers: { ...PREVIS_REQUEST_HEADER, "Content-Type": "image/png" },
          body: frame,
        },
      ));
    }
    onProgress("正在完成预演渲染缓存…");
    const completed = await responseJson(await fetch(
      `/tencent-vod-aigc/previs/renders/${created.render_id}/complete`,
      { method: "POST", headers: PREVIS_REQUEST_HEADER },
    ));
    complete = true;
    runtime.render(state.time);
    return completed.manifest_path;
  } finally {
    if (!complete) {
      await fetch(`/tencent-vod-aigc/previs/renders/${created.render_id}`, {
        method: "DELETE",
        headers: PREVIS_REQUEST_HEADER,
      }).catch((error) => console.warn("Failed to remove incomplete previs render", error));
    }
  }
}

function refreshAll(state) {
  state.refreshTabs?.();
  state.refreshInspector?.();
  state.refreshTimeline?.();
}

function resizeRenderer(renderer, camera, host) {
  const width = Math.max(1, host.clientWidth);
  const height = Math.max(1, host.clientHeight);
  const canvas = renderer.domElement;
  if (canvas.width !== Math.round(width * renderer.getPixelRatio())
    || canvas.height !== Math.round(height * renderer.getPixelRatio())) {
    renderer.setSize(width, height, false);
    camera.aspect = width / height;
    camera.updateProjectionMatrix();
  }
}

function disposeChildren(root, disposeObject = false) {
  for (const child of [...root.children]) {
    child.traverse((object) => {
      object.geometry?.dispose?.();
      if (Array.isArray(object.material)) object.material.forEach((material) => disposeMaterial(material));
      else disposeMaterial(object.material);
    });
    if (disposeObject && typeof child.dispose === "function") child.dispose();
    root.remove(child);
  }
}

function disposeMaterial(material) {
  if (!material) return;
  for (const value of Object.values(material)) {
    if (value?.isTexture) value.dispose();
  }
  material.dispose?.();
}

function upsertTrackPoint(track, time, position) {
  const existing = track.points.find((point) => Math.abs(point.time - time) < EPSILON);
  if (existing) {
    existing.position = [...position];
    return existing;
  }
  const point = { time: clamp(time, 0, 1), position: [...position] };
  track.points.push(point);
  track.points.sort((a, b) => a.time - b.time);
  return point;
}

function uniqueTime(points, current, requested) {
  if (!points.some((point) => point !== current && Math.abs(point.time - requested) < EPSILON)) {
    return requested;
  }
  for (let offset = 1; offset <= 1000; offset += 1) {
    const candidate = clamp(requested + offset / 1000, 0, 1);
    if (!points.some((point) => point !== current && Math.abs(point.time - candidate) < EPSILON)) {
      return candidate;
    }
  }
  return current?.time ?? requested;
}

function getSelectedCamera(state) {
  return state.cameraRig.cameras.find((camera) => camera.id === state.selectedCameraId)
    || state.cameraRig.cameras[0];
}

function uniqueId(items, prefix) {
  const ids = new Set(items.map((item) => item.id));
  let index = items.length + 1;
  while (ids.has(`${prefix}-${index}`)) index += 1;
  return `${prefix}-${index}`;
}

function hierarchyRow(label, meta, active, handler) {
  const row = element("button", "vod-previs__item");
  row.type = "button";
  row.dataset.active = String(active);
  row.append(textElement("span", "", label), textElement("span", "vod-previs__item-meta", meta));
  row.addEventListener("click", handler);
  return row;
}

function element(tag, className = "") {
  const result = document.createElement(tag);
  if (className) result.className = className;
  return result;
}

function textElement(tag, className, text) {
  const result = element(tag, className);
  result.textContent = text;
  return result;
}

function button(label, handler, primary = false, danger = false) {
  const result = element("button", "vod-previs__button");
  if (primary) result.classList.add("vod-previs__button--primary");
  if (danger) result.classList.add("vod-previs__button--danger");
  result.type = "button";
  result.textContent = label;
  result.addEventListener("click", handler);
  return result;
}

function textInput(label, value, onChange) {
  const field = element("label", "vod-previs__field");
  field.appendChild(textElement("span", "", label));
  const input = element("input", "vod-previs__input");
  input.value = value ?? "";
  input.addEventListener("change", () => onChange(input.value));
  field.appendChild(input);
  return field;
}

function numberInput(label, value, onChange, step = 0.1, eventName = "input") {
  const field = element("label", "vod-previs__field");
  field.appendChild(textElement("span", "", label));
  const input = element("input", "vod-previs__input");
  input.type = "number";
  input.step = String(step);
  input.value = String(finite(value));
  input.addEventListener(eventName, () => onChange(finite(input.value)));
  field.appendChild(input);
  return field;
}

function vecInput(label, value, onChange, min = null) {
  const field = element("label", "vod-previs__field");
  field.appendChild(textElement("span", "", label));
  const row = element("div", "vod-previs__vec");
  for (let index = 0; index < 3; index += 1) {
    const input = element("input", "vod-previs__input");
    input.type = "number";
    input.step = "0.1";
    if (min != null) input.min = String(min);
    input.value = String(finite(value[index]));
    input.addEventListener("input", () => {
      value[index] = min == null ? finite(input.value) : Math.max(min, finite(input.value, min));
      onChange(value);
    });
    row.appendChild(input);
  }
  field.appendChild(row);
  return field;
}

function selectInput(label, options, selected, onChange) {
  const field = element("label", "vod-previs__field");
  field.appendChild(textElement("span", "", label));
  const select = element("select", "vod-previs__select");
  for (const value of options) {
    const option = document.createElement("option");
    option.value = value;
    option.textContent = value;
    option.selected = value === selected;
    select.appendChild(option);
  }
  select.addEventListener("change", () => onChange(select.value));
  field.appendChild(select);
  return field;
}

function optionSelectInput(label, options, selected, onChange) {
  const field = element("label", "vod-previs__field");
  field.appendChild(textElement("span", "", label));
  const select = element("select", "vod-previs__select");
  for (const item of options) {
    const option = document.createElement("option");
    option.value = item.value;
    option.textContent = item.label;
    option.selected = item.value === selected;
    select.appendChild(option);
  }
  select.addEventListener("change", () => onChange(select.value));
  field.appendChild(select);
  return field;
}

function setWidgetValue(node, target, value) {
  if (!target) return;
  target.value = value;
  target.callback?.(value);
  node.graph?.setDirtyCanvas?.(true, true);
}

function isTypingTarget(target) {
  return target instanceof HTMLInputElement
    || target instanceof HTMLTextAreaElement
    || target instanceof HTMLSelectElement;
}

function lerp3(a, b, t) {
  return [0, 1, 2].map((index) => a[index] + (b[index] - a[index]) * t);
}

function cubicBezier(p0, p1, p2, p3, t) {
  const u = 1 - t;
  return [0, 1, 2].map((index) =>
    u ** 3 * p0[index]
    + 3 * u * u * t * p1[index]
    + 3 * u * t * t * p2[index]
    + t ** 3 * p3[index]);
}

function catmullRom(p0, p1, p2, p3, t) {
  const t2 = t * t;
  const t3 = t2 * t;
  return [0, 1, 2].map((index) => 0.5 * (
    2 * p1[index]
    + (-p0[index] + p2[index]) * t
    + (2 * p0[index] - 5 * p1[index] + 4 * p2[index] - p3[index]) * t2
    + (-p0[index] + 3 * p1[index] - 3 * p2[index] + p3[index]) * t3
  ));
}

function sub3(a, b) {
  return [a[0] - b[0], a[1] - b[1], a[2] - b[2]];
}

function normalize3(value) {
  const length = Math.hypot(...value);
  return length < EPSILON ? [0, 0, 0] : value.map((component) => component / length);
}

function distance3(a, b) {
  return Math.hypot(a[0] - b[0], a[1] - b[1], a[2] - b[2]);
}
