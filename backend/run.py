"""Start the backend (API + dashboard + live stream) on the LAN.

    python -m backend.run            (from the repository root; start.bat / start.sh do this)
"""
from __future__ import annotations

import socket
import sys

import uvicorn

from .config import APP_VERSION, Settings
from .logging_setup import configure_logging
from .main import create_app


def lan_ip() -> str:
    """Best-effort LAN IPv4 address (a UDP connect sends no packets)."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("10.255.255.255", 1))
        return s.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        s.close()


def main() -> int:
    try:
        settings = Settings.from_env()
    except ValueError as exc:
        print(f"Configuration error: {exc}\nFix .env (see .env.example) and try again.", file=sys.stderr)
        return 2
    configure_logging(settings.log_dir)
    app = create_app(settings)
    ip = lan_ip()
    bar = "=" * 72
    print(bar)
    print(f" Landslide Monitoring backend {APP_VERSION}")
    print(f" Dashboard        http://localhost:{settings.port}/   (LAN: http://{ip}:{settings.port}/)")
    print(f" ESP32 endpoint   http://{ip}:{settings.port}/api/readings")
    print(f"   secrets.h  ->  API_HOST \"{ip}\"   API_PORT {settings.port}")
    print(f" Devices          {', '.join(sorted(settings.device_tokens)) or 'NONE (run setup to create tokens)'}")
    print(f" Demo mode        {'ON - synthetic demonstration model allowed' if settings.demo_mode else 'off'}")
    print(f" Database         {settings.db_path}")
    print(f" API docs         http://localhost:{settings.port}/docs")
    print(" If the ESP32 reports HTTP -1 / connection refused: same Wi-Fi? firewall allows TCP", settings.port,
          "(see docs/TROUBLESHOOTING.md)")
    print(bar)
    try:
        uvicorn.run(app, host=settings.host, port=settings.port, log_level="info", access_log=False)
    except OSError as exc:
        print(f"Could not start the server on {settings.host}:{settings.port}: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
