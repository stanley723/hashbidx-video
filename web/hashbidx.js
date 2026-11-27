// HashBidX front-end helpers for ComfyUI.
// 1. Model-linked options: after a model is picked, the resolution, duration and audio settings only offer
//    what that model has, so a run cannot be started with a setting the model would refuse.
// 2. The api_key field shows dots instead of the key.
// If anything here fails, the boxes still work as plain ComfyUI boxes.
import { app } from "../../scripts/app.js";

// Keep in step with MODELS in nodes.py.
const MODELS = {
  "dreamina-seedance-2-5":      { durations: [4, 30], resolutions: ["720p", "480p", "1080p"], audioOff: true },
  "dreamina-seedance-2-0":      { durations: [4, 15], resolutions: ["720p", "480p", "1080p", "4k"], audioOff: true },
  "dreamina-seedance-2-0-fast": { durations: [4, 15], resolutions: ["720p", "480p"], audioOff: true },
  "dreamina-seedance-2-0-mini": { durations: [4, 15], resolutions: ["480p", "720p"], audioOff: true },
  "MiniMax-H3":                 { durations: [4, 15], resolutions: ["768p", "2k"], audioOff: false },
  "wan3.0-video":               { durations: [2, 30], resolutions: ["720p", "480p", "1080p"], audioOff: true },
  "wan3.0-video-prime":         { durations: [2, 30], resolutions: ["720p", "480p", "1080p"], audioOff: true },
};
const LINKED = { HashBidXVideo: "default", HashBidXExtendEdit: "same as source" };
const AUDIO = ["model default", "on", "off"];

function widget(node, name) {
  return (node.widgets || []).find((w) => w.name === name);
}

function setValues(w, values, fallback) {
  if (!w) return;
  w.options = w.options || {};
  w.options.values = values;
  if (!values.includes(w.value)) {
    w.value = fallback;
    if (typeof w.callback === "function") w.callback(w.value);
  }
}

function applyModel(node, firstChoice) {
  const spec = MODELS[widget(node, "model")?.value];
  if (!spec) return;
  setValues(widget(node, "resolution"), [firstChoice, ...spec.resolutions], spec.resolutions[0]);
  setValues(widget(node, "audio"), spec.audioOff ? AUDIO : AUDIO.slice(0, 2), "model default");
  const d = widget(node, "duration");
  if (d) {
    d.options = d.options || {};
    d.options.max = spec.durations[1];
    const fixed = fitDuration(d.value, d.value, spec);
    if (fixed !== d.value) d.value = fixed;
  }
  node.setDirtyCanvas?.(true, true);
}

// 0 means "model default", so it stays allowed. Anything else is kept inside the model's range.
function fitDuration(value, before, spec) {
  const [lo, hi] = spec.durations;
  let v = Number(value);
  if (!Number.isFinite(v)) return before;
  if (v > hi) return hi;
  if (v > 0 && v < lo) return before === lo && v === lo - 1 ? 0 : lo;
  return v;
}

function linkModel(node, firstChoice) {
  const m = widget(node, "model");
  if (!m || m.__hashbidx) return;
  m.__hashbidx = true;
  const onModel = m.callback;
  m.callback = function (...args) {
    const r = onModel ? onModel.apply(this, args) : undefined;
    try { applyModel(node, firstChoice); } catch (e) { console.warn("[HashBidX]", e); }
    return r;
  };
  const d = widget(node, "duration");
  if (d) {
    let before = d.value;
    const onDuration = d.callback;
    d.callback = function (...args) {
      try {
        const spec = MODELS[widget(node, "model")?.value];
        if (spec) {
          const fixed = fitDuration(d.value, before, spec);
          if (fixed !== d.value) { d.value = fixed; args[0] = fixed; }
        }
        before = d.value;
      } catch (e) { console.warn("[HashBidX]", e); }
      return onDuration ? onDuration.apply(this, args) : undefined;
    };
  }
  // A saved canvas sets its values after the box is created, so apply again once they are in.
  const onConfigure = node.onConfigure;
  node.onConfigure = function (...args) {
    const r = onConfigure ? onConfigure.apply(this, args) : undefined;
    try { applyModel(node, firstChoice); } catch (e) { console.warn("[HashBidX]", e); }
    return r;
  };
  applyModel(node, firstChoice);
}

