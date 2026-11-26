"""Pydantic request/response models. Field names match the firmware JSON."""
from __future__ import annotations

from typing import List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field

StateName = Literal["SAFE", "WATCH", "WARNING", "CRITICAL", "UNKNOWN"]
SensorStatus = Literal["OK", "UNCALIBRATED", "FAULT"]
HealthName = Literal["OK", "DEGRADED", "FAULT"]


class Reading(BaseModel):
    """One telemetry sample. Indices are normalized to [0, 1]; rates are per minute."""

    model_config = ConfigDict(extra="ignore")

    device_id: str = Field("unknown", max_length=64)
    seq: Optional[int] = Field(None, ge=0)
    source: Literal["device", "simulated"] = "device"
    fw_version: Optional[str] = Field(None, max_length=32)
    device_uptime_ms: Optional[int] = Field(None, ge=0)
    device_time_ms: Optional[float] = Field(None, ge=0)

    soil_raw: Optional[int] = Field(None, ge=0, le=4095)
    rain_raw: Optional[int] = Field(None, ge=0, le=4095)
    rain_do_wet: Optional[bool] = None

    moisture: Optional[float] = Field(None, ge=0, le=1, description="soil moisture index m in [0,1]")
    rain: Optional[float] = Field(None, ge=0, le=1, description="rain activity r in [0,1]")
    rain_duration_s: Optional[float] = Field(None, ge=0)
    tilt_deg: Optional[float] = Field(None, ge=-180, le=180, description="tilt change from baseline, degrees")
    tilt_abs_deg: Optional[float] = Field(None, ge=0, le=180)
    tilt_vec_change_deg: Optional[float] = Field(None, ge=0, le=180)
    moisture_rate_per_min: Optional[float] = Field(None, ge=-10, le=10, description="index per minute")
    tilt_rate_deg_per_min: Optional[float] = Field(None, ge=-1000, le=1000)

    comp_m: Optional[float] = Field(None, ge=0, le=1)
    comp_r: Optional[float] = Field(None, ge=0, le=1)
    comp_t: Optional[float] = Field(None, ge=0, le=1)
    risk_score: Optional[float] = Field(None, ge=0, le=1)
    risk_state: StateName = "UNKNOWN"
    raw_state: Optional[StateName] = None

    health: HealthName = "OK"
    soil_status: Optional[SensorStatus] = None
    rain_status: Optional[SensorStatus] = None
    mpu_status: Optional[SensorStatus] = None
    rates_ready: Optional[bool] = None
    faults: List[str] = Field(default_factory=list, max_length=32)

    dropped_count: Optional[int] = Field(None, ge=0)
    buffered: Optional[int] = Field(None, ge=0)
    wifi_rssi: Optional[int] = Field(None, ge=-127, le=10)

    scenario: Optional[str] = Field(None, max_length=64)
    dataset_label: Optional[str] = Field(None, max_length=16, description="label from a replayed dataset (simulated only)")


class ExperimentStart(BaseModel):
    name: str = Field(..., min_length=1, max_length=80)
    scenario: Literal["dry", "gradual_wetting", "heavy_rain", "combined_stress", "other"] = "other"
    notes: Optional[str] = Field(None, max_length=500)
