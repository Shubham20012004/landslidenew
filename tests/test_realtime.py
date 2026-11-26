"""Live event stream: hub unit tests and an end-to-end check against a real server."""
from __future__ import annotations

import asyncio
import json
import socket
import threading
import time

import httpx
import pytest
import uvicorn

from backend.services.realtime import Hub, sse_format
from conftest import DEV_HEADERS, TILT_FOR_CLASS, make_payload


def test_sse_format_rejects_nan():
    assert sse_format("x", {"a": 1}, 3) == 'id: 3\nevent: x\ndata: {"a":1}\n\n'
    with pytest.raises(ValueError):
        sse_format("x", {"a": float("nan")})


def test_hub_fanout_and_slow_subscriber_drop():
    async def run():
        hub = Hub()
        hub.bind_loop(asyncio.get_running_loop())
        q1, q2 = hub.subscribe(), hub.subscribe()
        hub.publish("reading", {"pk": 1})
        assert "reading" in await asyncio.wait_for(q1.get(), 1)
        assert "reading" in await asyncio.wait_for(q2.get(), 1)
        for i in range(Hub.QUEUE_SIZE + 5):     # q2 never drained, q1 drained below
            hub.publish("event", {"i": i})
            while not q1.empty():
                q1.get_nowait()
        assert hub.is_subscribed(q1) and not hub.is_subscribed(q2)
        # publishing from another thread is safe
        threading.Thread(target=hub.publish, args=("event", {"from": "thread"})).start()
        msg = await asyncio.wait_for(q1.get(), 1)
        assert "thread" in msg

    asyncio.run(run())


def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


@pytest.fixture
def live_server(settings):
    from backend.main import create_app

    port = _free_port()
    server = uvicorn.Server(uvicorn.Config(create_app(settings), host="127.0.0.1", port=port, log_level="warning"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    for _ in range(100):
        if server.started:
            break
        time.sleep(0.05)
    yield f"http://127.0.0.1:{port}"
    server.should_exit = True
    thread.join(timeout=10)


def _read_events(response, wanted: set[str], timeout: float = 10):
    events, current = [], {}
    deadline = time.time() + timeout
    for line in response.iter_lines():
        if line.startswith("event: "):
            current["event"] = line[7:]
        elif line.startswith("data: "):
            current["data"] = json.loads(line[6:])
        elif line == "" and current:
            events.append(current)
            current = {}
            if wanted <= {e["event"] for e in events}:
                break
        if time.time() > deadline:
            break
    return events


def test_end_to_end_stream_delivers_snapshot_reading_and_events(live_server):
    """Device telemetry -> backend -> database -> Random Forest -> device response -> live dashboard stream."""
    with httpx.Client(base_url=live_server, timeout=10) as http:
        with http.stream("GET", "/api/stream") as stream:
            assert stream.headers["content-type"].startswith("text/event-stream")

            def send():
                time.sleep(0.5)
                r = http.post("/api/readings", json=make_payload(1, tilt=TILT_FOR_CLASS["WARNING"]), headers=DEV_HEADERS)
                assert r.json()["risk_state"] == "WARNING"

            t = threading.Thread(target=send)
            t.start()
            events = _read_events(stream, {"snapshot", "reading", "event"})
            t.join()
    names = [e["event"] for e in events]
    assert names[0] == "snapshot"
    reading = next(e["data"] for e in events if e["event"] == "reading")
    assert reading["reading_id"] == "a1b2c3d4-01-000001"
    assert reading["prediction"]["risk_state"] == "WARNING"     # same prediction the device received
    assert any(e["data"]["kind"] in ("SESSION_STARTED", "RISK_CHANGED") for e in events if e["event"] == "event")


def test_stream_resynchronises_after_reconnect(live_server):
    with httpx.Client(base_url=live_server, timeout=10) as http:
        http.post("/api/readings", json=make_payload(1, tilt=TILT_FOR_CLASS["CRITICAL"]), headers=DEV_HEADERS)
        with http.stream("GET", "/api/stream") as stream:   # a "reconnecting" client
            snap = _read_events(stream, {"snapshot"})[0]["data"]
    latest = snap["latest"]["esp32-node-1"]
    assert latest["prediction"]["risk_state"] == "CRITICAL"
    assert snap["devices"][0]["device_id"] == "esp32-node-1" and snap["events"]
