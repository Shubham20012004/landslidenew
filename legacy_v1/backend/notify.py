"""Optional Telegram alerts. DISABLED unless TELEGRAM_ENABLED=true and both
TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID are set in .env. Only real device
transitions into WARNING/CRITICAL are sent; simulated data never triggers it."""
from __future__ import annotations

import json
import logging
import threading
import urllib.request

log = logging.getLogger("landslide.notify")


class Notifier:
    def __init__(self, enabled: bool, token: str, chat_id: str):
        self.enabled = bool(enabled and token and chat_id)
        self.token, self.chat_id = token, chat_id

    def state_change(self, device_id: str, source: str, old: str | None, new: str, score):
        if not self.enabled or source != "device" or new not in ("WARNING", "CRITICAL"):
            return
        text = (f"[PROTOTYPE] {device_id}: rule state {old} -> {new}"
                f" (R={score if score is not None else 'n/a'}). Prototype risk state, not an official warning.")
        threading.Thread(target=self._send, args=(text,), daemon=True).start()

    def _send(self, text: str):
        try:
            url = f"https://api.telegram.org/bot{self.token}/sendMessage"
            data = json.dumps({"chat_id": self.chat_id, "text": text}).encode()
            req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
            urllib.request.urlopen(req, timeout=5).read()
        except Exception as e:  # pragma: no cover - network dependent
            log.warning("Telegram send failed: %s", e)
