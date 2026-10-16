"""Agno-style Agent facade over the sidecar.

Mirrors the slice of agno's Agent API that pi can honor today:
``Agent(model=..., provider=..., instructions=..., session_id=...)`` and
``run(input) -> RunOutput`` with ``.content`` / ``.usage`` / ``.messages``.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field

from pi2py.transport import PiProcess


@dataclass
class RunOutput:
    """Result of one :meth:`Agent.run` — agno's RunOutput shape."""

    content: str
    usage: dict | None = None
    messages: list[dict] = field(default_factory=list)


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

    def run(self, input: str) -> RunOutput:  # noqa: A002 — agno's parameter name
        """Run one prompt to completion and return the assistant output."""
        process = self._process
        if process is None:
            process = PiProcess()
            process.start()
            self._process = process
        data = process.request("agent.run", {**self._payload, "input": input})
        usage = data.get("usage")
        messages = data.get("messages")
        return RunOutput(
            content=str(data.get("content", "")),
            usage=usage if isinstance(usage, dict) else None,
            messages=messages if isinstance(messages, list) else [],
        )

    def close(self) -> None:
        """Close the sidecar process if this Agent spawned it."""
        if self._owns_process and self._process is not None:
            self._process.close()
            self._process = None

    def __enter__(self) -> "Agent":
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()
