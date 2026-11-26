"""Historical analysis of the NASA Global Landslide Catalog (GLC).

    python historical/analyze_glc.py --input path/to/Global_Landslide_Catalog_Export.csv

WHERE TO GET THE DATA (not bundled, never fabricated or substituted):
  NASA Open Data Portal -> "Global Landslide Catalog"
  https://data.nasa.gov/dataset/global-landslide-catalog  (report reference [14])
  Download the CSV export and pass its path with --input. Record the download date.

The GLC is EVENT data (one row per reported landslide). It contains no soil
moisture, tilt or rain-sensor time series, so it is used for context only and
is never used to train or evaluate the sensor Random Forest (report Sec. 7.8.1).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import pandas as pd  # noqa: E402

REPO = Path(__file__).resolve().parents[1]
CANDIDATES = {
    "date": ["event_date", "date", "event_time", "eventdate"],
    "trigger": ["landslide_trigger", "trigger"],
    "country": ["country_name", "countryname", "country"],
    "fatalities": ["fatality_count", "fatalities"],
    "injuries": ["injury_count", "injuries"],
    "size": ["landslide_size", "size"],
    "category": ["landslide_category", "landslide_type", "category"],
}
REPORTED_SHARES = {"rain": 38, "monsoon": 18, "earthquake": 10, "construction": 6}  # report Table 7.2, [RE-VERIFY]
NOTE = ("Event-level catalogue data (reported events). Not continuous sensor measurements; not used to train the "
        "sensor model. Counts reflect reporting bias and the catalogue's rainfall-trigger focus.")


def pick(df: pd.DataFrame, key: str) -> str | None:
    lower = {c.lower().strip(): c for c in df.columns}
    for cand in CANDIDATES[key]:
        if cand in lower:
            return lower[cand]
    return None


def bar(series: pd.Series, title: str, path: Path, xlabel: str, horizontal=True):
    fig, ax = plt.subplots(figsize=(7, 4))
    if horizontal:
        s = series.iloc[::-1]
        ax.barh(s.index.astype(str), s.values, color="#4a78b5")
        ax.set_xlabel(xlabel)
    else:
        ax.bar(series.index.astype(str), series.values, color="#4a78b5")
        ax.set_ylabel(xlabel)
    ax.set_title(title, fontsize=10)
    fig.text(0.5, 0.01, "Historical event records (NASA GLC) — not sensor data", ha="center", fontsize=8, color="#555")
    fig.tight_layout(rect=(0, 0.04, 1, 1))
    fig.savefig(path, dpi=150)
    plt.close(fig)


def analyze(input_path: Path, out: Path, top_n: int = 15) -> dict:
    out.mkdir(parents=True, exist_ok=True)
    df = pd.read_csv(input_path, low_memory=False)
    cols = {k: pick(df, k) for k in CANDIDATES}
    summary: dict = {
        "input_file": input_path.name,
        "input_sha256": hashlib.sha256(input_path.read_bytes()).hexdigest(),
        "analysis_date": time.strftime("%Y-%m-%d %H:%M:%S %z"),
        "n_events": int(len(df)),
        "columns_used": cols,
        "columns_available": list(df.columns),
        "note": NOTE,
        "figures": [],
    }
    if cols["date"]:
        d = pd.to_datetime(df[cols["date"]], errors="coerce", utc=True)
        summary["date_parse_failures"] = int(d.isna().sum())
        summary["date_range"] = {"min": str(d.min().date()) if d.notna().any() else None,
                                 "max": str(d.max().date()) if d.notna().any() else None}
        monthly = d.dt.month.value_counts().reindex(range(1, 13), fill_value=0)
        summary["monthly_counts"] = {str(k): int(v) for k, v in monthly.items()}
        summary["yearly_counts"] = {str(int(k)): int(v) for k, v in d.dt.year.value_counts().sort_index().items()}
        months = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
        bar(pd.Series(monthly.values, index=months), "Events by month (all years)", out / "monthly_seasonality.png",
            "events", horizontal=False)
        summary["figures"].append("monthly_seasonality.png")
    if cols["trigger"]:
        trig = df[cols["trigger"]].fillna("unknown").astype(str).str.strip().str.lower()
        counts = trig.value_counts()
        share = (counts / counts.sum() * 100).round(2)
        summary["trigger_counts"] = {k: int(v) for k, v in counts.items()}
        summary["trigger_share_percent"] = {k: float(v) for k, v in share.items()}
        summary["report_trigger_shares_recheck"] = {
            k: {"reported_percent": v, "recomputed_percent": float(share.get(k, 0.0)),
                "note": "category name must match exactly; check trigger_counts for spelling variants"}
            for k, v in REPORTED_SHARES.items()}
        bar(counts.head(top_n), f"Reported landslide triggers (top {top_n})", out / "trigger_distribution.png", "events")
        summary["figures"].append("trigger_distribution.png")
    if cols["country"]:
        c = df[cols["country"]].fillna("unknown").astype(str).str.strip().value_counts()
        summary["top_countries"] = {k: int(v) for k, v in c.head(top_n).items()}
        summary["n_countries"] = int(c.index.nunique())
        bar(c.head(top_n), f"Events by country (top {top_n})", out / "top_countries.png", "events")
        summary["figures"].append("top_countries.png")
    for key in ("fatalities", "injuries"):
        if cols[key]:
            v = pd.to_numeric(df[cols[key]], errors="coerce")
            summary[key] = {"total": float(v.sum()), "events_with_value": int(v.notna().sum()),
                            "missing": int(v.isna().sum()), f"events_with_{key}": int((v > 0).sum()),
                            "mean": float(v.mean()) if v.notna().any() else None,
                            "median": float(v.median()) if v.notna().any() else None,
                            "max": float(v.max()) if v.notna().any() else None}
    if cols["fatalities"]:
        v = pd.to_numeric(df[cols["fatalities"]], errors="coerce").dropna()
        v = v[v > 0]
        if len(v):
            fig, ax = plt.subplots(figsize=(7, 4))
            ax.hist(v, bins=40, log=True, color="#a0522d")
            ax.set_xlabel("reported fatalities per event (events with >= 1)")
            ax.set_ylabel("events (log scale)")
            ax.set_title("Fatality distribution", fontsize=10)
            fig.tight_layout()
            fig.savefig(out / "fatalities_hist.png", dpi=150)
            plt.close(fig)
            summary["figures"].append("fatalities_hist.png")
    for key in ("size", "category"):
        if cols[key]:
            s = df[cols[key]].fillna("unknown").astype(str).str.strip().str.lower().value_counts()
            summary[f"{key}_counts"] = {k: int(v) for k, v in s.items()}
    (out / "summary.json").write_text(json.dumps(summary, indent=2))
    return summary


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--input", required=True, help="NASA Global Landslide Catalog CSV export")
    ap.add_argument("--out", default=str(REPO / "historical" / "outputs"))
    ap.add_argument("--top", type=int, default=15)
    args = ap.parse_args(argv)
    p = Path(args.input)
    if not p.exists():
        print(f"Input not found: {p}\nDownload the CSV from https://data.nasa.gov/dataset/global-landslide-catalog "
              "and pass its path. No substitute dataset is used.", file=sys.stderr)
        raise SystemExit(2)
    s = analyze(p, Path(args.out), args.top)
    print(f"{s['n_events']} events from {s['input_file']} analysed on {s['analysis_date']}")
    print(f"columns used: {s['columns_used']}")
    if "trigger_share_percent" in s:
        print("top triggers (%):", dict(list(s["trigger_share_percent"].items())[:6]))
    print(f"outputs: {args.out}")


if __name__ == "__main__":
    main()
