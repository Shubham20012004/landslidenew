"""Dataset loading and validation for Random Forest training.

Data sources are kept strictly separate:

* experimental - real ESP32 readings stored by the backend (origin='device')
  labelled by documented experiment annotations (time ranges). Model
  predictions are never used as labels.
* synthetic    - generated demonstration data (datasets/synthetic/), labels
  defined by the generator's scenario script. Software demonstration only.
* historical   - NASA Global Landslide Catalog event records. Event-level data
  with no sensor measurements: never used to train the sensor model.
* test fixtures - tests/fixtures, only for automated tests.
"""
from __future__ import annotations

import hashlib
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import pandas as pd

from .features import CLASSES, FEATURE_NAMES, FEATURE_RANGE

REPO_ROOT = Path(__file__).resolve().parents[1]
SYNTHETIC_DEFAULT = REPO_ROOT / "datasets" / "synthetic" / "four_sensor_demo_v1.csv"

# A reading is eligible for training/inference only if the device was in normal
# monitoring with a valid calibration and no sensor reported FAULT.
ELIGIBLE_SQL = (
    "r.op_state = 'MONITORING' AND r.calibration_status = 'OK'"
    " AND r.soil_health != 'FAULT' AND r.water_health != 'FAULT' AND r.mpu_health = 'OK'"
    " AND " + " AND ".join(f"r.{f} IS NOT NULL" for f in FEATURE_NAMES)
)

LABEL_JOIN_SQL = (
    "JOIN annotations a ON a.device_id = r.device_id"
    " AND r.received_at >= a.start_time AND r.received_at <= a.end_time"
    " AND (a.session_id IS NULL OR a.session_id = r.session_id)"
)


def labeled_readings_sql(extra_where: str = "") -> str:
    """Real device readings with exactly one annotation label (conflicting
    overlapping labels make a reading ambiguous; it is excluded)."""
    cols = ", ".join(f"r.{f}" for f in FEATURE_NAMES)
    return (
        f"SELECT r.id AS reading_pk, r.reading_id, r.device_id, r.session_id, r.received_at, {cols},"
        " MIN(a.label) AS label, MIN(a.id) AS annotation_id"
        f" FROM readings r {LABEL_JOIN_SQL}"
        f" WHERE r.origin = 'device' AND {ELIGIBLE_SQL}{extra_where}"
        " GROUP BY r.id HAVING COUNT(DISTINCT a.label) = 1 ORDER BY r.id"
    )


def dataset_summary(conn: sqlite3.Connection) -> dict:
    """Counts of real readings that are labelled, unlabelled or ambiguous."""
    total = conn.execute("SELECT COUNT(*) FROM readings WHERE origin='device'").fetchone()[0]
    eligible = conn.execute(f"SELECT COUNT(*) FROM readings r WHERE r.origin='device' AND {ELIGIBLE_SQL}").fetchone()[0]
    per_label = conn.execute(
        f"SELECT label, COUNT(*), COUNT(DISTINCT session_id) FROM ({labeled_readings_sql()}) GROUP BY label"
    ).fetchall()
    ambiguous = conn.execute(
        f"SELECT COUNT(*) FROM (SELECT r.id FROM readings r {LABEL_JOIN_SQL}"
        f" WHERE r.origin='device' AND {ELIGIBLE_SQL} GROUP BY r.id HAVING COUNT(DISTINCT a.label) > 1)"
    ).fetchone()[0]
    labeled = sum(n for _, n, _ in per_label)
    sessions_labeled = conn.execute(
        f"SELECT COUNT(DISTINCT session_id) FROM ({labeled_readings_sql()})"
    ).fetchone()[0]
    return {
        "device_readings_total": total,
        "eligible_readings": eligible,
        "labeled_readings": labeled,
        "unlabeled_eligible_readings": eligible - labeled - ambiguous,
        "ambiguous_readings": ambiguous,
        "labeled_sessions": sessions_labeled,
        "per_class": {c: {"readings": 0, "sessions": 0} for c in CLASSES}
        | {label: {"readings": n, "sessions": s} for label, n, s in per_label},
    }


