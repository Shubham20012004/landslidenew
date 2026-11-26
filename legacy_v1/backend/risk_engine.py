"""Python port of the firmware's pure risk engine (report Chapter 6).

Mirrors the //@@BEGIN_RISK_ENGINE region of landslide_monitor.ino operation by
operation, rounding to IEEE float32 after each step so the results match the
firmware's single-precision arithmetic. Default parameters are read from the
.ino CONFIG region, so there is a single source of truth.

Used by: tests (cross-check with the C++ engine), simulator (rule state for
simulated data) and ML label comparison. The ESP32 remains the authoritative
place where alerts are computed for real hardware.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, fields

import numpy as np

from .ino_config import load_ino_config

UNKNOWN, SAFE, WATCH, WARNING, CRITICAL = -1, 0, 1, 2, 3
STATE_NAMES = {UNKNOWN: "UNKNOWN", SAFE: "SAFE", WATCH: "WATCH", WARNING: "WARNING", CRITICAL: "CRITICAL"}
STATE_CODES = {v: k for k, v in STATE_NAMES.items()}
ORDERED_STATES = ["SAFE", "WATCH", "WARNING", "CRITICAL"]


def f32(x: float) -> float:
    return float(np.float32(x))


@dataclass
class RiskParams:
    wM: float
    wR: float
    wT: float
    betaM: float
    betaR: float
    betaT: float
    mLo: float
    mHi: float
    dmLo: float
    dmHi: float
    rLo: float
    rHi: float
    drLo: float
    drHi: float
    tLo: float
    tHi: float
    trLo: float
    trHi: float
    tau1: float
    tau2: float
    tau3: float
    hyst: float
    dwellMs: int

    @classmethod
    def from_ino(cls, ino_path: str | None = None) -> "RiskParams":
        c = load_ino_config(ino_path)
        return cls(
            wM=f32(c["W_MOISTURE"]), wR=f32(c["W_RAIN"]), wT=f32(c["W_TILT"]),
            betaM=f32(c["BETA_M"]), betaR=f32(c["BETA_R"]), betaT=f32(c["BETA_T"]),
            mLo=f32(c["M_LO"]), mHi=f32(c["M_HI"]), dmLo=f32(c["DM_LO"]), dmHi=f32(c["DM_HI"]),
            rLo=f32(c["R_LO"]), rHi=f32(c["R_HI"]), drLo=f32(c["DR_LO_S"]), drHi=f32(c["DR_HI_S"]),
            tLo=f32(c["T_LO_DEG"]), tHi=f32(c["T_HI_DEG"]), trLo=f32(c["TR_LO"]), trHi=f32(c["TR_HI"]),
            tau1=f32(c["TAU1"]), tau2=f32(c["TAU2"]), tau3=f32(c["TAU3"]),
            hyst=f32(c["HYSTERESIS"]), dwellMs=int(c["MIN_DWELL_MS"]),
        )

    @classmethod
    def from_dict(cls, d: dict) -> "RiskParams":
        kw = {}
        for fld in fields(cls):
            kw[fld.name] = int(d[fld.name]) if fld.name == "dwellMs" else f32(d[fld.name])
        return cls(**kw)

    def as_list(self) -> list:
        return [getattr(self, f.name) for f in fields(self)]

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class RiskInputs:
    m: float = 0.0
    dm: float = 0.0
    r: float = 0.0
    drS: float = 0.0
    tilt: float = 0.0
    tiltRate: float = 0.0
    mOk: bool = True
    rOk: bool = True
    tOk: bool = True
    ratesReady: bool = True


@dataclass
class RiskOutputs:
    compM: float
    compR: float
    compT: float
    score: float
    usable: int
    raw: int


def normalize_index(x: float, dry: int, wet: int) -> float:
    """Eq. 6.1 (polarity-independent, as in firmware normalizeIndex): (dry - x)/(dry - wet) clamped to [0,1]."""
    v = f32(f32(f32(dry) - f32(x)) / f32(f32(dry) - f32(wet)))
    return min(1.0, max(0.0, v))


def component(x: float, lo: float, hi: float) -> float:
    """Eq. 6.8: C(x; lo, hi) = min(1, max(0, (x - lo)/(hi - lo)))."""
    x, lo, hi = f32(x), f32(lo), f32(hi)
    if not hi > lo:
        return 1.0 if x >= hi else 0.0
    v = f32(f32(x - lo) / f32(hi - lo))
    return min(1.0, max(0.0, v))


def mix(level_c: float, trend_c: float, beta: float, trend_ready: bool) -> float:
    """Eq. 6.9 level/trend mix; trend unavailable -> level only."""
    if not trend_ready:
        return level_c
    return f32(f32(f32(1.0 - beta) * level_c) + f32(beta * trend_c))


def classify(score: float, p: RiskParams) -> int:
    """Eq. 6.11 with explicit intervals."""
    if score < p.tau1:
        return SAFE
    if score < p.tau2:
        return WATCH
    if score < p.tau3:
        return WARNING
    return CRITICAL


def compute(i: RiskInputs, p: RiskParams) -> RiskOutputs:
    """Eq. 6.10 weighted sum, re-normalized over usable (non-faulty, calibrated) components."""
    num, den, usable = 0.0, 0.0, 0
    cM = cR = cT = -1.0
    if i.mOk:
        cM = mix(component(i.m, p.mLo, p.mHi), component(i.dm, p.dmLo, p.dmHi), p.betaM, i.ratesReady)
        num = f32(num + f32(p.wM * cM)); den = f32(den + p.wM); usable += 1
    if i.rOk:
        cR = mix(component(i.r, p.rLo, p.rHi), component(i.drS, p.drLo, p.drHi), p.betaR, True)
        num = f32(num + f32(p.wR * cR)); den = f32(den + p.wR); usable += 1
    if i.tOk:
        t = abs(f32(i.tilt))
        cT = mix(component(t, p.tLo, p.tHi), component(i.tiltRate, p.trLo, p.trHi), p.betaT, i.ratesReady)
        num = f32(num + f32(p.wT * cT)); den = f32(den + p.wT); usable += 1
    if den > 0:
        score = f32(num / den)
        raw = classify(score, p)
    else:
        score, raw = -1.0, UNKNOWN
    return RiskOutputs(cM, cR, cT, score, usable, raw)


def lower_bound(state: int, p: RiskParams) -> float:
    return {WATCH: p.tau1, WARNING: p.tau2, CRITICAL: p.tau3}.get(state, -1.0e9)


class RiskMachine:
    """Escalate immediately; de-escalate only below (lower bound - hysteresis) after the dwell time."""

    def __init__(self, params: RiskParams):
        self.p = params
        self.state = UNKNOWN
        self.last_change_ms = 0

    def step(self, out: RiskOutputs, now_ms: int) -> int:
        p = self.p
        if out.raw == UNKNOWN:
            if self.state != UNKNOWN:
                self.state, self.last_change_ms = UNKNOWN, now_ms
            return self.state
        if self.state == UNKNOWN or out.raw > self.state:
            self.state, self.last_change_ms = out.raw, now_ms
            return self.state
        if out.raw < self.state:
            below = out.score < f32(lower_bound(self.state, p) - p.hyst)
            dwell_ok = ((now_ms - self.last_change_ms) & 0xFFFFFFFF) >= p.dwellMs
            if below and dwell_ok:
                target = classify(f32(out.score + p.hyst), p)
                if target < self.state:
                    self.state, self.last_change_ms = target, now_ms
        return self.state


class RainDuration:
    """Eq. 6.2: d_r accumulates dt while r >= r_th, resets to 0 otherwise."""

    def __init__(self, threshold: float):
        self.th = threshold
        self.d = 0.0

    def update(self, r: float | None, dt_s: float) -> float:
        if r is None:
            self.d = 0.0
        else:
            self.d = self.d + dt_s if r >= self.th else 0.0
        return self.d


def default_params() -> RiskParams:
    return RiskParams.from_ino()
