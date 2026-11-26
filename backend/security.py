"""Device and admin authentication (shared-secret tokens over the local network)."""
from __future__ import annotations

import hmac
import logging

from fastapi import HTTPException, Request

from .config import Settings
from .logging_setup import kv

log = logging.getLogger("landslide.security")


def client_host(request: Request) -> str:
    return request.client.host if request.client else "?"


def authenticate_device(request: Request, settings: Settings) -> str:
    """Returns the authenticated device id or raises 401."""
    device_id = request.headers.get("x-device-id", "")
    token = request.headers.get("x-device-token", "")
    expected = settings.device_tokens.get(device_id)
    if not expected or not token or not hmac.compare_digest(token.encode(), expected.encode()):
        log.warning("device authentication failed %s", kv(device_id=device_id[:64] or "-", client=client_host(request)))
        raise HTTPException(status_code=401, detail="device authentication failed")
    return device_id


def require_admin(request: Request, settings: Settings) -> None:
    if not settings.admin_token:
        raise HTTPException(status_code=503, detail="admin actions are disabled: LANDSLIDE_ADMIN_TOKEN is not configured")
    token = request.headers.get("x-admin-token", "")
    if not token or not hmac.compare_digest(token.encode(), settings.admin_token.encode()):
        log.warning("admin authentication failed %s", kv(path=request.url.path, client=client_host(request)))
        raise HTTPException(status_code=401, detail="admin token required")