@dataclass
class DatasetReport:
    source: str
    path: str
    sha256: str
    rows_loaded: int
    rows_used: int = 0
    dropped_missing: int = 0
    dropped_out_of_range: int = 0
    dropped_duplicates: int = 0
    dropped_bad_label: int = 0
    missing_per_feature: dict = field(default_factory=dict)
    class_counts: dict = field(default_factory=dict)
    sessions: int = 0
    sessions_per_class: dict = field(default_factory=dict)
    notes: list = field(default_factory=list)

    def as_dict(self) -> dict:
        return self.__dict__.copy()


def _sha256_frame(df: pd.DataFrame) -> str:
    return hashlib.sha256(pd.util.hash_pandas_object(df, index=False).values.tobytes()).hexdigest()


def load_synthetic(path: Path | str = SYNTHETIC_DEFAULT) -> tuple[pd.DataFrame, str]:
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"{path} not found. Generate it with: python -m ml.synthetic")
    df = pd.read_csv(path)
    return df, hashlib.sha256(path.read_bytes()).hexdigest()


def load_experimental(db_path: Path | str) -> tuple[pd.DataFrame, str]:
    db_path = Path(db_path)
    if not db_path.exists():
        raise FileNotFoundError(f"database {db_path} not found")
    conn = sqlite3.connect(f"file:{db_path.as_posix()}?mode=ro", uri=True)
    try:
        has_tables = conn.execute("SELECT 1 FROM sqlite_master WHERE name='annotations'").fetchone()
        if not has_tables:
            raise RuntimeError("database has no annotations table; start the backend once to migrate it")
        df = pd.read_sql_query(labeled_readings_sql(), conn)
    finally:
        conn.close()
    return df, _sha256_frame(df) if len(df) else hashlib.sha256(b"").hexdigest()


def validate_and_clean(df: pd.DataFrame, source: str, path: str, sha256: str) -> tuple[pd.DataFrame, DatasetReport]:
    """Schema validation and cleaning. Nothing is imputed: incomplete rows are dropped."""
    required = ["session_id", "label", *FEATURE_NAMES]
    missing_cols = [c for c in required if c not in df.columns]
    if missing_cols:
        raise ValueError(f"dataset is missing columns: {missing_cols}")
    report = DatasetReport(source=source, path=path, sha256=sha256, rows_loaded=len(df))
    df = df.copy()
    for f in FEATURE_NAMES:
        df[f] = pd.to_numeric(df[f], errors="coerce")
        df.loc[~np.isfinite(df[f].astype(float)), f] = np.nan
    report.missing_per_feature = {f: int(df[f].isna().sum()) for f in FEATURE_NAMES}
    before = len(df)
    df = df.dropna(subset=FEATURE_NAMES)
    report.dropped_missing = before - len(df)

    in_range = np.ones(len(df), dtype=bool)
    for f, (lo, hi) in FEATURE_RANGE.items():
        in_range &= df[f].between(lo, hi).to_numpy()
    report.dropped_out_of_range = int((~in_range).sum())
    df = df[in_range]

    before = len(df)
    df = df[df["label"].isin(CLASSES)]
    report.dropped_bad_label = before - len(df)

    before = len(df)
    df = df.drop_duplicates(subset=["session_id", *FEATURE_NAMES, "label"])
    report.dropped_duplicates = before - len(df)

    report.rows_used = len(df)
    report.class_counts = {c: int((df["label"] == c).sum()) for c in CLASSES}
    report.sessions = int(df["session_id"].nunique())
    report.sessions_per_class = {c: int(df.loc[df["label"] == c, "session_id"].nunique()) for c in CLASSES}
    return df.reset_index(drop=True), report


def sufficiency_problems(report: DatasetReport, min_per_class: int = 30, min_sessions_per_class: int = 2,
                         min_sessions: int = 5) -> list[str]:
    """Reasons the dataset cannot support a meaningful 4-class model (empty list = OK)."""
    problems = []
    if report.sessions < min_sessions:
        problems.append(f"only {report.sessions} labelled sessions (need >= {min_sessions})")
    for c in CLASSES:
        n, s = report.class_counts.get(c, 0), report.sessions_per_class.get(c, 0)
        if n < min_per_class:
            problems.append(f"class {c}: {n} readings (need >= {min_per_class})")
        if s < min_sessions_per_class:
            problems.append(f"class {c}: present in {s} sessions (need >= {min_sessions_per_class} for a grouped split)")
    return problems
