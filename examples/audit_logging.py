"""Built-in audit logging: ``Agent(audit_dir=..., name=...)``.

Every ``run()``/``run_skill()`` writes one summary JSONL entry — input,
output, aggregated usage, or the error — to
``<audit_dir>/<name>-<timestamp>.jsonl``. No wrapper, no logger
dependency: call ``agent.run()`` directly and the files appear. The
directory is created if missing; failures are logged and re-raised; an
unwritable audit dir never loses a finished model call (stderr
complaint instead).
"""

import sys
from pathlib import Path

from pi2py import Agent, McpServer

# models.json custom-provider entry (CLAUDE.md "测试用 Provider")
PROVIDER: dict = {
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

# The repo's test MCP server (an `add` tool); point this at your own.
TEST_MCP_SERVER = str(
    Path(__file__).resolve().parent.parent / "tests" / "mcp_server.py"
)


def main() -> None:
    agent = Agent(
        model="dots3-note-prev",
        provider=PROVIDER,
        instructions=["Answer in Chinese.", "Keep it to one sentence."],
        name="compute",
        audit_dir="logs",
        mcp_servers=[McpServer(command=sys.executable, args=[TEST_MCP_SERVER])],
    )
    with agent:
        agent.run("用一句话解释什么是 JSONL 协议")
        result = agent.run("用 add 工具算 123 + 459")
        # RunOutput.usage is the LAST turn only; audit entries carry the
        # whole-run aggregate (the two differ on tool-using runs).
        print("last-turn usage:", result.usage)
    print("\nlog files:")
    for f in sorted(Path("logs").glob("compute-*.jsonl")):
        print(" ", f)


if __name__ == "__main__":
    main()
