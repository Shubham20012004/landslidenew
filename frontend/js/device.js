// Device & system status view.
import { state, serverNow, deviceOnline } from "./state.js";
import { $, ago, api, duration, esc, num, setText, timeOf, displayModelText } from "./util.js";

function kv(rows) {
  return `<dl class="kv">${rows.map(([k, v]) => `<dt>${esc(k)}</dt><dd>${v}</dd>`).join("")}</dl>`;
}
const chip = (h) => `<span class="chip ${esc(h || "")}">${esc(h ? h.replace("_", " ") : "—")}</span>`;
const list = (xs) => (xs && xs.length ? xs.map((x) => esc(x.toLowerCase().replaceAll("_", " "))).join(", ") : "none");

function renderDevices() {
  const tbody = $("dTable").querySelector("tbody");
  const devs = [...state.devices.values()].sort((a, b) => b.last_seen_ts - a.last_seen_ts);
  tbody.innerHTML = devs.length ? devs.map((d) => {
    const online = deviceOnline(d.device_id);
    return `<tr class="${d.device_id === state.selected ? "sel" : ""}"><td><button class="btn" type="button" data-dev="${esc(d.device_id)}">${esc(d.device_id)}</button></td>
      <td>${d.origin === "simulated" ? `<span class="badge sim">simulated</span>` : "ESP32"}</td>
      <td>${online ? `<span class="pill SAFE">Online</span>` : `<span class="pill NA">Offline</span>`}</td>
      <td>${timeOf(d.last_seen_ts, true)} (${ago(serverNow() - d.last_seen_ts)})</td>
      <td class="mono">${esc(d.fw_version ?? "—")}</td><td class="mono">${esc(d.session_id ?? "—")}</td>
      <td class="num">${d.rssi !== null && d.rssi !== undefined ? d.rssi + " dBm" : "—"}</td></tr>`;
  }).join("") : `<tr><td colspan="7" class="empty">No device has sent telemetry to this backend yet.</td></tr>`;
}

function renderDetail() {
  const r = state.latest.get(state.selected);
  const d = state.devices.get(state.selected);
  setText("dName", state.selected ? `· ${state.selected}` : "");
  if (!r || !d) {
    for (const id of ["dDetail", "dCal", "dSensors"]) $(id).innerHTML = `<p class="empty">No telemetry from this device yet.</p>`;
    return;
  }
  $("dDetail").innerHTML = kv([
    ["Device ID", `<span class="mono">${esc(r.device_id)}</span>`],
    ["Firmware", `<span class="mono">${esc(r.fw_version)}</span>`],
    ["Device uptime", duration((r.device_uptime_ms ?? 0) / 1000)],
    ["Boot ID", `<span class="mono">${esc(r.boot_id)}</span>`],
    ["Session ID", `<span class="mono">${esc(r.session_id)}</span>`],
    ["Operating state", esc(r.op_state)],
    ["Wi-Fi RSSI", r.rssi !== null ? `${r.rssi} dBm` : "—"],
    ["Last telemetry", `${timeOf(r.received_ts, true)} (${ago(serverNow() - r.received_ts)})`],
    ["Client address", `<span class="mono">${esc(d.remote_addr ?? "—")}</span>`],
    ["Offline buffer", r.buffer ? `${r.buffer.queued} queued · ${r.buffer.dropped} dropped` : "—"],
  ]);
  const c = r.cal;
  $("dCal").innerHTML = !c ? `<p class="empty">No calibration information in the latest reading.</p>` : kv([
    ["Status", `${esc(c.status)}${c.error ? ` — <span class="warnline">${esc(c.error)}</span>` : ""}`],
    ["Trigger / attempts", `${esc(c.trigger ?? "—")} / ${c.attempts}`],
    ["Initial inclination", num(c.incl0_deg, 3, "°")],
    ["Mounting axis", esc(c.mount_axis ?? "—")],
    ["Stability (accel / gyro std)", `${num(c.accel_std_g, 4, " g")} / ${num(c.gyro_std_dps, 3, " °/s")}`],
    ["Gyro bias", c.gyro_bias_dps ? c.gyro_bias_dps.map((v) => num(v, 3)).join(", ") + " °/s" : "—"],
    ["SW-420 background", num(c.vib_bg_eps, 3, " events/s")],
    ["Soil / water ADC at start", `${c.soil_raw0 ?? "—"} / ${c.water_raw0 ?? "—"} (fixed endpoints 4090–921 / 0–1725)`],
    ["Shift from previous baseline", c.prev_shift_deg !== null && c.prev_shift_deg !== undefined ? num(c.prev_shift_deg, 2, "°") : "no previous baseline"],
    ["Saved to NVS", c.nvs_saved === undefined || c.nvs_saved === null ? "—" : c.nvs_saved ? "yes" : "NO"],
  ]);
  const s = r.sensors || {};
  $("dSensors").innerHTML = `<div class="tablewrap"><table class="data"><thead><tr><th>Sensor</th><th>Health</th><th>Raw</th><th>Flags</th></tr></thead><tbody>
    <tr><td>Soil moisture</td><td>${chip(s.soil?.health)}</td><td class="num">${s.soil?.raw ?? "—"}</td><td>${list(s.soil?.flags)}</td></tr>
    <tr><td>Water sensor</td><td>${chip(s.water?.health)}</td><td class="num">${s.water?.raw ?? "—"}</td><td>${list(s.water?.flags)}</td></tr>
    <tr><td>MPU6050</td><td>${chip(s.mpu?.health)}</td><td class="num">${s.mpu?.temp_c !== null && s.mpu?.temp_c !== undefined ? num(s.mpu.temp_c, 1, " °C") : "—"}</td><td>${list(s.mpu?.flags)}</td></tr>
    <tr><td>SW-420</td><td>${chip(s.vib?.health)}</td><td class="num">DO ${s.vib?.do ?? "—"}</td><td>connectivity not verifiable from a digital output</td></tr>
    </tbody></table></div><p><strong>Active faults:</strong> ${list(r.faults)}</p>`;
}

