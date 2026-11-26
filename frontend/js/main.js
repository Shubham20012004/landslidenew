// Entry point: routing, live stream, periodic refresh.
import { initDevice, renderDevice, showDevice } from "./device.js";
import { initHistory, showHistory } from "./history.js";
import { initLive, loadCharts, onLiveReading, renderLive, tickCharts } from "./live.js";
import { initModel, showModel } from "./model.js";
import { acceptReading, addEvent, pickDefaultDevice, state, syncClock } from "./state.js";
import { LiveStream } from "./stream.js";
import { $, api } from "./util.js";

const ROUTES = ["live", "history", "model", "device"];
let route = "live";

function currentRoute() {
  const r = location.hash.replace("#", "");
  return ROUTES.includes(r) ? r : "live";
}

async function navigate() {
  route = currentRoute();
  for (const r of ROUTES) $(`view-${r}`).classList.toggle("hidden", r !== route);
  for (const a of document.querySelectorAll(".sidebar a[data-route]")) {
    const on = a.dataset.route === route;
    a.classList.toggle("active", on);
    if (on) a.setAttribute("aria-current", "page"); else a.removeAttribute("aria-current");
  }
  document.title = `${$(`h-${route}`).textContent} · Landslide Monitor`;
  if (route === "live") renderLive();
  if (route === "history") await showHistory();
  if (route === "model") await showModel();
  if (route === "device") await showDevice();
}

function renderStreamPill() {
  const pill = $("streamPill");
  const cls = state.stream === "live" ? "ok" : state.stream === "connecting" ? "wait" : "bad";
  pill.className = `conn ${cls}`;
  pill.lastElementChild.textContent = state.stream === "live" ? "Live stream connected"
    : state.stream === "connecting" ? "Connecting…" : "Backend disconnected — retrying";
}

function refreshCurrent() {
  if (route === "live") renderLive();
  if (route === "device") renderDevice();
}

const stream = new LiveStream("/api/stream", {
  status(s) {
    state.stream = s;
    renderStreamPill();
    refreshCurrent();
  },
  snapshot(d) {
    syncClock(d.server_ts);
    state.model = d.model;
    for (const dev of d.devices) state.devices.set(dev.device_id, dev);
    for (const r of Object.values(d.latest)) acceptReading(r);
    for (const e of d.events) addEvent(e);
    pickDefaultDevice();
    loadCharts();   // (re)synchronise charts on connect and after every reconnect
    refreshCurrent();
  },
  reading(r) {
    if (!acceptReading(r)) return;
    if (!state.selected) { pickDefaultDevice(); loadCharts(); }
    onLiveReading(r);
    refreshCurrent();
  },
  event(e) {
    if (addEvent(e)) refreshCurrent();
  },
  model(m) {
    state.model = m;
    refreshCurrent();
  },
  heartbeat(h) { syncClock(h.server_ts); },
});

async function loadHealth() {
  try {
    state.health = await api("/api/health");
    syncClock(state.health.server_ts);
  } catch { /* the stream status shows the outage */ }
}

window.addEventListener("hashchange", navigate);
window.addEventListener("DOMContentLoaded", async () => {
  if (!window.Chart) {
    document.body.insertAdjacentHTML("afterbegin", '<div class="banner bad">Chart library failed to load.</div>');
  }
  initLive();
  initHistory();
  initModel();
  initDevice();
  await loadHealth();
  stream.start();
  await navigate();
  // Ages, staleness and the moving chart window update every second.
  setInterval(() => { refreshCurrent(); tickCharts(); }, 1000);
  setInterval(loadHealth, 30000);
});
