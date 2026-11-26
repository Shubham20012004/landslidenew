"""Generate report tables, figures and LaTeX snippets from the SQLite log.

    python report_tools/make_report.py                    # REAL device records only (default)
    python report_tools/make_report.py --allow-simulated  # also simulated; every such figure/table is marked SIMULATED

Outputs (report_tools/outputs/):
  table_5_1_gpio.md/.tex       GPIO table generated from the .ino pin configuration (fills report Table 5.1)
  table_10_2_summary.csv/.tex  per-experiment summary (Table 10.2)
  table_10_3_transitions.csv/.tex  risk-state transitions with times (Table 10.3)
  latency.json/.tex            device->server latency statistics (Sec. 11.4)
  ts_<experiment>.png          time-series plots (moisture, rain, tilt, risk score with thresholds)
  manifest.json                what was generated, from which data source, and what is still missing

Nothing is invented: if there are no real device records, tables stay empty
and the manifest says which report placeholders still need real experiments.
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
import time
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
from backend.ino_config import load_ino_config, pin_table  # noqa: E402

STATES = ["SAFE", "WATCH", "WARNING", "CRITICAL"]
RANK = {s: i for i, s in enumerate(STATES)}


def tex_escape(s) -> str:
    s = "" if s is None else str(s)
    for a, b in (("\\", r"\textbackslash{}"), ("&", r"\&"), ("%", r"\%"), ("$", r"\$"), ("#", r"\#"), ("_", r"\_"),
                 ("{", r"\{"), ("}", r"\}"), ("~", r"\textasciitilde{}"), ("^", r"\textasciicircum{}")):
        s = s.replace(a, b)
    return s


def latex_table(df: pd.DataFrame, caption: str, label: str) -> str:
    cols = list(df.columns)
    lines = [r"\begin{table}[htbp]", r"\centering", rf"\caption{{{tex_escape(caption)}}}", rf"\label{{{label}}}",
             r"\begin{tabular}{" + "l" * len(cols) + "}", r"\hline",
             " & ".join(tex_escape(c) for c in cols) + r" \\", r"\hline"]
    for _, row in df.iterrows():
        lines.append(" & ".join(tex_escape(v) for v in row.values) + r" \\")
    lines += [r"\hline", r"\end{tabular}", r"\end{table}", ""]
    return "\n".join(lines)


def watermark(fig, simulated: bool):
    if simulated:
        fig.text(0.5, 0.5, "SIMULATED", fontsize=60, color="red", alpha=0.18, ha="center", va="center", rotation=25)
        fig.text(0.5, 0.01, "SIMULATED DATA — not from the physical prototype", ha="center", fontsize=9, color="red")


def load(db: Path, allow_simulated: bool):
    con = sqlite3.connect(db)
    readings = pd.read_sql_query("SELECT * FROM readings ORDER BY id", con)
    events = pd.read_sql_query("SELECT * FROM events ORDER BY id", con)
    exps = pd.read_sql_query("SELECT * FROM experiments ORDER BY id", con)
    con.close()
    if not allow_simulated:
        readings = readings[readings["source"] == "device"]
        events = events[(events["source"] == "device") | events["source"].isna()]
    return readings, events, exps


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--db", default=str(REPO / "backend" / "data" / "landslide.db"))
    ap.add_argument("--out", default=str(REPO / "report_tools" / "outputs"))
    ap.add_argument("--allow-simulated", action="store_true")
    args = ap.parse_args(argv)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    cfg = load_ino_config()
    manifest = {"generated_at": time.strftime("%Y-%m-%d %H:%M:%S %z"), "db": args.db,
                "allow_simulated": args.allow_simulated, "files": [], "still_missing": []}

    # ---- GPIO table from the firmware (always available) ----
    pins = pd.DataFrame(pin_table())
    pins.columns = ["Signal", "ESP32 pin / address", "Remarks (from .ino)"]
    md = ["| " + " | ".join(pins.columns) + " |", "|" + "---|" * len(pins.columns)]
    md += ["| " + " | ".join(str(v) for v in r) + " |" for r in pins.values]
    (out / "table_5_1_gpio.md").write_text("\n".join(md) + "\n")
    (out / "table_5_1_gpio.tex").write_text(latex_table(pins, "GPIO pin assignment (generated from landslide_monitor.ino)", "tab:gpio"))
    manifest["files"] += ["table_5_1_gpio.md", "table_5_1_gpio.tex"]

    if not Path(args.db).exists():
        manifest["still_missing"].append("SQLite database not found: run the backend and record experiments first.")
        (out / "manifest.json").write_text(json.dumps(manifest, indent=2))
        print(json.dumps(manifest, indent=2))
        return manifest

    readings, events, exps = load(Path(args.db), args.allow_simulated)
    sources = sorted(readings["source"].dropna().unique().tolist()) if len(readings) else []
    simulated = "simulated" in sources
    manifest["sources_included"] = sources
    manifest["n_readings"] = int(len(readings))
    if readings.empty:
        manifest["still_missing"] += [
            "No real device readings: Table 10.2, Table 10.3, Sec. 11.2-11.4 plots and latency remain [DATA]/[INSERT].",
            "Record runs: start the backend, start an experiment in the dashboard, run the scenario on the prototype."]
        (out / "manifest.json").write_text(json.dumps(manifest, indent=2))
        print(json.dumps(manifest, indent=2))
        return manifest

    tag = " (SIMULATED)" if simulated else ""
    exp_name = {int(r.id): f"{r.name}" for r in exps.itertuples()} if len(exps) else {}
    exp_scn = {int(r.id): r.scenario for r in exps.itertuples()} if len(exps) else {}
    exp_start = {int(r.id): r.started_at for r in exps.itertuples()} if len(exps) else {}
    readings["group"] = readings["experiment_id"].apply(lambda x: int(x) if pd.notna(x) else -1)

    # ---- Table 10.2: per-experiment summary ----
    rows = []
    for gid, g in readings.groupby("group"):
        src = ",".join(sorted(g["source"].unique()))
        states = g["risk_state"].dropna()
        known = [s for s in states if s in RANK]
        rows.append({
            "Experiment": exp_name.get(gid, "(untagged)"), "Scenario": exp_scn.get(gid, "") or (g["scenario"].dropna().iloc[0] if g["scenario"].notna().any() else ""),
            "Source": src, "Samples": len(g),
            "Duration (s)": round(g["server_time"].max() - g["server_time"].min(), 1),
            "Moisture mean/max": f"{g['moisture'].mean():.3f}/{g['moisture'].max():.3f}" if g["moisture"].notna().any() else "n/a",
            "Rain mean/max": f"{g['rain'].mean():.3f}/{g['rain'].max():.3f}" if g["rain"].notna().any() else "n/a",
            "Tilt max |deg|": f"{g['tilt_deg'].abs().max():.2f}" if g["tilt_deg"].notna().any() else "n/a",
            "Risk score mean/max": f"{g['risk_score'].mean():.3f}/{g['risk_score'].max():.3f}" if g["risk_score"].notna().any() else "n/a",
            "Highest state": max(known, key=RANK.get) if known else "UNKNOWN",
            "Final state": states.iloc[-1] if len(states) else "",
            "Samples with faults": int((g["faults"].fillna("[]") != "[]").sum()),
        })
    summ = pd.DataFrame(rows)
    summ.to_csv(out / "table_10_2_summary.csv", index=False)
    (out / "table_10_2_summary.tex").write_text(latex_table(summ, "Summary of experimental runs" + tag, "tab:exp-summary"))
    manifest["files"] += ["table_10_2_summary.csv", "table_10_2_summary.tex"]

    # ---- Table 10.3: transitions ----
    tr = events[events["kind"] == "STATE_CHANGE"].copy()
    if not args.allow_simulated:
        tr = tr[tr["source"] == "device"]
    trows = []
    for e in tr.itertuples():
        eid = int(e.experiment_id) if pd.notna(e.experiment_id) else -1
        t0 = exp_start.get(eid)
        trows.append({"Experiment": exp_name.get(eid, "(untagged)"), "Source": e.source,
                      "Transition": f"{e.from_state or 'start'} -> {e.to_state}",
                      "Time": e.server_time_iso, "t since start (s)": round(e.server_time - t0, 1) if t0 else "",
                      "Risk score": "" if pd.isna(e.risk_score) else round(e.risk_score, 3)})
    tdf = pd.DataFrame(trows, columns=["Experiment", "Source", "Transition", "Time", "t since start (s)", "Risk score"])
    tdf.to_csv(out / "table_10_3_transitions.csv", index=False)
    (out / "table_10_3_transitions.tex").write_text(latex_table(tdf, "Recorded risk-state transitions" + tag, "tab:transitions"))
    manifest["files"] += ["table_10_3_transitions.csv", "table_10_3_transitions.tex"]

    # ---- latency (device sample time -> server receive). Only device rows with NTP time; buffered rows separate ----
    lat = readings[(readings["source"] == "device") & readings["latency_ms"].notna()]
    live = lat[(lat["buffered"].fillna(0) == 0)]
    def stats(s):
        return None if s.empty else {"n": int(len(s)), "median_ms": float(s.median()), "p95_ms": float(s.quantile(0.95)),
                                     "max_ms": float(s.max()), "mean_ms": float(s.mean())}
    lat_json = {"definition": "server receive time - device sample time (device clock via NTP; includes queueing). "
                              "Does not measure sampling-to-LED latency, which needs a separate bench measurement.",
                "live_samples": stats(live["latency_ms"]), "all_samples_including_buffered": stats(lat["latency_ms"]),
                "inter_arrival_s_median": float(readings[readings["source"] == "device"]["server_time"].diff().median())
                if (readings["source"] == "device").sum() > 1 else None}
    (out / "latency.json").write_text(json.dumps(lat_json, indent=2))
    if lat_json["live_samples"]:
        ls = lat_json["live_samples"]
        ldf = pd.DataFrame([{"Samples": ls["n"], "Median (ms)": round(ls["median_ms"], 1), "95th pct (ms)": round(ls["p95_ms"], 1),
                             "Max (ms)": round(ls["max_ms"], 1)}])
        (out / "latency.tex").write_text(latex_table(ldf, "Device-to-backend latency (real device, NTP clock)", "tab:latency"))
        manifest["files"].append("latency.tex")
    else:
        manifest["still_missing"].append("Latency: no real device rows with NTP time (device_time_ms).")
    manifest["files"].append("latency.json")

    # ---- time-series plots per experiment ----
    for gid, g in readings.groupby("group"):
        name = exp_name.get(gid, "untagged")
        sim = (g["source"] == "simulated").any()
        one_dev = g["device_id"].nunique() == 1
        if one_dev and g["device_uptime_ms"].notna().all():
            t = (g["device_uptime_ms"] - g["device_uptime_ms"].min()) / 1000.0
            xlab = "device time since first sample (s)"
        else:
            t = g["server_time"] - g["server_time"].min()
            xlab = "server receive time since first sample (s)"
        fig, (a1, a2) = plt.subplots(2, 1, figsize=(9, 6), sharex=True)
        a1.plot(t, g["moisture"], label="moisture index m")
        a1.plot(t, g["rain"], label="rain activity r")
        a1.set_ylim(-0.02, 1.02)
        a1.set_ylabel("index (0-1)")
        a1b = a1.twinx()
        a1b.plot(t, g["tilt_deg"], color="#a0522d", label="tilt change (deg)")
        a1b.set_ylabel("tilt change (deg)")
        a1.legend(loc="upper left", fontsize=8)
        a1b.legend(loc="upper right", fontsize=8)
        a2.plot(t, g["risk_score"], color="black", label="risk score R")
        for k, c in (("TAU1", "#d9b800"), ("TAU2", "#f08a00"), ("TAU3", "#d32f2f")):
            a2.axhline(cfg[k], color=c, ls="--", lw=1, label=f"{k}={cfg[k]} (PROVISIONAL)")
        a2.set_ylim(-0.02, 1.02)
        a2.set_xlabel(xlab)
        a2.set_ylabel("R")
        a2.legend(fontsize=7, loc="upper left")
        fig.suptitle(f"{name} — {'SIMULATED' if sim else 'real device'} data", fontsize=11)
        watermark(fig, sim)
        fig.tight_layout(rect=(0, 0.03, 1, 0.97))
        safe = "".join(ch if ch.isalnum() else "_" for ch in str(name))[:40]
        fname = f"ts_{gid}_{safe}.png"
        fig.savefig(out / fname, dpi=140)
        plt.close(fig)
        manifest["files"].append(fname)

    if not (readings["source"] == "device").any():
        manifest["still_missing"].append("Only simulated rows were used: these outputs must NOT fill report [DATA] placeholders.")
    (out / "manifest.json").write_text(json.dumps(manifest, indent=2))
    print(json.dumps({k: manifest[k] for k in ("sources_included", "n_readings", "still_missing")}, indent=2))
    print(f"{len(manifest['files'])} files in {out}")
    return manifest


if __name__ == "__main__":
    main()
