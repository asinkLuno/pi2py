"""Agent facade: one real task against the CLAUDE.md test provider."""

import os
import shutil

import pytest

from pi2py import Agent, SidecarCommandError

pytestmark = pytest.mark.skipif(
    shutil.which("node") is None and os.environ.get("PI2PY_NODE") is None,
    reason="Node.js not available",
)

# The OpenAI-compatible test provider documented in CLAUDE.md, as a
# models.json custom-provider entry.
PROVIDER = {
    "id": "dianxian-note",
    "name": "Diandian Note Prev",
    "baseUrl": "https://note3-prev-api.askdiandian.com/v1",
    "api": "openai-completions",
    "apiKey": "ak_DUJWaGqkrcbM4wXZXnHbVjzNgy3MK",
    "compat": {
        "supportsStore": False,
        "supportsDeveloperRole": False,
        "supportsReasoningEffort": False,
        "maxTokensField": "max_tokens",
    },
    "models": [
        {
            "id": "dots3-note-prev",
            "name": "Dots3 Note Prev",
            "reasoning": True,
            "input": ["text"],
            "cost": {"input": 0, "output": 0, "cacheRead": 0, "cacheWrite": 0},
            "contextWindow": 128000,
            "maxTokens": 16384,
        }
    ],
}


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
