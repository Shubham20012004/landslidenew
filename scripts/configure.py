"""Create or complete the local configuration (called by setup.bat / setup.sh).

* .env (repository root): created from .env.example if missing; missing tokens
  are generated. Existing values are never overwritten.
* firmware/landslide_monitor/secrets.h: if it exists and has no DEVICE_TOKEN
  (or a placeholder), the esp32-node-1 token is inserted. Wi-Fi settings are
  left untouched. If it does not exist, nothing is created (copy
  secrets.example.h and fill in your Wi-Fi first).

Tokens are written to these local, git-ignored files only. They are never
printed in full.
"""
from __future__ import annotations

import re
import secrets
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ENV = ROOT / ".env"
ENV_EXAMPLE = ROOT / ".env.example"
SECRETS_H = ROOT / "firmware" / "landslide_monitor" / "secrets.h"
DEVICE_ID = "esp32-node-1"
SIM_ID = "sim-node-1"


def read_env(path: Path) -> dict[str, str]:
    values = {}
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            m = re.match(r"^\s*([A-Z_][A-Z0-9_]*)\s*=\s*(.*)$", line)
            if m:
                values[m.group(1)] = m.group(2).strip()
    return values


def set_env_value(text: str, key: str, value: str) -> str:
    pattern = re.compile(rf"^\s*{key}\s*=.*$", re.M)
    if pattern.search(text):
        return pattern.sub(f"{key}={value}", text, count=1)
    return text.rstrip("\n") + f"\n{key}={value}\n"


def secrets_token() -> str | None:
    if not SECRETS_H.exists():
        return None
    m = re.search(r'#define\s+DEVICE_TOKEN\s+"([^"]*)"', SECRETS_H.read_text(encoding="utf-8"))
    token = m.group(1) if m else ""
    return token if len(token) >= 16 and "paste" not in token else None


def main() -> int:
    created = not ENV.exists()
    text = ENV.read_text(encoding="utf-8") if ENV.exists() else ENV_EXAMPLE.read_text(encoding="utf-8")
    values = read_env(ENV) if ENV.exists() else {}
    changes = []

    tokens = {}
    for item in values.get("LANDSLIDE_DEVICE_TOKENS", "").split(","):
        if ":" in item:
            dev, tok = item.split(":", 1)
            tokens[dev.strip()] = tok.strip()
    fw_token = secrets_token()
    if DEVICE_ID not in tokens:
        tokens[DEVICE_ID] = fw_token or secrets.token_urlsafe(24)
        changes.append(f"device token for {DEVICE_ID}" + (" (taken from secrets.h)" if fw_token else " (generated)"))
    if SIM_ID not in tokens:
        tokens[SIM_ID] = secrets.token_urlsafe(24)
        changes.append(f"device token for {SIM_ID} (simulator)")
    text = set_env_value(text, "LANDSLIDE_DEVICE_TOKENS", ",".join(f"{d}:{t}" for d, t in tokens.items()))
    if len(values.get("LANDSLIDE_ADMIN_TOKEN", "")) < 16:
        text = set_env_value(text, "LANDSLIDE_ADMIN_TOKEN", secrets.token_urlsafe(24))
        changes.append("admin token (generated)")
    ENV.write_text(text, encoding="utf-8")
    print(("created " if created else "updated ") + str(ENV.relative_to(ROOT)) + (": " + ", ".join(changes) if changes else ": no changes"))

    if SECRETS_H.exists():
        if fw_token is None:
            src = SECRETS_H.read_text(encoding="utf-8")
            line = f'#define DEVICE_TOKEN  "{tokens[DEVICE_ID]}"'
            if re.search(r"#define\s+DEVICE_TOKEN", src):
                src = re.sub(r"#define\s+DEVICE_TOKEN\s+\"[^\"]*\"", line, src)
            else:
                src = src.rstrip("\n") + "\n" + line + "\n"
            SECRETS_H.write_text(src, encoding="utf-8")
            print("updated firmware/landslide_monitor/secrets.h with the device token (re-flash the ESP32)")
        elif fw_token != tokens[DEVICE_ID]:
            print("WARNING: DEVICE_TOKEN in secrets.h differs from .env; the backend will reject the ESP32.\n"
                  "         Make LANDSLIDE_DEVICE_TOKENS in .env and DEVICE_TOKEN in secrets.h identical.")
        else:
            print("firmware secrets.h token matches .env")
    else:
        print("firmware/landslide_monitor/secrets.h not found: copy secrets.example.h to secrets.h, set Wi-Fi and\n"
              "  API_HOST, then run setup again to insert the device token (or copy it from .env).")
    print("Admin token (for dataset labels / model reload) is in .env as LANDSLIDE_ADMIN_TOKEN.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
