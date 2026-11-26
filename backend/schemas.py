"""Telemetry contract (schema 2.x) and API request models.

Validation ranges are physical limits of the sensors and of the firmware's
feature definitions, not "expected" values: e.g. a 180 deg flip of the model
within one second gives a tilt rate of ~10,800 deg/min, so the limit is
+-20,000 deg/min. Valid measurements are never clipped to pass validation.
Unknown extra fields are ignored for validation but preserved in the stored
original payload.
"""
from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, model_validator

Health = Literal["OK", "SUSPECT", "FAULT", "NOT_CALIBRATED"]
RiskClass = Literal["SAFE", "WATCH", "WARNING", "CRITICAL"]
OpState = Literal["INITIALIZING", "CALIBRATING", "MONITORING", "CALIBRATION_FAILED"]
CalStatus = Literal["NOT_STARTED", "IN_PROGRESS", "OK", "FAILED"]

ID_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,79}$"
Flag = Annotated[str, Field(max_length=40, pattern=r"^[A-Z0-9_]+$")]
Flags = Annotated[list[Flag], Field(default_factory=list, max_length=16)]


def _opt(lo: float, hi: float):
    return Field(None, ge=lo, le=hi)


class _In(BaseModel):
    model_config = ConfigDict(extra="ignore", allow_inf_nan=False, populate_by_name=True)


class SoilIn(_In):
    raw: Optional[int] = _opt(0, 4095)
    filt: Optional[float] = _opt(0, 4095)
    do: Optional[int] = _opt(0, 1)
    moisture: Optional[float] = _opt(0, 1)
    pct: Optional[float] = _opt(0, 100)
    rate_per_min: Optional[float] = _opt(-120, 120)
    mean_30s: Optional[float] = _opt(0, 1)
    std_30s: Optional[float] = _opt(0, 1)
    health: Health
    flags: Flags


class WaterIn(_In):
    raw: Optional[int] = _opt(0, 4095)
    filt: Optional[float] = _opt(0, 4095)
    wetness: Optional[float] = _opt(0, 1)
    pct: Optional[float] = _opt(0, 100)
    wet_s: Optional[float] = _opt(0, 1e7)
    rate_per_min: Optional[float] = _opt(-120, 120)
    mean_30s: Optional[float] = _opt(0, 1)
    health: Health
    flags: Flags


class MpuIn(_In):
    ax: Optional[float] = _opt(-16, 16)
    ay: Optional[float] = _opt(-16, 16)
    az: Optional[float] = _opt(-16, 16)
    gx: Optional[float] = _opt(-2500, 2500)
    gy: Optional[float] = _opt(-2500, 2500)
    gz: Optional[float] = _opt(-2500, 2500)
    incl0_deg: Optional[float] = _opt(0, 180)
    incl_deg: Optional[float] = _opt(0, 180)
    tilt_deg: Optional[float] = _opt(0, 180)
    incl_change_deg: Optional[float] = _opt(-180, 180)
    roll_deg: Optional[float] = _opt(-180, 180)
    pitch_deg: Optional[float] = _opt(-180, 180)
    tilt_rate_dpm: Optional[float] = _opt(-20000, 20000)
    acc_mag_g: Optional[float] = _opt(0, 30)
    acc_dyn_g: Optional[float] = _opt(0, 30)
    gyro_mag_dps: Optional[float] = _opt(0, 3500)
    temp_c: Optional[float] = _opt(-60, 150)
    health: Health
    flags: Flags


class VibIn(_In):
    do: Optional[int] = _opt(0, 1)
    transitions: Optional[int] = _opt(0, 1e8)
    events: Optional[int] = _opt(0, 1e8)
    window_ms: Optional[int] = _opt(0, 3.6e6)
    eps: Optional[float] = _opt(0, 1e5)
    bg_eps: Optional[float] = _opt(0, 1e5)
    above_bg_eps: Optional[float] = _opt(0, 1e5)
    ev_5s: Optional[int] = _opt(0, 3e6)
    ev_10s: Optional[int] = _opt(0, 3e6)
    ev_30s: Optional[int] = _opt(0, 3e6)
    trend_eps: Optional[float] = _opt(-1e5, 1e5)
    health: Health = "OK"


class CalIn(_In):
    status: CalStatus
    trigger: Optional[str] = Field(None, max_length=16)
    error: Optional[str] = Field(None, max_length=40)
    attempts: int = Field(0, ge=0, le=100)
    completed_uptime_ms: Optional[int] = Field(None, ge=0)
    gravity0: Optional[list[float]] = Field(None, min_length=3, max_length=3)
    gyro_bias_dps: Optional[list[float]] = Field(None, min_length=3, max_length=3)
    incl0_deg: Optional[float] = _opt(0, 180)
    accel_std_g: Optional[float] = _opt(0, 30)
    gyro_std_dps: Optional[float] = _opt(0, 3500)
    vib_bg_eps: Optional[float] = _opt(0, 1e5)
    soil_raw0: Optional[int] = _opt(-1, 4095)
    water_raw0: Optional[int] = _opt(-1, 4095)
    prev_incl0_deg: Optional[float] = _opt(0, 180)
    prev_shift_deg: Optional[float] = _opt(0, 180)
    nvs_saved: Optional[bool] = None
    mount_axis: Optional[str] = Field(None, max_length=3)
    samples: Optional[int] = Field(None, ge=0, le=100000)


class BufferIn(_In):
    queued: int = Field(0, ge=0, le=100000)
    dropped: int = Field(0, ge=0)


class TelemetryIn(_In):
    telemetry_schema: str = Field(..., alias="schema", pattern=r"^2\.\d+$")
    feature_schema: str = Field(..., max_length=16)
    origin: Literal["device", "simulated"] = "device"
    device_id: str = Field(..., pattern=ID_PATTERN)
    fw: str = Field(..., max_length=32)
    boot_id: str = Field(..., pattern=ID_PATTERN)
    session_id: str = Field(..., pattern=ID_PATTERN)
    reading_id: str = Field(..., pattern=ID_PATTERN)
    seq: int = Field(..., ge=0, le=2**32)
    uptime_ms: int = Field(..., ge=0, le=2**32)
    epoch_ms: Optional[int] = Field(None, ge=0)
    replay: bool = False
    op_state: OpState
    rssi: Optional[int] = Field(None, ge=-127, le=0)
    faults: Annotated[list[Flag], Field(default_factory=list, max_length=32)]
    buffer: Optional[BufferIn] = None
    cal: Optional[CalIn] = None
    soil: SoilIn
    water: WaterIn
    mpu: MpuIn
    vib: VibIn

    @model_validator(mode="after")
    def _ids_consistent(self) -> "TelemetryIn":
        if self.reading_id == self.session_id:
            raise ValueError("reading_id must differ from session_id")
        return self


class AnnotationIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    device_id: str = Field(..., pattern=ID_PATTERN)
    session_id: Optional[str] = Field(None, pattern=ID_PATTERN)
    start: datetime
    end: datetime
    label: RiskClass
    notes: Optional[str] = Field(None, max_length=500)
    author: Optional[str] = Field(None, max_length=64)

    @model_validator(mode="after")
    def _range(self) -> "AnnotationIn":
        if self.start.tzinfo is None or self.end.tzinfo is None:
            raise ValueError("start and end must include a timezone (e.g. 2026-11-26T10:00:00Z)")
        if self.end <= self.start:
            raise ValueError("end must be after start")
        if (self.end - self.start).total_seconds() > 7 * 24 * 3600:
            raise ValueError("an annotation may cover at most 7 days")
        return self
