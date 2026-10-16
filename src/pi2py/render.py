"""Rich terminal rendering for streamed pi run events.

This is the engine behind :meth:`pi2py.Agent.print_response`: pi's output
(streamed reasoning + markdown text) rendered with rich instead of pi's own
TUI. Reasoning streams dim above the live-updating markdown and is dropped
from the final render.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from rich.console import Console, Group, RenderableType
from rich.live import Live
from rich.markdown import Markdown
from rich.text import Text

if TYPE_CHECKING:
    from pi2py.agent import RunEvent


class RunPrinter:
    """Render a stream of :class:`~pi2py.agent.RunEvent` to a terminal.

    Non-assistant messages are ignored; tool events arrive with M3's remote
    tools and are ignored until then. On a non-terminal console (pipe, log
    file) the live region is skipped and only the final message is printed.
    """

    def __init__(
        self, *, console: Console | None = None, markdown: bool = True
    ) -> None:
        self._console = console if console is not None else Console()
        self._markdown = markdown
        self._live: Live | None = None
        self._thinking = ""
        self._text = ""

    def handle(self, event: RunEvent) -> None:
        role = (event.message or {}).get("role")
        if event.type == "message_start" and role == "assistant":
            self._thinking, self._text = "", ""
        elif event.type == "message_update":
            if event.thinking_delta:
                self._thinking += event.thinking_delta
                self._refresh()
            if event.delta:
                self._text += event.delta
                self._refresh()
        elif event.type == "message_end" and role == "assistant":
            self._finish()

    def _renderable(self) -> RenderableType:
        parts: list[RenderableType] = []
        if self._thinking:
            parts.append(Text(self._thinking, style="dim italic"))
        if self._text:
            parts.append(self._styled_text())
        return parts[0] if len(parts) == 1 else Group(*parts)

    def _styled_text(self) -> RenderableType:
        return Markdown(self._text) if self._markdown else Text(self._text)

    def _refresh(self) -> None:
        if not self._console.is_terminal:
            return
        if self._live is None:
            # transient: thinking vanishes when the message completes; lines
            # that already scrolled off-screen cannot be erased and remain in
            # the scrollback — the final render below is always clean.
            self._live = Live(
                self._renderable(),
                console=self._console,
                refresh_per_second=10,
                vertical_overflow="visible",
                transient=True,
            )
            self._live.start()
        else:
            self._live.update(self._renderable())

    def _finish(self) -> None:
        if self._live is not None:
            self._live.stop()
            self._live = None
        if self._text.strip():
            self._console.print(self._styled_text())
        elif self._thinking.strip():
            self._console.print(Text(self._thinking, style="dim"))
        self._thinking, self._text = "", ""
