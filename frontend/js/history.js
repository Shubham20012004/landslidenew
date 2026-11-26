// History & data logs view.
import { fillSensorCharts, sensorCharts } from "./charts.js";
import { state, serverNow } from "./state.js";
import { $, api, esc, fromLocalInput, num, options, riskPill, setText, timeOf, toLocalInput } from "./util.js";

let charts = null;
let cursors = [];        // stack of `before` cursors for pages already visited (null = newest page)
let nextBefore = null;
let eventsBefore = null;
let filters = {};
let initialized = false;

function query(extra = {}) {
  const q = new URLSearchParams();
  for (const [k, v] of Object.entries({ ...filters, ...extra })) if (v !== null && v !== undefined && v !== "") q.set(k, v);
  return q.toString();
}

function readForm() {
  const start = fromLocalInput($("hStart").value);
  const end = fromLocalInput($("hEnd").value);
  filters = {
    device_id: $("hDevice").value,
    session_id: $("hSession").value,
    start: start !== null ? start.toFixed(0) : "",
    end: end !== null ? end.toFixed(0) : "",
    risk_state: $("hRisk").value,
  };
}

function showError(msg) {
  const b = $("histError");
  b.textContent = msg || "";
  b.className = "banner bad" + (msg ? "" : " hidden");
}

async function loadSessions() {
  const dev = $("hDevice").value;
  try {
    const s = await api(`/api/sessions?limit=200${dev ? "&device_id=" + encodeURIComponent(dev) : ""}`);
    options($("hSession"), s.items.map((x) => ({ value: x.session_id,
      label: `${x.session_id} · ${timeOf(x.started_ts, true)} · ${x.reading_count} readings` })), { empty: "All sessions" });
  } catch (err) { showError(`Sessions unavailable: ${err.message}`); }
}

async function loadCharts() {
  const { risk_state, ...f } = filters;   // charts show all readings of the selection
  const q = new URLSearchParams();
  for (const [k, v] of Object.entries(f)) if (v) q.set(k, v);
  if (!f.start && !f.session_id) q.set("start", (serverNow() - 86400).toFixed(0));
  q.set("max_points", "800");
  const s = await api(`/api/readings/series?${q}`);
  fillSensorCharts(charts, s);
  setText("hChartInfo", s.points.length
    ? (s.bucket_s ? `· ${s.points.length} points, ${s.bucket_s}-s averages` : `· ${s.points.length} readings`) +
      (!f.start && !f.session_id ? " · last 24 h" : "")
    : "· no readings in this selection");
}

async function loadPage(before) {
  const data = await api(`/api/readings/history?${query({ limit: 50, before: before ?? "", with_total: before ? "" : "true" })}`);
  nextBefore = data.next_before;
  if (data.total !== null && data.total !== undefined) setText("hCount", `· ${data.total.toLocaleString()} matching`);
  const tbody = $("hTable").querySelector("tbody");
  tbody.innerHTML = data.items.length ? data.items.map((r) => {
    const f = r.features, p = r.prediction;
    const tags = [r.is_replay ? "replayed" : "", r.origin === "simulated" ? "simulated" : "", ...r.faults.map((x) => x.toLowerCase())]
      .filter(Boolean).join(", ");
    return `<tr><td>${timeOf(r.received_ts, true)}</td><td class="mono">${esc(r.session_id)}</td><td class="num">${r.seq}</td>
      <td class="num">${num(f.soil_moisture !== null ? f.soil_moisture * 100 : null, 1)}</td>
      <td class="num">${num(f.water_wetness !== null ? f.water_wetness * 100 : null, 1)}</td>
      <td class="num">${num(f.tilt_deg, 3)}</td><td class="num">${num(f.vib_events_per_s, 2)}</td>
      <td>${riskPill(p.status === "OK" ? p.risk_state : p.status)}</td>
      <td class="num">${p.status === "OK" ? num(p.confidence * 100, 1, "%") : "—"}</td><td>${esc(tags)}</td></tr>`;
  }).join("") : `<tr><td colspan="10" class="empty">No readings match these filters.</td></tr>`;
  $("hOlder").disabled = !nextBefore;
  $("hNewer").disabled = cursors.length <= 1;
}

async function loadEvents(append = false) {
  const q = new URLSearchParams({ limit: "30" });
  if (filters.device_id) q.set("device_id", filters.device_id);
  if (filters.session_id) q.set("session_id", filters.session_id);
  if (filters.start) q.set("start", filters.start);
  if (filters.end) q.set("end", filters.end);
  if (append && eventsBefore) q.set("before", eventsBefore);
  const data = await api(`/api/events?${q}`);
  eventsBefore = data.next_before;
  const tbody = $("hEvents").querySelector("tbody");
  const rows = data.items.map((e) => `<tr><td>${timeOf(e.created_ts, true)}</td><td><span class="sev sev-${esc(e.severity)}"></span>${esc(e.severity)}</td>
    <td>${esc(e.message)}</td><td class="mono">${esc(e.session_id ?? "")}</td><td class="mono">${esc(e.kind)}</td></tr>`).join("");
  if (append) tbody.insertAdjacentHTML("beforeend", rows);
  else tbody.innerHTML = rows || `<tr><td colspan="5" class="empty">No events in this selection.</td></tr>`;
  $("hMoreEvents").disabled = !eventsBefore;
}

function updateExport() {
  const { risk_state, ...f } = filters;
  const q = new URLSearchParams({ scope: "readings" });
  for (const [k, v] of Object.entries(f)) if (v) q.set(k, v);
  $("hExport").href = `/api/export/csv?${q}`;
}

async function apply() {
  readForm();
  if (filters.start && filters.end && Number(filters.end) <= Number(filters.start)) {
    showError("‘To’ must be after ‘From’.");
    return;
  }
  showError("");
  cursors = [null];
  updateExport();
  try {
    await Promise.all([loadCharts(), loadPage(null), loadEvents(false)]);
  } catch (err) { showError(`Could not load history: ${err.message}`); }
}

export function initHistory() {
  charts = sensorCharts(["hSoil", "hWater", "hTilt", "hVib"]);
  $("histForm").addEventListener("submit", (e) => { e.preventDefault(); apply(); });
  $("hDevice").addEventListener("change", async () => { $("hSession").value = ""; await loadSessions(); });
  $("hReset").addEventListener("click", async () => {
    $("hSession").value = ""; $("hStart").value = ""; $("hEnd").value = ""; $("hRisk").value = "";
    await apply();
  });
  $("hOlder").addEventListener("click", async () => {
    if (!nextBefore) return;
    cursors.push(nextBefore);
    try { await loadPage(nextBefore); } catch (err) { showError(err.message); }
  });
  $("hNewer").addEventListener("click", async () => {
    if (cursors.length <= 1) return;
    cursors.pop();
    try { await loadPage(cursors[cursors.length - 1]); } catch (err) { showError(err.message); }
  });
  $("hMoreEvents").addEventListener("click", () => loadEvents(true).catch((err) => showError(err.message)));
}

export async function showHistory() {
  const devs = [...state.devices.values()];
  options($("hDevice"), devs.map((d) => ({ value: d.device_id, label: d.device_id + (d.origin === "simulated" ? " (simulated)" : "") })),
    { empty: "All devices", value: initialized ? undefined : state.selected ?? "" });
  if (!initialized) {
    initialized = true;
    $("hEnd").value = "";
    $("hStart").value = toLocalInput(serverNow() - 3600 * 6);
    await loadSessions();
    await apply();
  }
}
