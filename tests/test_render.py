"""RunPrinter + RunEvent tests — pure, no sidecar, no network."""

import io

from rich.console import Console

from pi2py.agent import RunEvent, RunPrinter


def make_console() -> tuple[Console, io.StringIO]:
    buf = io.StringIO()
    return Console(file=buf, width=80), buf


def run_event(raw: dict) -> RunEvent:
    return RunEvent(type=str(raw.get("type", "")), raw=raw)


def assistant_stream(thinking: str = "let me think", text: str = "# Title\n\nhello world") -> list[RunEvent]:
    """A realistic event sequence for one assistant message."""
    return [
        run_event({"type": "agent_start"}),
        run_event({"type": "message_start", "message": {"role": "user", "content": []}}),
        run_event({"type": "message_end", "message": {"role": "user", "content": []}}),
        run_event({"type": "message_start", "message": {"role": "assistant", "content": []}}),
        run_event({"type": "turn_start"}),
        run_event({"type": "message_update", "assistantMessageEvent": {"type": "thinking_delta", "delta": thinking}}),
        run_event({"type": "message_update", "assistantMessageEvent": {"type": "text_delta", "delta": text}}),
        run_event({"type": "message_end", "message": {"role": "assistant", "content": [{"type": "text", "text": text}]}}),
        run_event({"type": "turn_end", "message": {"role": "assistant"}, "toolResults": []}),
        run_event({"type": "agent_end", "messages": []}),
    ]


def feed(printer: RunPrinter, events: list[RunEvent]) -> None:
    for event in events:
        printer.handle(event)


def test_final_render_is_markdown_not_source() -> None:
    console, buf = make_console()
    feed(RunPrinter(console=console), assistant_stream())
    out = buf.getvalue()
    assert "hello world" in out
    assert "Title" in out
    assert "# Title" not in out  # rendered as a heading, not raw markdown


def test_markdown_off_prints_plain_text() -> None:
    console, buf = make_console()
    feed(RunPrinter(console=console, markdown=False), assistant_stream())
    assert "# Title" in buf.getvalue()


def test_thinking_is_transient() -> None:
    console, buf = make_console()
    feed(RunPrinter(console=console), assistant_stream(thinking="secret reasoning"))
    assert "secret reasoning" not in buf.getvalue()


def test_thinking_only_message_still_prints() -> None:
    console, buf = make_console()
    feed(RunPrinter(console=console), assistant_stream(text=""))
    assert "let me think" in buf.getvalue()


def test_live_path_on_forced_terminal() -> None:
    buf = io.StringIO()
    console = Console(file=buf, width=80, force_terminal=True)
    feed(RunPrinter(console=console), assistant_stream())
    out = buf.getvalue()
    assert "hello world" in out
    assert "let me think" in out  # visible in live frames while streaming


def test_run_event_accessors() -> None:
    text_update = run_event({"type": "message_update", "assistantMessageEvent": {"type": "text_delta", "delta": "hi"}})
    assert text_update.delta == "hi"
    assert text_update.thinking_delta == ""
    thinking_update = run_event(
        {"type": "message_update", "assistantMessageEvent": {"type": "thinking_delta", "delta": "hm"}}
    )
    assert thinking_update.delta == ""
    assert thinking_update.thinking_delta == "hm"
    ended = run_event(
        {"type": "message_end", "message": {"role": "assistant", "content": [{"type": "text", "text": "a"}, {"type": "thinking", "thinking": "b"}]}}
    )
    assert ended.content == "a"
    assert run_event({"type": "agent_end"}).message is None
