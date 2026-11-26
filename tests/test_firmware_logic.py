"""Compiles and runs the host unit tests of firmware/landslide_monitor/ls_logic.h.

This verifies the hardware-independent firmware logic only (normalization,
ADC saturation handling, tilt geometry, filters, JSON output, backend response
validation, LED/buzzer mapping). It is not a hardware test.
"""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "tests" / "firmware_host" / "test_logic.cpp"
FW = ROOT / "firmware" / "landslide_monitor"


def compiler():
    for name in ("g++", "clang++"):
        if shutil.which(name):
            return name
    return None


@pytest.mark.skipif(compiler() is None, reason="no C++ compiler on PATH")
def test_firmware_logic_host_suite(tmp_path):
    exe = tmp_path / "fw_logic_test"
    build = subprocess.run([compiler(), "-std=c++11", "-Wall", "-Wextra", "-O1", "-D__USE_MINGW_ANSI_STDIO=1",
                            str(SRC), "-o", str(exe)], capture_output=True, text=True)
    assert build.returncode == 0, build.stderr
    run = subprocess.run([str(exe)], capture_output=True, text=True, timeout=60)
    assert run.returncode == 0, run.stdout + run.stderr
    assert "0 failures" in run.stdout


def test_firmware_uses_specified_gpio_and_endpoints():
    ino = (FW / "landslide_monitor.ino").read_text(encoding="utf-8")
    logic = (FW / "ls_logic.h").read_text(encoding="utf-8")
    pins = {"PIN_LED_RED": 19, "PIN_LED_BLUE": 21, "PIN_LED_YELLOW": 22, "PIN_LED_GREEN": 23, "PIN_SOIL_AO": 35,
            "PIN_SOIL_DO": 32, "PIN_WATER_S": 34, "PIN_VIB_DO": 15, "PIN_SDA": 25, "PIN_SCL": 26,
            "PIN_BUZZER1": 12, "PIN_BUZZER2": 13}
    for name, gpio in pins.items():
        assert f"static const int {name} = {gpio};" in ino, name
    for const in ("SOIL_DRY_ADC = 4090", "SOIL_WET_ADC = 921", "WATER_DRY_ADC = 0", "WATER_WET_ADC = 1725"):
        assert const in logic
    # no on-device risk formula and no manual calibration commands
    for forbidden in ("0.40", "riskScore", "soil_dry", "soil_wet", "rain_dry", "rain_wet", '"baseline"', '"ok"'):
        assert forbidden not in ino, forbidden
    for cmd in ("demo", "status", "raw", "test", "wifi", "json", "send", "help"):
        assert f'!strcmp(cmd, "{cmd}")' in ino, cmd


def test_firmware_telemetry_matches_feature_schema():
    """Every feature path in feature_schema.json is emitted by the firmware JSON builder."""
    from ml.features import SCHEMA

    ino = (FW / "landslide_monitor.ino").read_text(encoding="utf-8")
    for f in SCHEMA["features"]:
        section, key = f["path"].split(".")
        assert f'w.beginObject("{section}")' in ino
        assert f'"{key}"' in ino, f["path"]
    assert f'FEATURE_SCHEMA = "{SCHEMA["version"]}"' in ino