async function renderSystem() {
  try {
    const [h, comm] = await Promise.all([api("/api/health"), api("/api/events?category=communication&limit=25")]);
    state.health = h;
    $("dSystem").innerHTML = kv([
      ["API", `<span class="pill ${h.status === "ok" ? "SAFE" : "CRITICAL"}">${esc(h.status.toUpperCase())}</span> version ${esc(h.version)}`],
      ["Live stream", `<span id="dStream">${streamText()}</span>`],
      ["Database", `${h.database.ok ? "OK" : "ERROR"} · schema v${h.database.schema_version ?? "?"}`],
      ["Model", h.model.ready ? `ready · <span class="mono">${esc(displayModelText(h.model.active_version))}</span>${h.model.demo_model ? " (experimental model)" : ""}` : `<span class="warnline">not ready — ${esc(displayModelText(h.model.reason))}</span>`],
      ["Registered devices", esc(h.config.registered_devices.join(", ") || "none")],
      ["Offline after / stale after", `${h.config.device_offline_s} s / ${h.config.prediction_fresh_s} s`],
      ["Server time", timeOf(h.server_ts, true)],
      ["Configuration warnings", h.warnings.length ? h.warnings.map(esc).join("<br>") : "none"],
    ]);
    $("dComm").querySelector("tbody").innerHTML = comm.items.length ? comm.items.map((e) =>
      `<tr><td>${timeOf(e.created_ts, true)}</td><td><span class="sev sev-${esc(e.severity)}"></span>${esc(e.kind)}</td>
       <td>${esc(e.device_id ?? "—")}</td><td>${esc(e.message)}</td></tr>`).join("")
      : `<tr><td colspan="4" class="empty">No communication faults recorded.</td></tr>`;
  } catch (err) {
    $("dSystem").innerHTML = `<div class="banner bad">Backend unreachable: ${esc(err.message)}</div>`;
  }
}

export function initDevice() {
  $("dTable").addEventListener("click", (e) => {
    const b = e.target.closest("button[data-dev]");
    if (!b) return;
    state.selected = b.dataset.dev;
    renderDevice();
  });
}

function streamText() { return state.stream === "live" ? "connected" : state.stream === "connecting" ? "connecting…" : "disconnected (retrying)"; }

export function renderDevice() {
  renderDevices();
  renderDetail();
  const el = $("dStream");
  if (el) el.textContent = streamText();
}

export async function showDevice() { renderDevice(); await renderSystem(); }
