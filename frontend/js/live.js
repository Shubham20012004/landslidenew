// Live monitoring view.
import { fillSensorCharts, sensorCharts } from "./charts.js";
import { state, serverNow, deviceOnline, freshFor, offlineAfter } from "./state.js";
import { $, ago, api, duration, esc, num, pct, RISKS, RISK_COLOR, setText, STATUS_TEXT, timeOf } from "./util.js";

let charts = null;
let rangeS = 900;
let loadedFor = null;
let chartReq = 0;

export function initLive() {
  charts = sensorCharts(["chSoil", "chWater", "chTilt", "chVib"]);
  for (const c of Object.values(charts)) c.setWindow(rangeS * 1000);
  $("rangeSeg").addEventListener("click", (e) => {
    const b = e.target.closest("button[data-range]");
    if (!b) return;
    rangeS = Number(b.dataset.range);
    for (const x of $("rangeSeg").querySelectorAll("button")) x.classList.toggle("on", x === b);
    for (const c of Object.values(charts)) c.setWindow(rangeS * 1000);
    loadCharts();
  });
  $("deviceSelect").addEventListener("change", (e) => {
    state.selected = e.target.value;
    loadCharts();
    renderLive();
  });
}

export async function loadCharts() {
  const dev = state.selected;
  if (!dev || !charts) return;
  const req = ++chartReq;
  const start = serverNow() - rangeS;
  try {
    const s = await api(`/api/readings/series?device_id=${encodeURIComponent(dev)}&start=${start.toFixed(3)}&max_points=600`);
    if (req !== chartReq) return;   // a newer request superseded this one
    fillSensorCharts(charts, s);
    loadedFor = dev;
    setText("chartNote", s.bucket_s
      ? `Averaged into ${s.bucket_s}-second buckets. Gaps mark periods without valid measurements; nothing is interpolated.`
      : "Gaps mark periods without valid measurements; nothing is interpolated.");
  } catch (err) {
    setText("chartNote", `Chart data unavailable: ${err.message}`);
  }
}

export function onLiveReading(r) {
  if (!charts || r.device_id !== state.selected || loadedFor !== r.device_id) return;
  const f = r.features;
  charts.soil.push(r.received_ts, f.soil_moisture === null ? NaN : f.soil_moisture * 100);
  charts.water.push(r.received_ts, f.water_wetness === null ? NaN : f.water_wetness * 100);
  charts.tilt.push(r.received_ts, f.tilt_deg ?? NaN);
  charts.vib.push(r.received_ts, f.vib_events_per_s ?? NaN);
}

export function tickCharts() { if (charts) for (const c of Object.values(charts)) c.tick(); }

function setStatus(id, text, cls = "") {
  const el = $(id);
  el.textContent = text;
  el.className = "v " + cls;
}

function renderStatusbar(r, dev) {
  const online = dev && deviceOnline(dev.device_id);
  setStatus("sbDevice", !dev ? "No device yet" : `${online ? "Online" : "Offline"}${dev.origin === "simulated" ? " · simulated" : ""}`,
    !dev ? "" : online ? "good" : "bad");
  setStatus("sbBackend", state.stream === "live" ? "Connected" : state.stream === "connecting" ? "Connecting…" : "Disconnected",
    state.stream === "live" ? "good" : "bad");
  setStatus("sbSession", r?.session_id ?? "—");
  const age = r ? serverNow() - r.received_ts : null;
  setStatus("sbUpdate", r ? ago(age) : "—", r && age > offlineAfter() ? "bad" : "");
  const cal = r?.cal?.status ?? (r ? r.calibration_status : null);
  setStatus("sbCal", cal === "OK" ? "Calibrated" : cal === "IN_PROGRESS" ? "In progress" : cal === "FAILED" ? `Failed${r?.cal?.error ? " · " + r.cal.error : ""}` : "—",
    cal === "OK" ? "good" : cal === "FAILED" ? "bad" : cal === "IN_PROGRESS" ? "warn" : "");
  const m = state.model;
  setStatus("sbModel", !m ? "—" : m.ready ? (m.demo_model ? "Ready · demo" : "Ready") : "Not ready", m?.ready ? (m.demo_model ? "warn" : "good") : "bad");
}

function renderRisk(r) {
  const panel = $("riskPanel");
  const p = r?.prediction;
  const age = r ? serverNow() - r.received_ts : Infinity;
  const stale = !!r && (age > freshFor() || state.stream !== "live");
  const ok = p?.available && p.risk_state;
  panel.className = "panel risk" + (ok ? ` r-${p.risk_state}` : "") + (stale ? " stale" : "");
  const big = $("riskState");
  if (!r) {
    big.textContent = "No data";
    big.classList.add("small");
    setText("riskReason", "Waiting for telemetry from a device.");
  } else if (ok) {
    big.textContent = p.risk_state;
    big.classList.remove("small");
    setText("riskReason", stale ? `STALE — this prediction is ${Math.round(age)} s old and is not current.` : "");
  } else {
    big.textContent = STATUS_TEXT[p?.status] || p?.status || "No prediction";
    big.classList.add("small");
    setText("riskReason", (stale ? `STALE (${Math.round(age)} s old). ` : "") + (p?.reason || ""));
  }
  setText("riskConf", ok ? `${(p.confidence * 100).toFixed(1)} %` : "—");
  setText("riskTime", p?.predicted_ts ? `${timeOf(p.predicted_ts)} (${ago(serverNow() - p.predicted_ts)})` : "—");
  setText("riskModel", p?.model_version ?? state.model?.active_version ?? "—");
  setText("riskQuality", r ? (p?.data_quality === "DEGRADED" ? "Degraded (suspect sensor)" : p?.data_quality === "OK" ? "OK" : "—") : "—");
  $("demoBadge").classList.toggle("hidden", !(p?.demo_model || (!p && state.model?.demo_model)));
  const probs = $("riskProbs");
  if (ok && p.probabilities) {
    probs.innerHTML = RISKS.map((c) => {
      const v = p.probabilities[c] ?? 0;
      return `<div class="prob"><span>${c}</span><div class="bar" role="img" aria-label="${c} ${(v * 100).toFixed(1)} percent">
        <div class="fill" style="width:${(v * 100).toFixed(1)}%;background:${RISK_COLOR[c]}"></div></div><span class="pv">${(v * 100).toFixed(1)}%</span></div>`;
    }).join("");
  } else {
    probs.innerHTML = "";
  }
}

