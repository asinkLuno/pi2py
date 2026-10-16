"""MCP wiring: the sidecar connects to a stdio MCP server and the model
calls its tools (docs/tools-mcp.md)."""

import os
import shutil
import sys
from pathlib import Path

import pytest
from conftest import PROVIDER

from pi2py import Agent, McpServer, SidecarCommandError

pytestmark = pytest.mark.skipif(
    shutil.which("node") is None and os.environ.get("PI2PY_NODE") is None,
    reason="Node.js not available",
)

TEST_SERVER = McpServer(
    command=sys.executable, args=[str(Path(__file__).parent / "mcp_server.py")]
)


def test_bad_server_definition_fails_at_construction() -> None:
    with pytest.raises(ValueError):
        McpServer().payload()  # neither command nor url
    with pytest.raises(ValueError):
        McpServer(command="x", url="http://x").payload()  # both


def test_unreachable_server_fails_before_model_call() -> None:
    agent = Agent(
        model="dots3-note-prev",
        provider=PROVIDER,
        mcp_servers=[McpServer(command="/nonexistent-mcp-server")],
    )
    with pytest.raises(SidecarCommandError):
        agent.run("hi")


def test_model_calls_tool() -> None:
    agent = Agent(model="dots3-note-prev", provider=PROVIDER, mcp_servers=[TEST_SERVER])
    result = agent.run("Use the add tool to compute 2 + 3. Reply with just the number.")
    assert result.content.strip() == "5"


def test_tool_error_is_visible_to_model() -> None:
    agent = Agent(model="dots3-note-prev", provider=PROVIDER, mcp_servers=[TEST_SERVER])
    result = agent.run(
        "Call the fail tool once, then reply with exactly the text it returned."
    )
    assert "boom" in result.content.lower()
