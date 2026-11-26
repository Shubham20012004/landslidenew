// Shared helpers: API access, formatting, DOM.
export const RISKS = ["SAFE", "WATCH", "WARNING", "CRITICAL"];
export const RISK_COLOR = { SAFE: "#1e7a46", WATCH: "#1d5fd1", WARNING: "#c98d00", CRITICAL: "#c42b2b" };
export const STATUS_TEXT = {
  OK: "Prediction available",
  CALIBRATING: "Calibrating",
  SENSOR_FAULT: "Sensor fault",
  MODEL_NOT_READY: "Model not ready",
  INSUFFICIENT_DATA: "Insufficient data",
  INFERENCE_ERROR: "Inference error",
  NOT_PROCESSED: "Not processed",
};

export class ApiError extends Error {
  constructor(status, message) { super(message); this.status = status; }
}

export async function api(path, { method = "GET", body, admin } = {}) {
  const headers = { Accept: "application/json" };
  if (body !== undefined) headers["Content-Type"] = "application/json";
  if (admin) headers["X-Admin-Token"] = admin;
  let res;
  try {
    res = await fetch(path, { method, headers, body: body !== undefined ? JSON.stringify(body) : undefined, cache: "no-store" });
  } catch {
    throw new ApiError(0, "Backend unreachable");
  }
  let data = null;
  try { data = await res.json(); } catch { /* non-JSON */ }
  if (!res.ok) {
    const err = data?.error;
    const detail = Array.isArray(err?.details) && err.details.length ? `: ${err.details[0].loc} ${err.details[0].msg}` : "";
    throw new ApiError(res.status, (err?.message || `HTTP ${res.status}`) + detail);
  }
  return data;
}

export const $ = (id) => document.getElementById(id);

export function esc(v) {
  return String(v ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

export function isNum(v) { return v !== null && v !== undefined && Number.isFinite(Number(v)); }

export function num(v, digits = 2, unit = "") {
  return isNum(v) ? Number(v).toFixed(digits) + unit : "—";
}

export function pct(fraction, digits = 1) {
  return isNum(fraction) ? (Number(fraction) * 100).toFixed(digits) + " %" : "—";
}

export function duration(seconds) {
  if (!isNum(seconds)) return "—";
  const s = Math.round(Number(seconds));
  if (s < 60) return `${s} s`;
  if (s < 3600) return `${Math.floor(s / 60)} min ${s % 60} s`;
  return `${Math.floor(s / 3600)} h ${Math.floor((s % 3600) / 60)} min`;
}

export function ago(seconds) {
  if (!isNum(seconds)) return "—";
  const s = Math.max(0, Math.round(seconds));
  if (s < 2) return "just now";
  if (s < 90) return `${s} s ago`;
  if (s < 5400) return `${Math.round(s / 60)} min ago`;
  if (s < 172800) return `${Math.round(s / 3600)} h ago`;
  return `${Math.round(s / 86400)} d ago`;
}

export function timeOf(ts, withDate = false) {
  if (!isNum(ts)) return "—";
  const d = new Date(Number(ts) * 1000);
  return withDate
    ? d.toLocaleString([], { year: "numeric", month: "short", day: "2-digit", hour: "2-digit", minute: "2-digit", second: "2-digit" })
    : d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" });
}

// datetime-local <input> value <-> epoch seconds (local time zone)
export function toLocalInput(ts) {
  const d = new Date(ts * 1000);
  const p = (n) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}T${p(d.getHours())}:${p(d.getMinutes())}:${p(d.getSeconds())}`;
}
export function fromLocalInput(value) {
  if (!value) return null;
  const t = new Date(value).getTime();
  return Number.isFinite(t) ? t / 1000 : null;
}

export function riskPill(state) {
  return RISKS.includes(state) ? `<span class="pill ${state}">${state}</span>` : `<span class="pill NA">${esc(STATUS_TEXT[state] || state || "—")}</span>`;
}

export function setText(id, text) { const el = $(id); if (el) el.textContent = text; }

export function options(select, items, { value, empty } = {}) {
  const prev = value ?? select.value;
  select.innerHTML = (empty ? `<option value="">${esc(empty)}</option>` : "") +
    items.map((it) => `<option value="${esc(it.value)}">${esc(it.label)}</option>`).join("");
  if ([...select.options].some((o) => o.value === prev)) select.value = prev;
}
