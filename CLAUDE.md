# pi2py

从 Python 非交互式调用 [pi](https://github.com/earendil-works/pi) 的 `packages/agent`（`@earendil-works/pi-agent-core`）的桥接库。目标 API 风格仿 agno（`Agent.run()` / 流式 RunEvent；工具经 MCP 接入，不做反向 RPC——见 `docs/tools-mcp.md`）。

## 架构（M1 已落地）

- `src/pi2py/node/`：随 wheel 分发的 Node sidecar（`sidecar.mjs` + 锁死的 `package.json`/`package-lock.json`，依赖 `@earendil-works/pi-agent-core@1.3.0` + `pi-ai@1.3.0`）
- `config.py`：`Pi2pyConfig`（pydantic-settings，`$PI2PY_*` 环境变量）
- `agent.py`：agno 风格 `Agent` 门面（`model`/`provider`/`instructions`/`skills`/`mcp_servers`/`session_id`/`name`/`audit_dir`；`run(input) -> RunOutput`，`run(input, stream=True) -> Iterator[RunEvent]`，`print_response(input)`）；`audit_dir` 开启每 run 审计：`_write_audit` 落一条 `<name>-<时间戳>.jsonl`（input/content/跨消息聚合 usage/error，目录自建，写失败只报 stderr 不毁 run）。provider 用 models.json 自定义条目，sidecar 侧 `agent.run` 命令用 `createProvider` + `openAICompletionsApi()` 装配，`stream: true` 时经 `Agent.subscribe` 转发 AgentEvent（`message_update` 只带 delta）。`skills=[路径...]` 强制注入：`load_skill()` 读 SKILL.md（目录或文件均可）、剥掉 YAML frontmatter、拼进 instructions；`run_skill(path, input)` 是 `/skill:name args` 等效物：路径由调用方显传入（不做任何名字发现/目录扫描），正文仅注入本次 run——纯 Python 侧，不动 sidecar。`mcp_servers=[McpServer(...)]`（stdio 的 command/args/env 或 HTTP 的 url）：sidecar 侧经 `@modelcontextprotocol/sdk` 连接（进程级缓存）、tools 转 `AgentTool`（`Type.Unsafe` 包 inputSchema）进 `initialState.tools`
- `render.py`：`RunPrinter`——把流式 RunEvent 用 rich 渲染到终端（dim 思考 + Live markdown，思考不落最终输出），驱动 `Agent.print_response`
- `_install.py`：首次使用时把 sidecar 拷到 `~/.cache/pi2py/sidecar-<lockhash>/` 并 `npm ci --ignore-scripts`（`$PI2PY_SIDECAR_DIR` 可覆盖）
- `_node.py`：Node ≥22.19 发现（`$PI2PY_NODE` → PATH → nvm/volta/fnm/asdf，nvm 取最新）
- `transport.py`：`PiProcess`——同步 JSONL 协议客户端（reader 线程分发、id 关联、双向记录 via `on_record` tap、关 stdin 优雅退出、崩溃检测带 stderr tail）
- 协议规则仿 pi 自己的 RPC 模式：stdout 只走协议、严格 LF 分帧（两端都不能用 readline/str.splitlines）、响应按 id 异步关联

里程碑：M1 传输层 ✅ → M2 Agent 门面（`run` / 流式 RunEvent / `print_response` rich 渲染 ✅）→ M3 工具走 MCP（连接用户 MCP server ✅，决策与设计见 `docs/tools-mcp.md`）→ M4 steer/结构化输出等。同步门面优先，不做 asyncio 版。

## 测试用 Provider

测试真实模型调用时使用下面这个 OpenAI 兼容端点（models.json 格式的自定义 provider 配置）：

```json
"dianxian-note": {
  "name": "Diandian Note Prev",
  "baseUrl": "https://note3-prev-api.askdiandian.com/v1",
  "api": "openai-completions",
  "apiKey": "ak_DUJWaGqkrcbM4wXZXnHbVjzNgy3MK",
  "compat": {
    "supportsStore": false,
    "supportsDeveloperRole": false,
    "supportsReasoningEffort": false,
    "maxTokensField": "max_tokens"
  },
  "models": [
    {
      "id": "dots3-note-prev",
      "name": "Dots3 Note Prev",
      "reasoning": true,
      "input": [
        "text"
      ],
      "cost": {
        "input": 0,
        "output": 0,
        "cacheRead": 0,
        "cacheWrite": 0
      },
      "contextWindow": 128000,
      "maxTokens": 16384
    }
  ]
}
```

- Provider id: `dianxian-note`，模型 id: `dots3-note-prev`
- 调用方式参考：pi-ai 的 `createProvider` + `openAICompletionsApi()`，compat 需照抄上面配置
