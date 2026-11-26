"""Read configuration directly from the single firmware file.

The ESP32 sketch is the single source of truth for pins, thresholds and
tunables. This module parses the //@@BEGIN_CONFIG ... //@@END_CONFIG region
(one ``constexpr`` declaration per line) so the Python risk-engine port,
simulator, dashboard and report tools never keep a second, drifting copy.
"""
from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
INO_PATH = REPO_ROOT / "firmware" / "landslide_monitor" / "landslide_monitor.ino"

_DECL = re.compile(
    r"^\s*constexpr\s+(int|float|double|bool|uint32_t|uint8_t|int32_t)\s+(\w+)\s*=\s*([^;]+);\s*(//\s*(.*))?$"
)


def extract_region(name: str, ino_path: Path = INO_PATH) -> str:
    """Return the text between //@@BEGIN_<name> and //@@END_<name>."""
    text = Path(ino_path).read_text(encoding="utf-8")
    begin, end = f"//@@BEGIN_{name}", f"//@@END_{name}"
    i, j = text.find(begin), text.find(end)
    if i < 0 or j < 0 or j < i:
        raise ValueError(f"Region {name} not found in {ino_path}")
    return text[i + len(begin): j]


def _parse_value(ctype: str, raw: str):
    raw = raw.strip()
    if ctype == "bool":
        return raw == "true"
    if ctype in ("float", "double"):
        return float(raw.rstrip("fF"))
    return int(raw, 0)


@lru_cache(maxsize=4)
def load_ino_config(ino_path: str | None = None) -> dict:
    """Parse constants -> {NAME: value}. Comments are kept in ``_comments``."""
    path = Path(ino_path) if ino_path else INO_PATH
    region = extract_region("CONFIG", path)
    cfg: dict = {}
    comments: dict = {}
    for line in region.splitlines():
        m = _DECL.match(line)
        if not m:
            continue
        ctype, name, value, _, comment = m.groups()
        cfg[name] = _parse_value(ctype, value)
        comments[name] = (comment or "").strip()
    if not cfg:
        raise ValueError("No constants parsed from the firmware CONFIG region")
    cfg["_comments"] = comments
    return cfg


def pin_table(ino_path: str | None = None) -> list[dict]:
    """GPIO table rows generated from the firmware pin configuration."""
    cfg = load_ino_config(ino_path)
    rows = []
    for name, value in cfg.items():
        if name.startswith("PIN_"):
            rows.append({"signal": name, "gpio": value, "note": cfg["_comments"].get(name, "")})
    for name in ("MPU_ADDR_PRIMARY", "MPU_ADDR_ALT"):
        if name in cfg:
            rows.append({"signal": name, "gpio": f"0x{cfg[name]:02X}", "note": cfg["_comments"].get(name, "")})
    return rows
