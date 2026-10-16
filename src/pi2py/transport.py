"""Sync transport to the pi2py Node sidecar: process lifecycle + JSONL protocol.

This is the low-level building block; the agno-style Agent facade is built on
top of it. Protocol rules (mirroring pi's own RPC mode):

- stdin/stdout carry JSONL records; stdout is protocol-only, diagnostics
  go to stderr (kept in ``stderr_tail`` for error reports)
- requests carry a string ``id``; responses echo it and may arrive out of
  order — always correlate by id
- ``event`` records are dispatched from the reader thread: records echoing a
  pending request's ``id`` go to that request's ``on_event`` callback
  (see :meth:`request`), the rest to the ``on_event`` constructor callback
- closing stdin (``close()``) asks the sidecar to exit gracefully
"""

import atexit
import json
import os
import subprocess
import sys
import threading
import weakref
from collections import deque
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Self

from pi2py._install import ensure_sidecar
from pi2py._node import find_node

Record = dict[str, object]
EventHandler = Callable[[Record], None]

_STARTUP_TIMEOUT = 15.0
_CLOSE_TIMEOUT = 5.0
_PROTOCOL = 1


class PiProcessError(Exception):
    """The sidecar process failed, died, or was misused."""


class SidecarCommandError(PiProcessError):
    """The sidecar rejected a command or reported a command-level error."""


class SidecarTimeout(PiProcessError):
    """A command did not receive its response within the timeout."""


class _Pending:
    __slots__ = ("error", "event", "on_event", "result")

    def __init__(self) -> None:
        self.event = threading.Event()
        self.result: Record | None = None
        self.error: PiProcessError | None = None
        self.on_event: EventHandler | None = None


