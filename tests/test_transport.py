"""Transport-layer tests for the M1 JSONL protocol client.

The session-scoped ``sidecar_env`` fixture (conftest.py) points
PI2PY_SIDECAR_DIR at a temp dir, so the first test exercises the full
first-run path (copy sidecar files + npm ci + spawn). Subsequent processes
reuse the install.
"""

import os
import re
import shutil
import threading
import time
from collections.abc import Iterator
from pathlib import Path

import pytest

from pi2py import (
    NodeNotFoundError,
    PiProcess,
    PiProcessError,
    SidecarCommandError,
    find_node,
)

pytestmark = pytest.mark.skipif(
    shutil.which("node") is None and os.environ.get("PI2PY_NODE") is None,
    reason="Node.js not available",
)


@pytest.fixture
def proc() -> Iterator[tuple[PiProcess, list[dict]]]:
    events: list[dict] = []
    process = PiProcess(on_event=events.append)
    process.start()
    yield process, events
    process.close()


def test_find_node() -> None:
    node = find_node()
    assert Path(node).is_file()


def test_ready_handshake(proc: tuple[PiProcess, list[dict]]) -> None:
    process, _ = proc
    assert process.alive
    assert re.fullmatch(r"v\d+\.\d+\.\d+\S*", process.node_version or "")


def test_ping(proc: tuple[PiProcess, list[dict]]) -> None:
    process, _ = proc
    data = process.request("ping")
    assert data["pong"] is True
    assert data["node"] == process.node_version


def test_echo_roundtrip(proc: tuple[PiProcess, list[dict]]) -> None:
    process, _ = proc
    data = process.request("echo", {"payload": {"a": [1, 2, 3], "s": "汉字\nline"}})
    assert data["echo"] == {"a": [1, 2, 3], "s": "汉字\nline"}


def test_selfcheck_loads_pi_packages(proc: tuple[PiProcess, list[dict]]) -> None:
    process, _ = proc
    data = process.request("selfcheck", timeout=60)
    assert data["agentCoreLoaded"] is True
    assert data["piAiLoaded"] is True


def test_unknown_command(proc: tuple[PiProcess, list[dict]]) -> None:
    process, _ = proc
    with pytest.raises(SidecarCommandError, match="unknown command"):
        process.request("definitely-not-a-command")


def test_event_dispatch(proc: tuple[PiProcess, list[dict]]) -> None:
    process, events = proc
    data = process.request("emit-test-event", {"n": 7})
    assert data["emitted"] is True
    # the sidecar sends the event record before the response, so by the time
    # request() returns the reader thread has already dispatched it
    assert len(events) == 1
    assert events[0]["type"] == "event"
    assert events[0]["kind"] == "test"
    assert events[0]["n"] == 7


def test_request_scoped_events_route_by_id(proc: tuple[PiProcess, list[dict]]) -> None:
    process, global_events = proc
    scoped: list[dict] = []
    data = process.request("emit-test-event", {"n": 3}, on_event=scoped.append)
    assert data["emitted"] is True
    assert len(scoped) == 1
    assert scoped[0]["n"] == 3
    # records routed to a request do not reach the global handler
    assert global_events == []


def test_concurrent_requests_correlate_by_id(
    proc: tuple[PiProcess, list[dict]],
) -> None:
    process, _ = proc
    results: dict[int, object] = {}
    errors: list[BaseException] = []

    def worker(i: int) -> None:
        try:
            data = process.request("echo", {"payload": i})
            results[i] = data["echo"]
        except BaseException as exc:  # noqa: BLE001 — collected and asserted below
            errors.append(exc)

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(24)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)

    assert errors == []
    assert results == {i: i for i in range(24)}


def test_on_record_taps_both_directions() -> None:
    seen: list[tuple[str, dict]] = []
    with PiProcess(on_record=lambda d, r: seen.append((d, r))) as process:
        assert process.request("echo", {"payload": 1})["echo"] == 1
    # rx taps land before dispatch, so everything is in by the time run() returns
    assert seen[0][0] == "rx" and seen[0][1]["type"] == "ready"
    assert ("tx", {"id": "0", "type": "echo", "payload": 1}) in seen
    assert any(
        d == "rx" and r.get("type") == "response" and r.get("id") == "0"
        for d, r in seen
    )


def test_on_record_raising_is_contained() -> None:
    def boom(direction: str, record: dict) -> None:
        raise RuntimeError("boom")

    with PiProcess(on_record=boom) as process:
        assert process.request("ping")["pong"] is True
    assert "on_record raised" in process.stderr_tail


def test_graceful_shutdown(proc: tuple[PiProcess, list[dict]]) -> None:
    process, _ = proc
    process.request("ping")
    process.close()
    assert process.exit_code == 0
    assert not process.alive
    # close is idempotent
    process.close()


def test_crash_detection_and_stderr_tail(proc: tuple[PiProcess, list[dict]]) -> None:
    process, _ = proc
    dying = process.request("die", {"code": 3})
    assert dying["dying"] is True
    # die exits on a short timer; wait for the reader thread to observe EOF
    deadline = time.monotonic() + 2.0
    while process.alive and time.monotonic() < deadline:
        time.sleep(0.01)
    assert not process.alive
    with pytest.raises(PiProcessError):
        process.request("ping")
    assert process.exit_code == 3
    assert "die command received" in process.stderr_tail


def test_context_manager() -> None:
    with PiProcess() as process:
        assert process.request("ping")["pong"] is True
    assert not process.alive
    assert process.exit_code == 0


def test_request_before_start() -> None:
    with pytest.raises(PiProcessError, match="not started"):
        PiProcess().request("ping")


def test_node_not_found_error_message(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PI2PY_NODE", "/nonexistent/node")
    with pytest.raises(NodeNotFoundError, match="PI2PY_NODE"):
        find_node()
