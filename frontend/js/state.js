// Shared client state, updated from the SSE stream and HTTP calls.
export const state = {
  serverOffset: 0,             // server_ts - client clock (s)
  stream: "connecting",        // connecting | live | down
  health: null,
  model: null,
  devices: new Map(),          // device_id -> device
  latest: new Map(),           // device_id -> reading (live only)
  events: [],                  // newest first
  eventIds: new Set(),
  selected: null,              // device_id shown on the live/device pages
  listeners: new Set(),
};

export function serverNow() { return Date.now() / 1000 + state.serverOffset; }

export function syncClock(serverTs) {
  if (Number.isFinite(serverTs)) state.serverOffset = serverTs - Date.now() / 1000;
}

export function emit(kind) { for (const fn of state.listeners) fn(kind); }
export function subscribe(fn) { state.listeners.add(fn); }

export function addEvent(ev) {
  if (state.eventIds.has(ev.id)) return false;
  state.eventIds.add(ev.id);
  state.events.unshift(ev);
  state.events.sort((a, b) => b.id - a.id);
  if (state.events.length > 100) {
    for (const e of state.events.splice(100)) state.eventIds.delete(e.id);
  }
  return true;
}

/** Accept a live reading only if it is newer than what we have for that device. */
export function acceptReading(r) {
  const cur = state.latest.get(r.device_id);
  if (cur && r.pk <= cur.pk) return false;
  state.latest.set(r.device_id, r);
  const d = state.devices.get(r.device_id) || { device_id: r.device_id, origin: r.origin };
  Object.assign(d, { last_seen_ts: r.received_ts, session_id: r.session_id, fw_version: r.fw_version,
    boot_id: r.boot_id, rssi: r.rssi, last_reading_pk: r.pk, origin: r.origin });
  state.devices.set(r.device_id, d);
  return true;
}

export function offlineAfter() { return state.health?.config?.device_offline_s ?? 10; }
export function freshFor() { return state.health?.config?.prediction_fresh_s ?? 10; }

export function deviceOnline(deviceId) {
  const d = state.devices.get(deviceId);
  return !!d && serverNow() - d.last_seen_ts <= offlineAfter();
}

export function pickDefaultDevice() {
  if (state.selected && state.devices.has(state.selected)) return state.selected;
  let best = null;
  for (const d of state.devices.values()) if (!best || d.last_seen_ts > best.last_seen_ts) best = d;
  state.selected = best?.device_id ?? null;
  return state.selected;
}
