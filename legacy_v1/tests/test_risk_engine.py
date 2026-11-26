"""Unit tests for the Python port of the firmware risk engine."""
import pytest

from backend.ino_config import extract_region, load_ino_config, pin_table
from backend.risk_engine import (CRITICAL, SAFE, UNKNOWN, WARNING, WATCH, RainDuration, RiskInputs, RiskMachine,
                                 RiskParams, classify, component, compute, mix, normalize_index)


@pytest.fixture
def p():
    return RiskParams.from_ino()


def test_config_parsed_from_ino_single_source():
    c = load_ino_config()
    # report Eq. 6.10 weights
    assert (c["W_MOISTURE"], c["W_RAIN"], c["W_TILT"]) == (0.40, 0.30, 0.30)
    assert abs(c["W_MOISTURE"] + c["W_RAIN"] + c["W_TILT"] - 1.0) < 1e-9
    assert c["TAU1"] < c["TAU2"] < c["TAU3"]
    # provisional values must be labelled as such in the firmware
    for k in ("TAU1", "TAU2", "TAU3", "BETA_M", "SAMPLE_INTERVAL_MS", "MA_WINDOW_N", "RATE_WINDOW_L", "RAIN_WET_THRESHOLD", "HYSTERESIS"):
        assert "PROVISIONAL" in c["_comments"][k], k
    assert c["RING_CAPACITY"] >= 300


def test_pin_map_matches_current_wiring():
    pins = {r["signal"]: r["gpio"] for r in pin_table()}
    assert pins["PIN_SOIL_AO"] == 34 and pins["PIN_RAIN_AO"] == 35
    assert pins["PIN_SOIL_DO"] == 27 and pins["PIN_RAIN_DO"] == 32
    assert pins["PIN_I2C_SDA"] == 25 and pins["PIN_I2C_SCL"] == 26
    assert [pins[k] for k in ("PIN_LED_SAFE", "PIN_LED_WATCH", "PIN_LED_WARNING", "PIN_LED_CRITICAL")] == [16, 17, 18, 19]
    assert pins["PIN_BUZZER"] == 23
    # analog inputs must be ADC1 (GPIO 32-39) so they work with Wi-Fi on
    assert 32 <= pins["PIN_SOIL_AO"] <= 39 and 32 <= pins["PIN_RAIN_AO"] <= 39
    # I2C pins are not reused
    others = [v for k, v in pins.items() if k.startswith("PIN_") and k not in ("PIN_I2C_SDA", "PIN_I2C_SCL")]
    assert 25 not in others and 26 not in others


def test_firmware_is_single_file():
    from backend.ino_config import INO_PATH
    folder = INO_PATH.parent
    code = [f.name for f in folder.iterdir() if f.suffix in (".h", ".cpp", ".c", ".hpp", ".ino")]
    assert sorted(code) == ["landslide_monitor.ino", "secrets.example.h"] or sorted(code) == [
        "landslide_monitor.ino", "secrets.example.h", "secrets.h"]
    assert "riskCompute" in extract_region("RISK_ENGINE")


def test_normalization_eq_6_1():
    # dry reading -> 0, wet reading -> 1, clamped outside, polarity-independent
    assert normalize_index(3000, 3000, 1400) == 0.0
    assert normalize_index(1400, 3000, 1400) == 1.0
    assert normalize_index(2200, 3000, 1400) == pytest.approx(0.5)
    assert normalize_index(3500, 3000, 1400) == 0.0
    assert normalize_index(500, 3000, 1400) == 1.0
    assert normalize_index(2200, 1400, 3000) == pytest.approx(0.5)  # reversed polarity still maps dry->0 wet->1
    assert normalize_index(1400, 1400, 3000) == 0.0


def test_component_eq_6_8():
    assert component(0.3, 0.4, 0.9) == 0.0
    assert component(0.65, 0.4, 0.9) == pytest.approx(0.5)
    assert component(1.2, 0.4, 0.9) == 1.0
    assert component(0.5, 0.5, 0.5) == 1.0  # degenerate thresholds -> step


def test_mix_eq_6_9():
    assert mix(0.5, 1.0, 0.3, True) == pytest.approx(0.65)
    assert mix(0.5, 1.0, 0.3, False) == 0.5


def test_classify_intervals(p):
    assert classify(p.tau1 - 1e-4, p) == SAFE
    assert classify(p.tau1, p) == WATCH
    assert classify(p.tau2, p) == WARNING
    assert classify(p.tau3, p) == CRITICAL
    assert classify(1.0, p) == CRITICAL


def test_weights_eq_6_10(p):
    out = compute(RiskInputs(m=0.9, dm=0.03, r=0.2, drS=0, tilt=0.5, tiltRate=0.01), p)
    assert out.compM == pytest.approx(1.0) and out.compR == 0 and out.compT == 0
    assert out.score == pytest.approx(0.40)
    assert out.usable == 3


def test_fault_exclusion_and_renormalization(p):
    full = compute(RiskInputs(m=0.9, dm=0.03, r=0.2, drS=0, tilt=0.5, tiltRate=0.01), p)
    no_tilt = compute(RiskInputs(m=0.9, dm=0.03, r=0.2, drS=0, tOk=False), p)
    assert no_tilt.compT == -1 and no_tilt.usable == 2
    assert no_tilt.score == pytest.approx(0.40 / 0.70, rel=1e-5)
    assert no_tilt.score > full.score


def test_no_components_never_safe(p):
    out = compute(RiskInputs(mOk=False, rOk=False, tOk=False), p)
    assert out.raw == UNKNOWN and out.score == -1 and out.usable == 0
    sm = RiskMachine(p)
    sm.step(compute(RiskInputs(), p), 0)
    assert sm.state == SAFE
    assert sm.step(out, 100) == UNKNOWN


def test_hysteresis_and_dwell(p):
    sm = RiskMachine(p)
    hi = compute(RiskInputs(m=1, dm=1, r=1, drS=1000, tilt=5, tiltRate=1), p)
    assert sm.step(hi, 0) == CRITICAL
    mid = compute(RiskInputs(m=0.65, dm=0.015, r=0.55, drS=300, tilt=2.25, tiltRate=0.045), p)
    assert mid.raw in (WATCH, WARNING)
    assert sm.step(mid, 1000) == CRITICAL           # dwell not met
    assert sm.step(mid, p.dwellMs + 1) < CRITICAL   # dwell met -> de-escalate
    assert sm.step(hi, p.dwellMs + 2) == CRITICAL   # escalation immediate


def test_rain_duration_eq_6_2():
    rd = RainDuration(0.3)
    assert rd.update(0.5, 1.0) == 1.0
    assert rd.update(0.5, 1.0) == 2.0
    assert rd.update(0.1, 1.0) == 0.0      # resets when below r_th
    assert rd.update(0.3, 1.0) == 1.0      # threshold inclusive
    assert rd.update(None, 1.0) == 0.0     # faulty sensor
