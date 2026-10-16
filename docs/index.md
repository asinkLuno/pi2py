# pi2py

从 Python 同步调用 [pi](https://github.com/earendil-works/pi) 的 agent 核心（`@earendil-works/pi-agent-core`）的桥接库。API 风格仿 [agno](https://github.com/agno-agi/agno)：`Agent.run()` 返回 `RunOutput`，`run(stream=True)` 产出流式 `RunEvent`，`print_response()` 用 rich 渲染到终端；工具调用（数据库等业务函数）经 MCP 接入，见[工具调用（MCP）](tools-mcp.md)。

## 快速开始

```python
from pi2py import Agent

PROVIDER = {
    "id": "dianxian-note",
    "name": "Diandian Note Prev",
    "baseUrl": "https://example.com/v1",
    "api": "openai-completions",
    "apiKey": "ak_...",
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

agent = Agent(model="dots3-note-prev", provider=PROVIDER, instructions="用中文回答。")
result = agent.run("你好")
print(result.content)

# 或者流式渲染到终端（rich：dim 思考 + Live markdown）
agent.print_response("你好")

# 或者自己消费事件流
for event in agent.run("你好", stream=True):
    print(event.delta, end="", flush=True)
```

`provider` 是 models.json 格式的自定义 provider 条目（与 pi 自己的配置同构）。首次调用会自动定位 Node.js 并安装 sidecar 依赖——这一步做了什么，见 [Sidecar 机制](sidecar.md)。

## 文档

- [Sidecar 机制](sidecar.md) — 架构总览、首次安装、Node 发现、JSONL 协议与生命周期
- [工具调用（MCP）](tools-mcp.md) — 为什么不做反向 RPC、MCP 路线架构、agno 迁移映射

## 构建文档

```bash
uv sync --group docs
uv run mkdocs serve
```
