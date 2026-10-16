# 工具调用：MCP 路线

> 决策（2026-10-16）：M3 的工具能力**不走反向 RPC 回环，改走 MCP**。业务里的数据库工具（原 agno `tools=[...]` 封装的 Python 函数）由 MCP server 提供，pi2py 负责让 sidecar 里的 agent 连上它。

## 为什么放弃反向 RPC

原方案是 sidecar 为每个 Python 工具合成一个 `AgentTool`，`execute()` 经现有 JSONL 管道反向调用 Python。调研后发现两个问题：

1. **结构性改动**：sidecar 的 `drain()` 循环在命令 await 期间持着 `busy` 标志，`agent.run` 执行中无法处理 Python 回传的 `tool_response`——工具的 `execute()` 永远等不到结果，死锁。必须把 stdin 帧泵和命令 await 解耦（按 id 路由到 pending promise），这是对 M1 传输层的重写级改动。
1. **自定义协议无生态**：反向 RPC 的协议只有 pi2py 自己用；MCP 是标准协议，Python 侧有 FastMCP，TS 侧有官方 SDK，都是现成的。

## 架构

```
Python 业务进程                        sidecar（Node）
┌──────────────────────┐              ┌─────────────────────────────┐
│ DB 连接池 + 工具函数   │   MCP stdio  │ MCP client ─→ AgentTool[]    │
│ FastMCP @mcp.tool()  │ ◄──────────► │        pi-agent-core Agent   │
└──────────────────────┘              └─────────────────────────────┘
```

- **Python 侧（用户自己写，业务逻辑不动）**：agno 时代的工具函数改挂 FastMCP——`@mcp.tool()` 装饰，连接池就在 server 进程里闭包捕获，比 agno 的 db/session 注入更直接。
- **sidecar 侧（pi2py 的活）**：MCP client 把 server 的 tools 转成 pi-agent-core 的 `AgentTool`：
  - `name` / `description` / `parameters`（MCP 的 `inputSchema` 就是 JSON Schema）直接映射；pi 的参数校验走 TypeBox，裸 schema 若不过校验就用 `Type.Unsafe()` 包一层。
  - `execute()` → `tools/call`，返回内容包成 `AgentToolResult.content`（text）；MCP 的 `isError` 结果映射为 `AgentToolResult.isError`——DB 查询失败对模型可见，可改写 SQL 重试。
- **Python API**：`Agent(mcp_servers=[...])`，每项是 server 定义（stdio 的 `command`/`args`/`env`，或 streamable HTTP 的 `url`）。

首期只做上面这条：**连接用户自己的 MCP server**。`Agent(tools=[函数])` 自动包 FastMCP 是后续糖，不承诺。

## 已知事实（2026-10 调研）

- pi-agent-core 1.3.0 **不带 MCP client**——pi 的 MCP 支持长在 pi-coding-agent 包里（整套 coding agent，不引入）。sidecar 侧已选官方 `@modelcontextprotocol/sdk@^1`（v1 的 import 路径是 `…/sdk/client`、`…/sdk/client/stdio.js`、`…/sdk/client/streamableHttp.js`），外加 `typebox` 显式直接依赖（`Type.Unsafe` 包裸 JSON Schema）。
- 改 sidecar 依赖会变 package-lock → lockhash 变 → 缓存目录重装。现有 `_install.py` 机制自动处理，无额外工作。

## 用户侧写法

pi2py 不带任何 MCP 依赖——server 是用户自己的进程。stdio 模式（推荐，FastMCP 签名/docstring 自动成 schema）：

```python
# mydb/mcp_server.py
from fastmcp import FastMCP

mcp = FastMCP("db")


@mcp.tool()
def query(sql: str) -> str:
    """Run a read-only SQL query."""
    ...  # 连接池闭包捕获，进程存活期复用


if __name__ == "__main__":
    mcp.run()  # stdio
```

```python
import sys
from pi2py import Agent, McpServer

agent = Agent(
    model=...,
    provider=...,
    mcp_servers=[McpServer(command=sys.executable, args=["-m", "mydb.mcp_server"])],
)
agent.run("库里有多少用户？")  # 模型自己调 query，看到结果后作答
```

streamable HTTP 模式用 `McpServer(url="https://...")`。注意 `env` 传了就是**整体替换**默认继承环境（PATH/HOME 没了），只在真需要时传。

## 实现要点（已落地）

- sidecar 模块级连接缓存（canonical JSON 做 key）：同一 sidecar 进程里同配置 server 只连一次，DB 连接池随 server 进程存活；不同 Agent 实例共享 sidecar 时复用。
- MCP tool → `AgentTool`：`inputSchema` 经 `Type.Unsafe` 包成 TSchema；`execute` → `tools/call`，text/image content 同形直传，其余类型 stringify；`isError` 映射为 `AgentToolResult.isError`（对模型可见，可自我修正）；协议级异常 rethrow，由 pi loop 转成 error tool result。
- 连接发生在 `agent.run` 构造 Agent 之前——server 起不来时整条命令失败，不烧模型调用。
- 生命周期：sidecar 退出 → stdio 管道 EOF → server 子进程自退（MCP 标准机制），无显式清理。

## 后续项（不承诺）

- `Agent(tools=[函数])` 自动包 FastMCP 的糖
- MCP resources/prompts、`outputSchema` → `structuredContent` 映射
- 每 server 的 timeout 配置（SDK 默认 60s）
- 跨 server 工具名冲突的前缀消解（当前：同名时先注册者胜）

## agno → MCP 迁移映射

| agno | MCP 路线下的等效物 |
|---|---|
| `tools=[函数]` 注册 | 函数挂到 FastMCP server，`Agent(mcp_servers=[...])` |
| 签名 + docstring 自动成 schema | FastMCP 同样从签名/docstring 生成 `inputSchema` |
| `stop_after_tool_call` | 无直接对应（MCP 无此语义），需要时再看 |
| `tool_hook` | server 侧自己包，或 sidecar 的 `beforeToolCall`/`afterToolCall` |
| `db`/`session` 注入工具 | 闭包捕获连接池 |
| `show_tool_calls` | `Agent.subscribe` 的工具事件（转发通道已有） |
