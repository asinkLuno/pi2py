"""Shared fixtures: hermetic sidecar install dir for the whole session."""

import os

import pytest

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


@pytest.fixture(scope="session", autouse=True)
def sidecar_env(tmp_path_factory: pytest.TempPathFactory) -> None:
    os.environ["PI2PY_SIDECAR_DIR"] = str(tmp_path_factory.mktemp("sidecar"))
