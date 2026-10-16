"""Agno-style Agent facade over the sidecar.

Mirrors the slice of agno's Agent API that pi can honor today:
``Agent(model=..., provider=..., instructions=..., session_id=...)``,
``run(input) -> RunOutput`` with ``.content`` / ``.usage`` / ``.messages``,
streaming ``run(input, stream=True) -> Iterator[RunEvent]``, and
``print_response(input)`` rendering the stream with rich.
"""

import queue
import threading
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Literal, Self, overload

from rich.console import Console

from pi2py.render import RunPrinter
from pi2py.transport import PiProcess, Record


@dataclass
class RunOutput:
    """Result of one :meth:`Agent.run` — agno's RunOutput shape."""

    content: str
    usage: dict | None = None
    messages: list[dict] = field(default_factory=list)


@dataclass
class RunEvent:
    """One streamed pi AgentEvent (pi-agent-core's vocabulary:
    ``agent_start``/``agent_end``, ``turn_start``/``turn_end``,
    ``message_start``/``message_update``/``message_end``,
    ``tool_execution_*``). ``raw`` is the event as the sidecar sent it.
    """

    type: str
    raw: dict = field(default_factory=dict)

    @property
    def delta(self) -> str:
        """Assistant text delta, when this update is a ``text_delta``."""
        return _ame_delta(self.raw, self.type, "text_delta")

    @property
    def thinking_delta(self) -> str:
        """Reasoning delta, when this update is a ``thinking_delta``."""
        return _ame_delta(self.raw, self.type, "thinking_delta")

    @property
    def message(self) -> dict | None:
        """Full message payload of message_start/end and turn_end events."""
        msg = self.raw.get("message")
        return msg if isinstance(msg, dict) else None

    @property
    def content(self) -> str:
        """Full text of ``message`` (pi-ai's contentText shape)."""
        if self.message is None:
            return ""
        parts = [
            block.get("text")
            for block in self.message.get("content") or []
            if isinstance(block, Mapping) and block.get("type") == "text"
        ]
        return "".join(p for p in parts if isinstance(p, str))


def _ame_delta(raw: Mapping[str, object], event_type: str, kind: str) -> str:
    ame = raw.get("assistantMessageEvent")
    if event_type == "message_update" and isinstance(ame, Mapping) and ame.get("type") == kind:
        delta = ame.get("delta")
        if isinstance(delta, str):
            return delta
    return ""


class Agent:
    """A pi agent bound to one provider + model.

    ``provider`` is a models.json custom-provider entry (dict with ``id``,
    ``baseUrl``, ``api``, ``apiKey``, ``compat``, ``models``) — the same shape
    pi's models.json uses. Each Agent owns one sidecar process unless
    ``process`` passes an existing :class:`PiProcess` to share.

    Usage::

        agent = Agent(model="dots3-note-prev", provider=PROVIDER,
                      instructions="Answer in Chinese.")
        result = agent.run("hello")
        print(result.content)
    """

    def __init__(
        self,
        *,
        model: str,
        provider: Mapping[str, object],
        instructions: str | Sequence[str] | None = None,
        session_id: str | None = None,
        process: PiProcess | None = None,
    ) -> None:
        if isinstance(instructions, (list, tuple)):
            instructions = "\n".join(instructions)
        self._payload: dict[str, object] = {
            "model": model,
            "provider": dict(provider),
            "instructions": instructions,
            "sessionId": session_id,
        }
        self._process = process
        self._owns_process = process is None

    @overload
    def run(self, input: str, *, stream: Literal[False] = ...) -> RunOutput: ...

    @overload
    def run(self, input: str, *, stream: Literal[True]) -> Iterator[RunEvent]: ...

    def run(self, input: str, *, stream: bool = False) -> "RunOutput | Iterator[RunEvent]":
        """Run one prompt to completion and return the assistant output.

        With ``stream=True``, return a generator yielding :class:`RunEvent`
        as pi emits them; the final assistant text is the last ``message_end``
        event's ``.content``. The sidecar run always finishes, even if the
        generator is abandoned early.
        """
        if stream:
            return self._run_streaming(input)
        data = self._process_request({**self._payload, "input": input})
        usage = data.get("usage")
        messages = data.get("messages")
        return RunOutput(
            content=str(data.get("content", "")),
            usage=usage if isinstance(usage, dict) else None,
            messages=messages if isinstance(messages, list) else [],
        )

    def print_response(
        self,
        input: str,
        *,
        markdown: bool = True,
        console: Console | None = None,
    ) -> None:
        """Run a prompt and render it live in the terminal with rich."""
        printer = RunPrinter(console=console, markdown=markdown)
        for event in self._run_streaming(input):
            printer.handle(event)

    def _process_request(self, payload: Mapping[str, object]) -> Record:
        if self._process is None:
            process = PiProcess()
            process.start()
            self._process = process
        return self._process.request("agent.run", payload)

    def _run_streaming(self, input: str) -> Iterator[RunEvent]:
        process = self._process
        if process is None:
            process = PiProcess()
            process.start()
            self._process = process
        events: queue.SimpleQueue[RunEvent | None] = queue.SimpleQueue()
        failure: list[BaseException] = []

        def deliver(record: Record) -> None:
            event = record.get("event")
            if record.get("kind") == "agent" and isinstance(event, dict):
                events.put(RunEvent(type=str(event.get("type", "")), raw=event))

        def work() -> None:
            try:
                process.request(
                    "agent.run",
                    {**self._payload, "input": input, "stream": True},
                    on_event=deliver,
                )
            except BaseException as exc:  # noqa: BLE001 — re-raised by the generator once the stream drains
                failure.append(exc)
            finally:
                events.put(None)

        threading.Thread(target=work, name="pi2py-agent-run", daemon=True).start()
        while True:
            event = events.get()
            if event is None:
                if failure:
                    raise failure[0]
                return
            yield event

    def close(self) -> None:
        """Close the sidecar process if this Agent spawned it."""
        if self._owns_process and self._process is not None:
            self._process.close()
            self._process = None

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()
