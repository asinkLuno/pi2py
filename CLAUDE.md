# pi2py

从 Python 非交互式调用 [pi](https://github.com/earendil-works/pi) 的 `packages/agent`（`@earendil-works/pi-agent-core`）的桥接库。目标 API 风格仿 agno（`Agent.run()` / 流式 RunEvent / Python 函数当工具）。

## 架构（M1 已落地）

- `src/pi2py/node/`：随 wheel 分发的 Node sidecar（`sidecar.mjs` + 锁死的 `package.json`/`package-lock.json`，依赖 `@earendil-works/pi-agent-core@1.3.0` + `pi-ai@1.3.0`）
- `config.py`：`Pi2pyConfig`（pydantic-settings，`$PI2PY_*` 环境变量）
- `agent.py`：agno 风格 `Agent` 门面（`model`/`provider`/`instructions`/`session_id`；`run(input) -> RunOutput`，`run(input, stream=True) -> Iterator[RunEvent]`，`print_response(input)`）；provider 用 models.json 自定义条目，sidecar 侧 `agent.run` 命令用 `createProvider` + `openAICompletionsApi()` 装配，`stream: true` 时经 `Agent.subscribe` 转发 AgentEvent（`message_update` 只带 delta）
- `render.py`：`RunPrinter`——把流式 RunEvent 用 rich 渲染到终端（dim 思考 + Live markdown，思考不落最终输出），驱动 `Agent.print_response`
- `_install.py`：首次使用时把 sidecar 拷到 `~/.cache/pi2py/sidecar-<lockhash>/` 并 `npm ci --ignore-scripts`（`$PI2PY_SIDECAR_DIR` 可覆盖）
- `_node.py`：Node ≥22.19 发现（`$PI2PY_NODE` → PATH → nvm/volta/fnm/asdf，nvm 取最新）
- `transport.py`：`PiProcess`——同步 JSONL 协议客户端（reader 线程分发、id 关联、双向记录、关 stdin 优雅退出、崩溃检测带 stderr tail）
- 协议规则仿 pi 自己的 RPC 模式：stdout 只走协议、严格 LF 分帧（两端都不能用 readline/str.splitlines）、响应按 id 异步关联

里程碑：M1 传输层 ✅ → M2 Agent 门面（`run` / 流式 RunEvent / `print_response` rich 渲染 ✅）→ M3 Python 远程工具（反向 RPC 回环）→ M4 steer/结构化输出等。同步门面优先，不做 asyncio 版。

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
