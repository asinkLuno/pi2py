"""Agent facade: one real task against the CLAUDE.md test provider."""

import io
import json
import os
import shutil
from pathlib import Path

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


def test_audit_log_writes_one_file_per_run(tmp_path: Path) -> None:
    agent = Agent(
        model="dots3-note-prev",
        provider=PROVIDER,
        name="t",
        audit_dir=tmp_path / "logs",
        instructions="Answer briefly.",
    )
    result = agent.run("hi")
    agent.close()
    files = sorted((tmp_path / "logs").glob("t-*.jsonl"))
    assert len(files) == 1
    entry = json.loads(files[0].read_text(encoding="utf-8"))
    assert entry["name"] == "t"
    assert entry["input"] == "hi"
    assert entry["content"] == result.content
    assert entry["usage"]["totalTokens"] > 0


def test_audit_log_records_failures(tmp_path: Path) -> None:
    agent = Agent(model="no-such-model", provider=PROVIDER, name="bad", audit_dir=tmp_path)
    with pytest.raises(SidecarCommandError, match="no-such-model"):
        agent.run("hello")
    agent.close()
    entry = json.loads(next(tmp_path.glob("bad-*.jsonl")).read_text(encoding="utf-8"))
    assert "no-such-model" in entry["error"]
    assert "content" not in entry


def test_audit_log_covers_streaming_runs(tmp_path: Path) -> None:
    agent = Agent(model="dots3-note-prev", provider=PROVIDER, name="s", audit_dir=tmp_path)
    events = list(agent.run("hi", stream=True))
    assert events
    # the audit entry is written before the stream's end sentinel, so it
    # is on disk by the time the generator is exhausted
    entry = json.loads(next(tmp_path.glob("s-*.jsonl")).read_text(encoding="utf-8"))
    assert entry["content"].strip()
    assert entry["usage"]["totalTokens"] > 0
    agent.close()