function chip(id, health) {
  const el = $(id);
  el.textContent = health ? health.replace("_", " ") : "—";
  el.className = "chip " + (health || "");
}

function flags(list) { return list && list.length ? list.join(", ").toLowerCase().replaceAll("_", " ") : "none"; }

function trend(rate, unit, digits) {
  if (rate === null || rate === undefined) return "warming up";
  const arrow = Math.abs(rate) < 1e-4 ? "→" : rate > 0 ? "↑" : "↓";
  return `${arrow} ${num(rate, digits)} ${unit}`;
}

function renderCards(r) {
  const s = r?.sensors || {};
  const soil = s.soil || {}, water = s.water || {}, mpu = s.mpu || {}, vib = s.vib || {};
  chip("soilHealth", soil.health);
  setText("soilPct", pct(soil.moisture));
  setText("soilRaw", soil.raw ?? "—");
  setText("soilTrend", r ? trend(soil.rate_per_min === null || soil.rate_per_min === undefined ? null : soil.rate_per_min * 100, "%/min", 2) : "—");
  setText("soilFlags", r ? flags(soil.flags) : "—");
  chip("waterHealth", water.health);
  setText("waterPct", pct(water.wetness));
  setText("waterRaw", water.raw ?? "—");
  setText("waterDur", r ? (water.wet_s === null ? "unknown" : duration(water.wet_s)) : "—");
  setText("waterFlags", r ? flags(water.flags) : "—");
  chip("mpuHealth", mpu.health);
  setText("mpuTilt", num(mpu.tilt_deg, 2, "°"));
  setText("mpuIncl0", num(mpu.incl0_deg, 2, "°"));
  setText("mpuIncl", num(mpu.incl_deg, 2, "°"));
  setText("mpuRate", r ? (mpu.tilt_rate_dpm === null ? "warming up" : num(mpu.tilt_rate_dpm, 2, " °/min")) : "—");
  chip("vibHealth", vib.health);
  setText("vibEps", r ? num(vib.eps, 2, " ev/s") : "—");
  setText("vibBg", num(vib.bg_eps, 3, " ev/s"));
  setText("vib30", r ? (vib.ev_30s === null ? "warming up" : `${vib.ev_30s} events`) : "—");
  setText("vibTrend", r ? (vib.trend_eps === null ? "warming up" : trend(vib.trend_eps, "ev/s", 2)) : "—");
}

function renderBanner(r, dev) {
  const b = $("liveBanner");
  let msg = "", cls = "warn";
  if (state.stream !== "live") { msg = "Connection to the backend lost — values below are not live. Reconnecting automatically…"; cls = "bad"; }
  else if (dev && !deviceOnline(dev.device_id)) { msg = `No telemetry from ${dev.device_id} for ${duration(serverNow() - dev.last_seen_ts)}. Showing the last received data; it is not current.`; cls = "bad"; }
  else if (r?.origin === "simulated") { msg = "This device is a SIMULATOR (scripts/simulate_device.py). Its data is excluded from experimental training."; cls = "info"; }
  else if (r?.prediction?.demo_model) { msg = "Demonstration mode: the active model was trained on synthetic data. Predictions demonstrate the software only."; cls = "info"; }
  b.textContent = msg;
  b.className = "banner " + cls + (msg ? "" : " hidden");
}

function renderEvents() {
  const list = $("liveEvents");
  const evs = state.events.filter((e) => !state.selected || !e.device_id || e.device_id === state.selected).slice(0, 25);
  list.innerHTML = evs.length ? evs.map((e) => `<li><time datetime="${esc(e.created_at)}">${timeOf(e.created_ts)}</time>
      <span><span class="sev sev-${esc(e.severity)}" aria-hidden="true"></span>${esc(e.message)}</span></li>`).join("")
    : `<li class="empty">No events recorded yet.</li>`;
}

export function renderLive() {
  const sel = $("deviceSelect");
  const devs = [...state.devices.values()].sort((a, b) => a.device_id.localeCompare(b.device_id));
  const key = devs.map((d) => d.device_id).join("|");
  if (sel.dataset.key !== key) {   // rebuild only when the device list changes (keeps an open dropdown usable)
    sel.dataset.key = key;
    sel.innerHTML = devs.length ? devs.map((d) => `<option value="${esc(d.device_id)}">${esc(d.device_id)}${d.origin === "simulated" ? " (simulated)" : ""}</option>`).join("")
      : `<option value="">No devices yet</option>`;
  }
  if (sel.value !== (state.selected ?? "")) sel.value = state.selected ?? "";
  const dev = state.devices.get(state.selected);
  const r = state.latest.get(state.selected);
  renderStatusbar(r, dev);
  renderBanner(r, dev);
  renderRisk(r);
  renderCards(r);
  renderEvents();
}