// The api_key field becomes an input that shows dots. The value, its place in the box and how it is saved
// stay the same, so existing canvases load unchanged.
function maskKey(node) {
  const at = (node.widgets || []).findIndex((w) => w.name === "api_key");
  if (at < 0 || typeof node.addDOMWidget !== "function") return;
  const old = node.widgets[at];
  if (old.__hashbidx) return;

  const input = document.createElement("input");
  const dots = typeof CSS !== "undefined" && CSS.supports && CSS.supports("-webkit-text-security", "disc");
  input.type = dots ? "text" : "password";
  input.autocomplete = dots ? "off" : "new-password";
  input.spellcheck = false;
  input.setAttribute("autocapitalize", "off");
  input.setAttribute("autocorrect", "off");
  input.setAttribute("data-1p-ignore", "");
  input.setAttribute("data-lpignore", "true");
  input.setAttribute("aria-label", "api_key");
  input.placeholder = "paste your key here";
  input.value = old.value == null ? "" : String(old.value);
  Object.assign(input.style, {
    flex: "1 1 auto", minWidth: "0", height: "100%", boxSizing: "border-box", padding: "0", margin: "0",
    border: "none", outline: "none", background: "transparent", color: "var(--input-text, #ddd)",
    fontSize: "12px", fontFamily: "inherit", textAlign: "right",
  });
  if (dots) input.style.setProperty("-webkit-text-security", "disc");

  // Same look as the other fields in the box: the name on the left, the value on the right.
  const label = document.createElement("span");
  label.textContent = "api_key";
  Object.assign(label.style, { flex: "0 0 auto", marginRight: "10px", opacity: "0.7", fontSize: "12px" });
  const pill = document.createElement("div");
  Object.assign(pill.style, {
    display: "flex", alignItems: "center", height: "22px", boxSizing: "border-box", padding: "0 14px",
    borderRadius: "11px", border: "1px solid var(--border-color, #4e4e4e)",
    background: "var(--comfy-input-bg, #222)", color: "var(--input-text, #ddd)", cursor: "text",
  });
  pill.append(label, input);
  pill.addEventListener("click", () => input.focus());
  const holder = document.createElement("div");
  Object.assign(holder.style, { boxSizing: "border-box", padding: "2px 15px", width: "100%", height: "100%" });
  holder.appendChild(pill);

  const w = node.addDOMWidget("api_key", "hashbidx_key", holder, {
    getValue: () => input.value,
    setValue: (v) => { input.value = v == null ? "" : String(v); },
    getMinHeight: () => 26,
    getMaxHeight: () => 26,
    hideOnZoom: false,
    margin: 0,
  });
  w.__hashbidx = true;
  w.computeSize = (width) => [width, 26];
  w.tooltip = old.tooltip;
  // Pasting into the field must only fill the field (the canvas also listens for paste, to paste boxes).
  input.addEventListener("paste", (e) => e.stopPropagation());
  input.addEventListener("input", () => {
    if (typeof w.callback === "function") w.callback(input.value);
    node.graph?.setDirtyCanvas?.(true, true);
  });

  // Put the new field where the old one was and take the old one out.
  const list = node.widgets;
  const added = list.indexOf(w);
  if (added >= 0 && added !== at) {
    list.splice(added, 1);
    list.splice(at, 1, w);
    old.onRemove?.();
  }
  const size = node.computeSize?.();
  if (size && node.size) node.setSize?.([Math.max(node.size[0], size[0]), Math.max(node.size[1], size[1])]);
}

app.registerExtension({
  name: "HashBidX.Video",
  nodeCreated(node) {
    try {
      const cls = node.comfyClass || node.constructor?.comfyClass;
      if (cls in LINKED) linkModel(node, LINKED[cls]);
      if (cls === "HashBidXAPIKey") maskKey(node);
    } catch (e) {
      console.warn("[HashBidX] the box works, without the extra helpers:", e);
    }
  },
});
