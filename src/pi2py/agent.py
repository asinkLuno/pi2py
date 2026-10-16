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
from pathlib import Path
from typing import Literal, Self, overload

from rich.console import Console

from pi2py.render import RunPrinter
from pi2py.transport import PiProcess, Record


def _split_frontmatter(text: str) -> tuple[str, str]:
    """Split leading YAML frontmatter into ``(block, body)``.

    First closing fence only: ``---`` rules in the body come after it and
    stay in the body.
    """
    if text.startswith("---\n"):
        end = text.find("\n---", 4)
        if end != -1:
            return text[4:end], text[end + 4 :].lstrip("\n")
    return "", text


def load_skill(path: str | Path) -> str:
    """Read a skill's instructions for forced injection into the prompt.

    ``path`` is a SKILL.md file, a directory containing one, or any markdown
    file. YAML frontmatter is stripped — it carries routing metadata
    (name/description), not instructions.
    """
    file = Path(path)
    if file.is_dir():
        file = file / "SKILL.md"
    _, body = _split_frontmatter(file.read_text(encoding="utf-8"))
    return body


@dataclass
class McpServer:
    """An MCP server the sidecar connects to, exposing its tools to the agent.

    Exactly one of stdio (``command``/``args``/``env``) or streamable HTTP
    (``url``). ``env``, when given, *replaces* the default inherited
    environment — PATH and friends are gone unless included.
    """

    command: str | None = None
    args: Sequence[str] = ()
    env: Mapping[str, str] | None = None
    url: str | None = None

    def payload(self) -> dict[str, object]:
        if (self.url is None) == (self.command is None):
            raise ValueError(
                "McpServer needs exactly one of command (stdio) or url (http)"
            )
        if self.url is not None:
            return {"url": self.url}
        return {
            "command": self.command,
            "args": list(self.args),
            **({"env": dict(self.env)} if self.env else {}),
        }


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
    if (
        event_type == "message_update"
        and isinstance(ame, Mapping)
        and ame.get("type") == kind
    ):
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
                      instructions="Answer in Chinese.",
                      skills=["/path/to/humanizer"],
                      mcp_servers=[McpServer(command="python",
                                             args=["-m", "mydb.mcp_server"])])
        result = agent.run("hello")
        print(result.content)
    """

    def __init__(
        self,
        *,
        model: str,
        provider: Mapping[str, object],
        instructions: str | Sequence[str] | None = None,
        skills: Sequence[str | Path] | None = None,
        mcp_servers: Sequence[McpServer] | None = None,
        session_id: str | None = None,
        process: PiProcess | None = None,
    ) -> None:
        if isinstance(instructions, (list, tuple)):
            instructions = "\n".join(instructions)
        if skills:
            # Forced injection: skill bodies go straight into the system
            # prompt, appended after the base instructions.
            skill_text = "\n\n".join(load_skill(s) for s in skills)
            instructions = (
                f"{instructions}\n\n{skill_text}" if instructions else skill_text
            )
        self._payload: dict[str, object] = {
            "model": model,
            "provider": dict(provider),
            "instructions": instructions,
            "sessionId": session_id,
        }
        if mcp_servers:
            # Validated here (payload() checks shape) so a bad definition
            # fails at construction, not on first run.
            self._payload["mcpServers"] = [s.payload() for s in mcp_servers]
        self._process = process
        self._owns_process = process is None

    @overload
    def run(self, input: str, *, stream: Literal[False] = ...) -> RunOutput: ...

    @overload
    def run(self, input: str, *, stream: Literal[True]) -> Iterator[RunEvent]: ...

    def run(
        self, input: str, *, stream: bool = False
    ) -> "RunOutput | Iterator[RunEvent]":
        """Run one prompt to completion and return the assistant output.

        With ``stream=True``, return a generator yielding :class:`RunEvent`
        as pi emits them; the final assistant text is the last ``message_end``
        event's ``.content``. The sidecar run always finishes, even if the
        generator is abandoned early.
        """
        return self._run(self._payload.get("instructions"), input, stream)

    @overload
    def run_skill(
        self, skill: str | Path, input: str, *, stream: Literal[False] = ...
    ) -> RunOutput: ...

    @overload
    def run_skill(
        self, skill: str | Path, input: str, *, stream: Literal[True]
    ) -> Iterator[RunEvent]: ...

    def run_skill(
        self, skill: str | Path, input: str, *, stream: bool = False
    ) -> "RunOutput | Iterator[RunEvent]":
        """``/skill:name input`` equivalent: run one prompt with a skill's
        full instructions force-injected, for this run only.

        ``skill`` is a path to a SKILL.md file or its directory — no name
        discovery; the caller owns where skills live. The skill body is
        appended after this agent's base instructions; later ``run`` calls
        are unaffected.
        """
        body = load_skill(skill)
        base = self._payload.get("instructions")
        return self._run(f"{base}\n\n{body}" if base else body, input, stream)

    def _run(
        self, instructions: str | None, input: str, stream: bool
    ) -> "RunOutput | Iterator[RunEvent]":
        payload = {**self._payload, "instructions": instructions, "input": input}
        if stream:
            return self._run_streaming(payload)
        data = self._process_request(payload)
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
        for event in self._run_streaming({**self._payload, "input": input}):
            printer.handle(event)

    def _process_request(self, payload: Mapping[str, object]) -> Record:
        if self._process is None:
            process = PiProcess()
            process.start()
            self._process = process
        return self._process.request("agent.run", payload)

    def _run_streaming(self, payload: Mapping[str, object]) -> Iterator[RunEvent]:
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
                    {**payload, "stream": True},
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
