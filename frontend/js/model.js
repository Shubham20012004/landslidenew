// Model & dataset view: active model, genuine metrics, feature schema, labelling.
import { state, serverNow } from "./state.js";
import { $, api, esc, fromLocalInput, num, options, riskPill, RISKS, setText, timeOf, toLocalInput } from "./util.js";

let impChart = null;
const TOKEN_KEY = "landslide.adminToken";

function kv(rows) {
  return `<dl class="kv">${rows.map(([k, v]) => `<dt>${esc(k)}</dt><dd>${v}</dd>`).join("")}</dl>`;
}

function pctv(v) { return v === null || v === undefined ? "—" : `${(v * 100).toFixed(1)} %`; }

function renderStatus(s) {
  const ready = s.ready
    ? `<span class="pill SAFE">READY</span>`
    : `<span class="pill NA">MODEL NOT READY</span>`;
  const prov = s.provenance === "synthetic_demo"
    ? `<span class="badge demo">SYNTHETIC demonstration data</span>`
    : s.provenance === "experimental" ? `<span class="badge">Real experimental data</span>` : esc(s.provenance ?? "—");
  $("mStatus").innerHTML = kv([
    ["Status", ready + (s.reason ? `<div class="warnline">${esc(s.reason)}</div>` : "")],
    ["Active version", `<span class="mono">${esc(s.active_version ?? "none")}</span>`],
    ["Training data", prov],
    ["Created", esc(s.created_at ?? "—")],
    ["Demonstration mode", s.demo_mode ? "ON (synthetic models allowed)" : "off"],
    ["Feature schema", `${esc(s.feature_schema)} (model: ${esc(s.model_feature_schema ?? "—")})`],
    ["Hyperparameters", s.hyperparameters ? `<span class="mono">${esc(Object.entries(s.hyperparameters).map(([k, v]) => `${k}=${v}`).join(", "))}</span>` : "—"],
    ["Available versions", esc((s.registry?.available || []).join(", ") || "—")],
    ["Limitations", s.limitations ? `<ul>${s.limitations.map((l) => `<li>${esc(l)}</li>`).join("")}</ul>` : "—"],
  ]);
  const tbody = $("mSchema").querySelector("tbody");
  setText("mSchemaVer", `· ${s.feature_schema_detail?.version ?? ""}`);
  tbody.innerHTML = (s.feature_schema_detail?.features || []).map((f) =>
    `<tr title="${esc(f.description)}"><td class="mono">${esc(f.name)}</td><td>${esc(f.sensor)}</td><td>${esc(f.unit)}</td></tr>`).join("");
}

function renderMetrics(m) {
  const box = $("mMetrics");
  if (!m?.metrics) {
    box.innerHTML = `<p class="empty">No evaluation is available: ${esc(m?.reason || "the active model has no metrics file")}.</p>`;
    $("mDataset").innerHTML = `<p class="empty">No training dataset recorded.</p>`;
    renderImportance(null);
    return;
  }
  const x = m.metrics, t = x.test, base = x.majority_class_baseline, ds = x.dataset;
  const synthetic = x.provenance === "synthetic_demo";
  box.innerHTML = `
    ${synthetic ? `<div class="banner info">${esc(x.data_note)}</div>` : ""}
    <div class="tiles">
      <div class="tile"><div class="tv">${pctv(t.accuracy)}</div><div class="tk">Accuracy</div></div>
      <div class="tile"><div class="tv">${pctv(t.balanced_accuracy)}</div><div class="tk">Balanced accuracy</div></div>
      <div class="tile"><div class="tv">${num(t.macro.f1, 3)}</div><div class="tk">Macro F1</div></div>
      <div class="tile"><div class="tv">${num(base.macro.f1, 3)}</div><div class="tk">Majority baseline macro F1</div></div>
      <div class="tile"><div class="tv">${num(x.cross_validation.best_mean_f1_macro, 3)}</div><div class="tk">Grouped CV macro F1</div></div>
      <div class="tile"><div class="tv">${t.n.toLocaleString()}</div><div class="tk">Held-out readings (${x.split.test_sessions} sessions)</div></div>
    </div>
    <div class="grid-2">
      <div class="tablewrap"><table class="data"><thead><tr><th>Class</th><th>Precision</th><th>Recall</th><th>F1</th><th>Support</th></tr></thead><tbody>
        ${RISKS.map((c) => { const r = t.per_class[c]; return `<tr><td>${riskPill(c)}</td><td class="num">${num(r.precision, 3)}</td>
          <td class="num">${num(r.recall, 3)}</td><td class="num">${num(r.f1, 3)}</td><td class="num">${r.support}</td></tr>`; }).join("")}
      </tbody></table></div>
      <div class="tablewrap">${confusion(t.confusion_matrix)}</div>
    </div>
    <p class="fine">Split: ${esc(x.split.method)}. ${x.split.train_sessions} training sessions / ${x.split.test_sessions} held-out sessions; no session appears on both sides.</p>`;
  $("mDataset").innerHTML = kv([
    ["Source", synthetic ? `<span class="badge demo">SYNTHETIC</span> ${esc(ds.path)}` : `Real experimental readings (${esc(ds.path)})`],
    ["Readings used", ds.rows_used.toLocaleString()],
    ["Sessions", ds.sessions],
    ["Class distribution", RISKS.map((c) => `${c} ${ds.class_counts[c].toLocaleString()}`).join(" · ")],
    ["Dropped rows", `missing ${ds.dropped_missing}, out of range ${ds.dropped_out_of_range}, duplicates ${ds.dropped_duplicates}`],
    ["Imbalance ratio", num(x.class_imbalance_ratio, 1)],
    ["Dataset SHA-256", `<span class="mono">${esc(ds.sha256.slice(0, 16))}…</span>`],
  ]);
  renderImportance(x.feature_importance?.permutation_balanced_accuracy);
  $("mRuns").querySelector("tbody").innerHTML = (m.training_runs || []).map((r) =>
    `<tr><td class="mono">${esc(r.model_version)}</td><td>${esc(r.provenance)}</td><td>${timeOf(r.created_at, true)}</td>
     <td class="num">${(r.n_samples ?? 0).toLocaleString()}</td><td>${esc(r.status)}</td></tr>`).join("")
    || `<tr><td colspan="5" class="empty">No training runs recorded in this database.</td></tr>`;
}

