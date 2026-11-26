"""Shared test vectors: Python port and the C++ firmware engine must give identical states.

The C++ side compiles the CONFIG + RISK_ENGINE regions extracted from the .ino
(no second copy of firmware logic) with tests/firmware_host/harness_main.cpp.
Skipped (not failed) if no C++ compiler is installed; the skip reason says so.
"""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

from backend.ino_config import INO_PATH, extract_region
from backend.risk_engine import RiskInputs, RiskMachine, RiskParams, compute

HERE = Path(__file__).parent
VECTORS = json.loads((HERE / "vectors" / "risk_vectors.json").read_text())
TOL = VECTORS["tolerance"]
PARAM_ORDER = ["wM", "wR", "wT", "betaM", "betaR", "betaT", "mLo", "mHi", "dmLo", "dmHi", "rLo", "rHi", "drLo", "drHi",
               "tLo", "tHi", "trLo", "trHi", "tau1", "tau2", "tau3", "hyst", "dwellMs"]
IN_ORDER = ["m", "dm", "r", "drS", "tilt", "tiltRate", "mOk", "rOk", "tOk", "ratesReady", "nowMs"]
OUT_KEYS = ["compM", "compR", "compT", "score", "usable", "raw", "state"]


def params_for(case):
    return RiskParams.from_dict(VECTORS["param_sets"][case["params"]])


def python_run(case):
    p = params_for(case)
    sm = RiskMachine(p)
    rows = []
    for step in case["steps"]:
        i = step["in"]
        out = compute(RiskInputs(**{k: i[k] for k in IN_ORDER if k != "nowMs"}), p)
        st = sm.step(out, i["nowMs"])
        rows.append({"compM": out.compM, "compR": out.compR, "compT": out.compT, "score": out.score,
                     "usable": out.usable, "raw": out.raw, "state": st})
    return rows


def check(rows, case, impl):
    for k, (row, step) in enumerate(zip(rows, case["steps"])):
        for key, exp in step["expect"].items():
            if key.startswith("_"):
                continue
            got = row[key]
            if key in ("usable", "raw", "state"):
                assert got == exp, f"{impl} {case['name']} step {k} {key}: got {got} expected {exp}"
            else:
                assert abs(got - exp) <= TOL, f"{impl} {case['name']} step {k} {key}: got {got} expected {exp}"


@pytest.mark.parametrize("case", VECTORS["cases"], ids=[c["name"] for c in VECTORS["cases"]])
def test_python_engine_vectors(case):
    check(python_run(case), case, "python")


def _compiler():
    return shutil.which("g++") or shutil.which("clang++")


@pytest.fixture(scope="module")
def harness(tmp_path_factory):
    cxx = _compiler()
    if not cxx:
        pytest.skip("No C++ compiler (g++/clang++) found: firmware-engine cross-check NOT performed")
    d = tmp_path_factory.mktemp("fwhost")
    (d / "extracted_engine.inc").write_text(
        f"// extracted from {INO_PATH.name}\n" + extract_region("CONFIG") + "\n" + extract_region("RISK_ENGINE"))
    shutil.copy(HERE / "firmware_host" / "harness_main.cpp", d / "harness_main.cpp")
    exe = d / "harness.exe"
    flags = ["-std=c++11", "-O0", "-ffp-contract=off"]
    probe = subprocess.run([cxx, "-dumpmachine"], capture_output=True, text=True).stdout
    if "i686" in probe or "mingw32" in probe and "x86_64" not in probe:
        flags += ["-msse2", "-mfpmath=sse"]  # avoid x87 extended precision on 32-bit MinGW
    r = subprocess.run([cxx, *flags, "-I", str(d), str(d / "harness_main.cpp"), "-o", str(exe)],
                       capture_output=True, text=True)
    assert r.returncode == 0, f"C++ harness failed to compile:\n{r.stderr}"
    return exe


def cpp_run(exe, case):
    p = VECTORS["param_sets"][case["params"]]
    lines = ["P " + " ".join(str(p[k]) for k in PARAM_ORDER), "R"]
    for step in case["steps"]:
        i = step["in"]
        lines.append("S " + " ".join(str(int(i[k])) if isinstance(i[k], bool) else str(i[k]) for k in IN_ORDER))
    r = subprocess.run([str(exe)], input="\n".join(lines) + "\n", capture_output=True, text=True, timeout=30)
    assert r.returncode == 0, r.stderr
    rows = []
    for line in r.stdout.strip().splitlines():
        v = line.split()
        rows.append({"compM": float(v[0]), "compR": float(v[1]), "compT": float(v[2]), "score": float(v[3]),
                     "usable": int(v[4]), "raw": int(v[5]), "state": int(v[6])})
    return rows


@pytest.mark.parametrize("case", VECTORS["cases"], ids=[c["name"] for c in VECTORS["cases"]])
def test_firmware_cpp_engine_vectors(harness, case):
    check(cpp_run(harness, case), case, "firmware-c++")


@pytest.mark.parametrize("case", VECTORS["cases"], ids=[c["name"] for c in VECTORS["cases"]])
def test_python_and_cpp_identical_states(harness, case):
    py, cc = python_run(case), cpp_run(harness, case)
    assert [r["state"] for r in py] == [r["state"] for r in cc]
    assert [r["raw"] for r in py] == [r["raw"] for r in cc]
    for a, b in zip(py, cc):
        assert abs(a["score"] - b["score"]) <= 1e-6


def test_cpp_defaults_match_python_defaults(harness):
    r = subprocess.run([str(harness)], input="D\n", capture_output=True, text=True, timeout=30)
    v = [float(x) for x in r.stdout.split()]
    p = RiskParams.from_ino()
    assert v[:6] == pytest.approx([p.wM, p.wR, p.wT, p.tau1, p.tau2, p.tau3], abs=1e-7)
    assert int(v[6]) == p.dwellMs


def test_random_inputs_agree(harness):
    """Property check: 300 pseudo-random steps with default params give identical states."""
    import random
    rng = random.Random(7)
    steps = []
    for k in range(300):
        steps.append({"in": {"m": rng.random(), "dm": rng.uniform(-0.05, 0.06), "r": rng.random(),
                             "drS": rng.uniform(0, 900), "tilt": rng.uniform(-6, 6), "tiltRate": rng.uniform(-0.05, 0.12),
                             "mOk": rng.random() > 0.1, "rOk": rng.random() > 0.1, "tOk": rng.random() > 0.1,
                             "ratesReady": rng.random() > 0.2, "nowMs": k * 1000}, "expect": {}})
    case = {"name": "random", "params": "provisional_defaults", "steps": steps}
    py, cc = python_run(case), cpp_run(harness, case)
    mism = [(i, a["state"], b["state"], a["score"], b["score"]) for i, (a, b) in enumerate(zip(py, cc)) if a["state"] != b["state"]]
    assert not mism, mism[:5]
