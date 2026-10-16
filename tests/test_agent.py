"""Agent facade: one real task against the CLAUDE.md test provider."""

import io
import os
import shutil

import pytest
from conftest import PROVIDER
from rich.console import Console

from pi2py import Agent, SidecarCommandError

pytestmark = pytest.mark.skipif(
    shutil.which("node") is None and os.environ.get("PI2PY_NODE") is None,
    reason="Node.js not available",
)


def test_agent_runs_a_task() -> None:
    agent = Agent(
        model="dots3-note-prev",
        provider=PROVIDER,
        instructions=["Answer in Chinese.", "Keep it to one sentence."],
    )
    result = agent.run("用一句话解释什么是 JSONL 协议")
    assert result.content.strip()
    agent.close()


def test_agent_bad_model_id_is_a_clean_error() -> None:
    agent = Agent(model="no-such-model", provider=PROVIDER)
    with pytest.raises(SidecarCommandError, match="no-such-model"):
        agent.run("hello")
    agent.close()


def test_agent_streams_events() -> None:
    agent = Agent(
        model="dots3-note-prev",
        provider=PROVIDER,
        instructions=["Answer in Chinese.", "Keep it to one sentence."],
    )
    events = list(agent.run("用一句话解释什么是 JSONL 协议", stream=True))
    types = [e.type for e in events]
    assert types[0] == "agent_start"
    assert types[-1] == "agent_end"
    assert "message_update" in types
    streamed_text = "".join(e.delta for e in events)
    assert streamed_text.strip()
    final = [e for e in events if e.type == "message_end" and e.content]
    assert final
    assert final[-1].content.strip() == streamed_text.strip()
    agent.close()


def test_print_response_renders_to_console() -> None:
    agent = Agent(
        model="dots3-note-prev", provider=PROVIDER, instructions=["Answer in Chinese."]
    )
    buf = io.StringIO()
    agent.print_response(
        "用一句话解释什么是 JSONL 协议", console=Console(file=buf, width=80)
    )
    assert buf.getvalue().strip()
    agent.close()