function confusion(cm) {
  const rows = cm.matrix;
  return `<table class="data cm" aria-label="Confusion matrix: rows true class, columns predicted class">
    <thead><tr><th>True \\ Pred.</th>${cm.labels.map((l) => `<th>${l}</th>`).join("")}</tr></thead><tbody>
    ${rows.map((r, i) => {
      const tot = Math.max(1, r.reduce((a, b) => a + b, 0));
      return `<tr><th>${cm.labels[i]}</th>${r.map((v) => {
        const share = v / tot;
        return `<td style="background:rgba(42,93,176,${(share * 0.55).toFixed(3)});color:${share > 0.6 ? "#fff" : "inherit"}">${v}</td>`;
      }).join("")}</tr>`; }).join("")}
    </tbody></table><p class="fine">Rows: true class, columns: predicted. Shading = share of each true class.</p>`;
}

function renderImportance(imp) {
  const canvas = $("mImp");
  if (impChart) { impChart.destroy(); impChart = null; }
  if (!imp) { $("mImpTable").innerHTML = ""; return; }
  const names = Object.keys(imp);
  impChart = new window.Chart(canvas, {
    type: "bar",
    data: { labels: names, datasets: [{ data: names.map((n) => imp[n].mean), backgroundColor: "#2a5db0", borderRadius: 4,
      borderSkipped: "start", barPercentage: 0.7 }] },
    options: { indexAxis: "y", animation: false, responsive: true, maintainAspectRatio: false,
      plugins: { legend: { display: false }, tooltip: { displayColors: false,
        callbacks: { label: (c) => `−${(imp[c.label].mean * 100).toFixed(2)} pts balanced accuracy (± ${(imp[c.label].std * 100).toFixed(2)})` } } },
      scales: { x: { grid: { color: "#eef0f2" }, ticks: { color: "#6b7280" }, title: { display: true, text: "Drop in balanced accuracy when shuffled", color: "#6b7280" } },
        y: { grid: { display: false }, ticks: { color: "#4b5563", font: { family: "ui-monospace, Consolas, monospace", size: 11 } } } } },
  });
  $("mImpTable").innerHTML = `<table><tr><th>Feature</th><th>Importance</th></tr>${names.map((n) => `<tr><td>${esc(n)}</td><td>${imp[n].mean.toFixed(4)}</td></tr>`).join("")}</table>`;
}

