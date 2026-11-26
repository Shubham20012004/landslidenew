// Time-series charts (Chart.js, linear time axis in ms - no date adapter needed).
const SERIES_COLOR = "#2a5db0";

function timeTick(value, range) {
  const d = new Date(value);
  if (range > 2 * 86400 * 1000) return d.toLocaleDateString([], { month: "short", day: "2-digit" });
  return d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", ...(range < 600000 ? { second: "2-digit" } : {}) });
}

export class SeriesChart {
  /** @param opts {unit, digits, yMin, yMax, maxPoints, gapMs} */
  constructor(canvas, opts) {
    this.opts = { digits: 2, maxPoints: 1500, gapMs: 8000, ...opts };
    this.windowMs = null;
    const o = this.opts;
    this.chart = new window.Chart(canvas, {
      type: "line",
      data: { datasets: [{ data: [], borderColor: SERIES_COLOR, backgroundColor: SERIES_COLOR, borderWidth: 2,
        pointRadius: 0, pointHoverRadius: 4, pointHitRadius: 8, spanGaps: false, tension: 0 }] },
      options: {
        animation: false, responsive: true, maintainAspectRatio: false, parsing: false, normalized: true,
        interaction: { mode: "nearest", axis: "x", intersect: false },
        plugins: {
          legend: { display: false },
          tooltip: {
            displayColors: false,
            callbacks: {
              title: (items) => items.length ? new Date(items[0].parsed.x).toLocaleString() : "",
              label: (item) => `${item.parsed.y.toFixed(o.digits)}${o.unit ? " " + o.unit : ""}`,
            },
          },
        },
        scales: {
          x: { type: "linear", grid: { display: false }, border: { color: "#cfd4da" },
            ticks: { color: "#6b7280", maxTicksLimit: 6, maxRotation: 0,
              callback: (v) => timeTick(v, this.range()) } },
          y: { min: o.yMin, max: o.yMax, beginAtZero: o.yMin === undefined,
            grid: { color: "#eef0f2" }, border: { display: false },
            ticks: { color: "#6b7280", maxTicksLimit: 5 } },
        },
      },
    });
  }

  range() {
    if (this.windowMs) return this.windowMs;
    const d = this.chart?.data.datasets[0].data;   // undefined while the chart is being constructed
    return d && d.length > 1 ? d[d.length - 1].x - d[0].x : 0;
  }

  /** points: [{t (s), v}] sorted; inserts null breaks where data is missing or time gaps exceed gapMs. */
  setData(points, gapMs) {
    const gap = gapMs ?? this.opts.gapMs;
    const out = [];
    let prev = null;
    for (const p of points) {
      const x = p.t * 1000;
      if (prev !== null && x - prev > gap) out.push({ x: prev + 1, y: null });
      out.push({ x, y: Number.isFinite(p.v) ? p.v : null });
      prev = x;
    }
    this.chart.data.datasets[0].data = out;
    this.applyWindow();
    this.chart.update("none");
  }

  /** Append one live point; keeps a bounded window. */
  push(t, v) {
    const data = this.chart.data.datasets[0].data;
    const x = t * 1000;
    const last = data[data.length - 1];
    if (last && x <= last.x) return;               // old / duplicate update
    if (last && x - last.x > this.opts.gapMs) data.push({ x: last.x + 1, y: null });
    data.push({ x, y: Number.isFinite(v) ? v : null });
    this.trim();
    this.applyWindow();
    this.chart.update("none");
  }

  setWindow(ms) { this.windowMs = ms; }

  applyWindow() {
    const sx = this.chart.options.scales.x;
    if (this.windowMs) {
      const now = Date.now();
      sx.min = now - this.windowMs;
      sx.max = now;
    } else {
      sx.min = undefined;
      sx.max = undefined;
    }
  }

  trim() {
    const data = this.chart.data.datasets[0].data;
    const cutoff = this.windowMs ? Date.now() - this.windowMs : -Infinity;
    let i = 0;
    while (i < data.length && (data[i].x < cutoff || data.length - i > this.opts.maxPoints)) i++;
    if (i) data.splice(0, i);
  }

  tick() { if (this.windowMs) { this.trim(); this.applyWindow(); this.chart.update("none"); } }
}

/** The four standard sensor charts. */
export function sensorCharts(ids) {
  return {
    soil: new SeriesChart(document.getElementById(ids[0]), { unit: "%", digits: 1, yMin: 0, yMax: 100 }),
    water: new SeriesChart(document.getElementById(ids[1]), { unit: "%", digits: 1, yMin: 0, yMax: 100 }),
    tilt: new SeriesChart(document.getElementById(ids[2]), { unit: "°", digits: 3 }),
    vib: new SeriesChart(document.getElementById(ids[3]), { unit: "events/s", digits: 2 }),
  };
}

/** Load /api/readings/series points into the four charts. */
export function fillSensorCharts(charts, series) {
  const pts = series.points || [];
  const pick = (key, scale = 1) => pts.map((p) => ({ t: p.t, v: p[key] === null ? NaN : p[key] * scale }));
  const gap = series.bucket_s ? series.bucket_s * 1000 * 2.5 : undefined;
  charts.soil.setData(pick("soil_moisture", 100), gap);
  charts.water.setData(pick("water_wetness", 100), gap);
  charts.tilt.setData(pick("tilt_deg"), gap);
  charts.vib.setData(pick("vib_events_per_s"), gap);
}
