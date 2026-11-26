"use strict";

const $ = (id) => document.getElementById(id);

let chart;
const REFRESH_MS = 2000;

function fmt(value, digits = 3) {
  if (value === null || value === undefined || value === "" || !Number.isFinite(Number(value))) {
    return "—";
  }
  return Number(value).toFixed(digits);
}

function timeLocal(seconds) {
  if (seconds == null) return "";
  const d = new Date(Number(seconds) * 1000);
  return isNaN(d.getTime()) ? "" : d.toLocaleTimeString();
}

async function getJSON(url) {
  const res = await fetch(url, { cache: "no-store" });
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  return await res.json();
}

function setChip(id, status) {
  const el = $(id);
  if (!el) return;
  el.textContent = status || "—";
  el.className = "chip " + (status || "");
}

function renderLatest(data) {
  const r = data?.reading;

  if (!r) {
    $("conn").textContent = "No data";
    return;
  }

  $("conn").textContent = "Backend connected";

  const state = r.risk_state || "UNKNOWN";
  $("stateName").textContent = state;
  $("score").textContent = fmt(r.risk_score, 3);
  $("healthLine").textContent = `Health: ${r.health || "—"}`;
  $("sourceTag").textContent = r.source === "device" ? "REAL ESP32 DEVICE" : "SIMULATED";

  const box = $("stateBox");
  box.className = "risk-box s-" + state;

  $("cM").textContent = r.moisture == null ? "—" : fmt(r.moisture, 3);
  $("cR").textContent = r.rain == null ? "—" : fmt(r.rain, 3);
  $("cT").textContent = r.tilt_deg == null ? "—" : fmt(r.tilt_deg, 2) + "°";

  setChip("stSoil", r.soil_status || "—");
  setChip("stRain", r.rain_status || "—");
  setChip("stMpu", r.mpu_status || "—");

  $("devId").textContent = r.device_id || "—";
  $("fw").textContent = r.fw_version || "—";
  $("rssi").textContent = r.wifi_rssi == null ? "—" : `${r.wifi_rssi} dBm`;
  $("age").textContent = data.age_s == null ? "—" : `${Math.round(data.age_s)} s ago`;
}

function makeChart() {
  const ctx = $("chSensors");
  if (!ctx || typeof Chart === "undefined") return;

  chart = new Chart(ctx, {
    type: "line",
    data: {
      labels: [],
      datasets: [
        {
          label: "Soil Moisture",
          data: [],
          borderColor: "#4da3ff",
          pointRadius: 0,
          tension: 0.25
        },
        {
          label: "Rain Activity",
          data: [],
          borderColor: "#28c7c7",
          pointRadius: 0,
          tension: 0.25
        },
        {
          label: "Tilt (deg)",
          data: [],
          borderColor: "#ff9f40",
          pointRadius: 0,
          tension: 0.25,
          yAxisID: "y1"
        }
      ]
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      animation: false,
      plugins: {
        legend: {
          position: "bottom",
          labels: {
            color: "#e8eef5"
          }
        }
      },
      scales: {
        x: {
          ticks: { color: "#9fb0c0" },
          grid: { color: "#2d3b49" }
        },
        y: {
          min: 0,
          max: 1,
          ticks: { color: "#9fb0c0" },
          grid: { color: "#2d3b49" }
        },
        y1: {
          position: "right",
          ticks: { color: "#9fb0c0" },
          grid: { drawOnChartArea: false }
        }
      }
    }
  });
}

function renderHistory(data) {
  if (!chart) return;

  const rows = Array.isArray(data?.readings) ? data.readings.slice(-30) : [];

  chart.data.labels = rows.map(r => timeLocal(r.server_time));
  chart.data.datasets[0].data = rows.map(r => r.moisture ?? null);
  chart.data.datasets[1].data = rows.map(r => r.rain ?? null);
  chart.data.datasets[2].data = rows.map(r => r.tilt_deg ?? null);

  chart.update("none");
}

async function refreshDashboard() {
  try {
    const minutes = $("window")?.value || "15";

    const [latest, history] = await Promise.all([
      getJSON("/api/latest?source=device"),
      getJSON(`/api/history?minutes=${minutes}&source=device`)
    ]);

    renderLatest(latest);
    renderHistory(history);

  } catch (err) {
    console.error(err);
    $("conn").textContent = "Backend error";
  }
}

function init() {
  makeChart();
  $("window")?.addEventListener("change", refreshDashboard);
  refreshDashboard();
  setInterval(refreshDashboard, REFRESH_MS);
}

document.addEventListener("DOMContentLoaded", init);