async function renderLabels() {
  const [summary, labels] = await Promise.all([api("/api/dataset/summary"), api("/api/dataset/labels")]);
  $("mLabelSummary").innerHTML = [
    ["Real readings", summary.device_readings_total],
    ["Eligible for training", summary.eligible_readings],
    ["Labelled", summary.labeled_readings],
    ["Unlabelled (eligible)", summary.unlabeled_eligible_readings],
    ["Ambiguous (conflicting labels)", summary.ambiguous_readings],
    ["Labelled sessions", summary.labeled_sessions],
    ...RISKS.map((c) => [`${c} labels`, `${summary.per_class[c].readings} / ${summary.per_class[c].sessions} sess.`]),
  ].map(([k, v]) => `<div class="tile"><div class="tv">${typeof v === "number" ? v.toLocaleString() : esc(v)}</div><div class="tk">${esc(k)}</div></div>`).join("");
  $("annTable").querySelector("tbody").innerHTML = labels.items.length ? labels.items.map((a) => `<tr>
      <td>${riskPill(a.label)}</td><td>${esc(a.device_id)}</td><td class="mono">${esc(a.session_id ?? "any")}</td>
      <td>${esc(new Date(a.start).toLocaleString())}</td><td>${esc(new Date(a.end).toLocaleString())}</td><td>${esc(a.notes ?? "")}</td>
      <td><button class="btn danger" type="button" data-del="${a.id}" aria-label="Delete label ${a.id}">Delete</button></td></tr>`).join("")
    : `<tr><td colspan="7" class="empty">No experiment labels yet.</td></tr>`;
}

async function loadAnnotSessions() {
  const dev = $("aDevice").value;
  if (!dev) return;
  const s = await api(`/api/sessions?limit=200&device_id=${encodeURIComponent(dev)}`);
  options($("aSession"), s.items.map((x) => ({ value: x.session_id, label: `${x.session_id} · ${timeOf(x.started_ts, true)}` })),
    { empty: "Any session in range" });
}

function token() { return $("aToken").value.trim(); }

export function initModel() {
  $("aToken").value = sessionStorage.getItem(TOKEN_KEY) || "";
  $("aToken").addEventListener("change", () => sessionStorage.setItem(TOKEN_KEY, token()));
  $("aDevice").addEventListener("change", () => loadAnnotSessions().catch(() => {}));
  $("aSession").addEventListener("change", async () => {
    const sid = $("aSession").value;
    if (!sid) return;
    try {
      const s = await api(`/api/sessions/${encodeURIComponent(sid)}`);
      $("aStart").value = toLocalInput(s.started_ts);
      $("aEnd").value = toLocalInput(s.last_seen_ts + 1);
    } catch { /* keep manual values */ }
  });
  $("annForm").addEventListener("submit", async (e) => {
    e.preventDefault();
    const msg = $("annMsg");
    const start = fromLocalInput($("aStart").value), end = fromLocalInput($("aEnd").value);
    if (start === null || end === null || end <= start) { msg.textContent = "End must be after start."; return; }
    sessionStorage.setItem(TOKEN_KEY, token());
    try {
      const res = await api("/api/dataset/labels", { method: "POST", admin: token(), body: {
        device_id: $("aDevice").value, session_id: $("aSession").value || null,
        start: new Date(start * 1000).toISOString(), end: new Date(end * 1000).toISOString(),
        label: $("aLabel").value, notes: $("aNotes").value || null } });
      msg.textContent = `Saved label ${res.label} covering ${res.readings_in_range} readings.`;
      $("aNotes").value = "";
      await renderLabels();
    } catch (err) { msg.textContent = `Not saved: ${err.message}`; }
  });
  $("annTable").addEventListener("click", async (e) => {
    const b = e.target.closest("button[data-del]");
    if (!b) return;
    if (!confirm(`Delete experiment label #${b.dataset.del}? The readings themselves are kept.`)) return;
    try {
      await api(`/api/dataset/labels/${b.dataset.del}`, { method: "DELETE", admin: token() });
      await renderLabels();
    } catch (err) { $("annMsg").textContent = `Not deleted: ${err.message}`; }
  });
  $("mReload").addEventListener("click", async () => {
    const t = token() || sessionStorage.getItem(TOKEN_KEY) || "";
    if (!t) { setText("mReloadMsg", "Enter the admin token in the labelling form below first."); return; }
    try {
      const s = await api("/api/model/reload", { method: "POST", admin: t });
      setText("mReloadMsg", s.ready ? `Loaded ${s.active_version}.` : `Not ready: ${s.reason}`);
      await showModel();
    } catch (err) { setText("mReloadMsg", err.message); }
  });
}

export async function showModel() {
  const real = [...state.devices.values()];
  options($("aDevice"), real.map((d) => ({ value: d.device_id, label: d.device_id + (d.origin === "simulated" ? " (simulated — excluded from training)" : "") })),
    { value: $("aDevice").value || state.selected });
  if (!$("aStart").value) {
    $("aStart").value = toLocalInput(serverNow() - 600);
    $("aEnd").value = toLocalInput(serverNow());
  }
  try {
    const [status, metrics] = await Promise.all([api("/api/model/status"), api("/api/model/metrics")]);
    renderStatus(status);
    renderMetrics(metrics);
    await renderLabels();
    await loadAnnotSessions();
  } catch (err) {
    $("mMetrics").innerHTML = `<div class="banner bad">Could not load model information: ${esc(err.message)}</div>`;
  }
}