class PiProcess:
    """A running pi2py Node sidecar process.

    Usage::

        with PiProcess(on_event=print) as proc:
            data = proc.request("ping")
    """

    def __init__(
        self,
        *,
        node_path: str | None = None,
        sidecar_dir: Path | str | None = None,
        env: Mapping[str, str] | None = None,
        on_event: EventHandler | None = None,
        startup_timeout: float = _STARTUP_TIMEOUT,
    ) -> None:
        self._node_path = node_path
        self._sidecar_dir = Path(sidecar_dir) if sidecar_dir is not None else None
        self._env: dict[str, str] | None = dict(env) if env is not None else None
        self._on_event = on_event
        self._startup_timeout = startup_timeout

        self._proc: subprocess.Popen[bytes] | None = None
        self._threads: list[threading.Thread] = []
        self._pending_lock = threading.Lock()
        self._write_lock = threading.Lock()
        self._pending: dict[str, _Pending] = {}
        self._next_id = 0
        self._init_event = threading.Event()
        self._node_version: str | None = None
        self._protocol: int | None = None
        self._fatal: str | None = None
        self._dead = False
        self._exit_code: int | None = None
        self._stderr_lines: deque[str] = deque(maxlen=200)

    # -- lifecycle ----------------------------------------------------------

    @property
    def node_version(self) -> str | None:
        """Node version reported by the sidecar, e.g. ``'v24.13.1'``."""
        return self._node_version

    @property
    def exit_code(self) -> int | None:
        """Sidecar exit code once it has terminated, else ``None``."""
        return self._exit_code

    @property
    def alive(self) -> bool:
        return self._proc is not None and not self._dead

    @property
    def stderr_tail(self) -> str:
        return "\n".join(self._stderr_lines)

    def start(self) -> None:
        """Spawn the sidecar and wait for its ``ready`` record."""
        if self._proc is not None:
            raise PiProcessError("PiProcess already started")
        node = self._node_path or find_node()
        sidecar_dir = self._sidecar_dir or ensure_sidecar()
        script = sidecar_dir / "sidecar.mjs"
        if not script.is_file():
            raise PiProcessError(f"sidecar script not found: {script}")
        self._proc = subprocess.Popen(
            [node, str(script)],
            cwd=str(sidecar_dir),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=self._env if self._env is not None else os.environ.copy(),
        )
        self._threads = [
            threading.Thread(target=self._reader_loop, name="pi2py-reader", daemon=True),
            threading.Thread(target=self._stderr_loop, name="pi2py-stderr", daemon=True),
        ]
        for t in self._threads:
            t.start()
        _LIVE.add(self)

        if not self._init_event.wait(self._startup_timeout):
            self._kill_hard()
            raise PiProcessError(
                f"sidecar did not report ready within {self._startup_timeout}s\n{self.stderr_tail}"
            )
        if self._fatal is not None:
            self._kill_hard()
            raise PiProcessError(f"sidecar failed to start: {self._fatal}")
        if self._protocol != _PROTOCOL:
            self._kill_hard()
            raise PiProcessError(
                f"sidecar protocol mismatch: got {self._protocol}, expected {_PROTOCOL}"
            )

    def request(
        self,
        command: str,
        payload: Mapping[str, object] | None = None,
        *,
        timeout: float | None = None,
        on_event: EventHandler | None = None,
    ) -> Record:
        """Send a command and block until its id-matched response arrives.

        ``on_event``, if given, receives this request's ``event`` records
        (records echoing its id) from the reader thread while the call blocks.

        Raises SidecarCommandError if the sidecar reports failure,
        SidecarTimeout on timeout, PiProcessError if the process is gone.
        """
        if self._proc is None:
            raise PiProcessError("PiProcess not started")
        with self._pending_lock:
            if self._dead:
                raise PiProcessError(self._death_message())
            pid = str(self._next_id)
            self._next_id += 1
            pending = _Pending()
            pending.on_event = on_event
            self._pending[pid] = pending
        record: Record = {"id": pid, "type": command}
        if payload:
            record.update(payload)
        try:
            self._send(record)
        except PiProcessError:
            with self._pending_lock:
                self._pending.pop(pid, None)
            raise
        if not pending.event.wait(timeout):
            with self._pending_lock:
                self._pending.pop(pid, None)
            if self._dead:
                raise PiProcessError(self._death_message()) from None
            raise SidecarTimeout(f"no response for {command!r} within {timeout}s")
        if pending.error is not None:
            raise pending.error
        return pending.result or {}

    def close(self, timeout: float = _CLOSE_TIMEOUT) -> None:
        """Ask the sidecar to exit (close stdin), then reap it. Idempotent."""
        proc = self._proc
        if proc is None:
            return
        if not self._dead:
            try:
                assert proc.stdin is not None
                proc.stdin.close()
            except OSError:
                pass
            try:
                proc.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                proc.terminate()
                try:
                    proc.wait(timeout=2.0)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait()
        for t in self._threads:
            t.join(timeout=2.0)
        self._proc = None

    def __enter__(self) -> Self:
        if self._proc is None:
            self.start()
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    # -- internals ----------------------------------------------------------

    def _send(self, record: Record) -> None:
        proc = self._proc
        assert proc is not None and proc.stdin is not None
        data = (json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n").encode("utf-8")
        with self._write_lock:
            try:
                proc.stdin.write(data)
                proc.stdin.flush()
            except (BrokenPipeError, OSError) as exc:
                raise PiProcessError(self._death_message()) from exc

    def _reader_loop(self) -> None:
        proc = self._proc
        assert proc is not None and proc.stdout is not None
        # Binary readline splits on b"\n" only (never on U+2028/U+2029),
        # matching the sidecar's framing rules.
        out = proc.stdout
        while True:
            line = out.readline()
            if not line:
                break
            text = line.decode("utf-8", errors="replace").strip()
            if not text:
                continue
            try:
                record = json.loads(text)
            except json.JSONDecodeError:
                self._stderr_lines.append(f"[pi2py] non-JSON stdout line: {text[:200]}")
                continue
            self._dispatch(record)
        self._on_death()

    def _dispatch(self, record: Record) -> None:
        rtype = record.get("type")
        if rtype == "response":
            rid = record.get("id")
            with self._pending_lock:
                pending = self._pending.pop(rid, None) if isinstance(rid, str) else None
            if pending is None:
                self._stderr_lines.append(
                    "[pi2py] stray response: " + json.dumps(record, ensure_ascii=False)[:200]
                )
                return
            if record.get("success"):
                data = record.get("data")
                pending.result = data if isinstance(data, dict) else {}
            else:
                pending.error = SidecarCommandError(str(record.get("error", "command failed")))
            pending.event.set()
        elif rtype == "ready":
            protocol = record.get("protocol")
            self._protocol = protocol if isinstance(protocol, int) else None
            node = record.get("node")
            self._node_version = node if isinstance(node, str) else None
            self._init_event.set()
        elif rtype == "fatal":
            self._fatal = str(record.get("message", "unknown fatal error"))
            self._init_event.set()
        elif rtype == "event":
            handler = self._event_handler_for(record)
            if handler is not None:
                try:
                    handler(record)
                except Exception as exc:  # noqa: BLE001 — user callbacks must not kill the reader thread
                    self._stderr_lines.append(f"[pi2py] on_event raised: {exc!r}")
        else:
            self._stderr_lines.append(f"[pi2py] unknown record type: {rtype!r}")

    def _event_handler_for(self, record: Record) -> EventHandler | None:
        """An event echoing a pending request's id belongs to that request."""
        rid = record.get("id")
        if isinstance(rid, str):
            with self._pending_lock:
                pending = self._pending.get(rid)
            if pending is not None and pending.on_event is not None:
                return pending.on_event
        return self._on_event

    def _stderr_loop(self) -> None:
        proc = self._proc
        assert proc is not None and proc.stderr is not None
        for line in iter(proc.stderr.readline, b""):
            self._stderr_lines.append(line.decode("utf-8", errors="replace").rstrip("\n"))

    def _on_death(self) -> None:
        proc = self._proc
        code = proc.wait() if proc is not None else None
        with self._pending_lock:
            if self._dead:
                return
            self._dead = True
            self._exit_code = code
            pending = list(self._pending.items())
            self._pending.clear()
        for _, p in pending:
            p.error = PiProcessError(self._death_message())
            p.event.set()

    def _death_message(self) -> str:
        code = self._exit_code if self._exit_code is not None else "unknown"
        tail = self.stderr_tail
        msg = f"sidecar exited (code {code})"
        if tail:
            msg += f"\n{tail}"
        return msg

    def _kill_hard(self) -> None:
        proc = self._proc
        if proc is None:
            return
        proc.kill()
        proc.wait()
        for t in self._threads:
            t.join(timeout=2.0)


_LIVE: "weakref.WeakSet[PiProcess]" = weakref.WeakSet()


def _close_at_exit() -> None:
    for process in list(_LIVE):
        try:
            process.close(timeout=1.0)
        except Exception as exc:  # noqa: BLE001 — best-effort cleanup at interpreter exit
            print(f"pi2py: failed to close sidecar at exit: {exc!r}", file=sys.stderr)


atexit.register(_close_at_exit)
