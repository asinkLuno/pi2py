// pi2py sidecar: Node-side host for the pi agent runtime.
//
// M1 scope: transport only — JSONL framing, command dispatch, lifecycle.
// Agent wiring (prompt/event streaming, remote tools) plugs into `commands`.
//
// Protocol (mirrors pi's own RPC mode, packages/coding-agent/docs/rpc.md):
//   * stdin/stdout carry JSONL records: one JSON object per line,
//     LF-terminated, CRLF tolerated. stdout is protocol-only; diagnostics
//     go to stderr.
//   * Requests carry a string `id`; responses echo it. Responses are async —
//     correlate by id, never by order.
//   * Closing stdin requests a graceful exit.

const PROTOCOL = 1;
const MIN_NODE = [22, 19, 0];

function log(...args) {
  process.stderr.write(`[sidecar] ${args.join(" ")}\n`);
}

function send(record) {
  process.stdout.write(`${JSON.stringify(record)}\n`);
}

function nodeVersion() {
  const m = /^v(\d+)\.(\d+)\.(\d+)/.exec(process.version);
  return m ? [Number(m[1]), Number(m[2]), Number(m[3])] : null;
}

function versionAtLeast(v, min) {
  if (v === null) return false;
  for (let i = 0; i < 3; i++) {
    if (v[i] !== min[i]) return v[i] > min[i];
  }
  return true;
}

// --- commands -------------------------------------------------------------

// Lazy imports keep `ping` startup light; the pi packages load on first agent use.
async function loadPi() {
  const [{ Agent }, piAi, { openAICompletionsApi }] = await Promise.all([
    import("@earendil-works/pi-agent-core"),
    import("@earendil-works/pi-ai"),
    import("@earendil-works/pi-ai/compat"),
  ]);
  return { Agent, piAi, openAICompletionsApi };
}

// --- MCP ------------------------------------------------------------------

// Connections live for the whole sidecar process: the server process holds
// state (e.g. a DB pool) that must survive runs. Keyed by the canonical
// server definition, so identical definitions share one connection. No
// explicit teardown — the server child exits on stdin EOF when we do.
const mcpConnections = new Map();

async function loadMcp() {
  const [{ Client }, { StdioClientTransport }, { StreamableHTTPClientTransport }, { Type }] =
    await Promise.all([
      import("@modelcontextprotocol/sdk/client"),
      import("@modelcontextprotocol/sdk/client/stdio.js"),
      import("@modelcontextprotocol/sdk/client/streamableHttp.js"),
      import("typebox"),
    ]);
  return { Client, StdioClientTransport, StreamableHTTPClientTransport, Type };
}

// MCP content -> pi-ai content. text/image blocks are shape-identical, so
// they pass through; everything else (audio, resource) is stringified.
function mcpContentToPi(content) {
  return (content ?? []).map((item) =>
    item.type === "text" || item.type === "image" ? item : { type: "text", text: JSON.stringify(item) },
  );
}

// One MCP server definition -> connected client + its tools as AgentTools.
// `env` replaces the SDK's default inherited set (PATH, HOME, ...) — only
// passed when the caller provided one. Protocol-level errors from callTool
// are rethrown: the agent loop turns them into error tool results the
// model can see and react to.
async function connectMcpServer(def) {
  const { Client, StdioClientTransport, StreamableHTTPClientTransport, Type } = await loadMcp();
  let transport;
  if (def.url) {
    transport = new StreamableHTTPClientTransport(new URL(def.url));
  } else if (def.command) {
    transport = new StdioClientTransport({
      command: def.command,
      args: def.args ?? [],
      ...(def.env ? { env: def.env } : {}),
    });
  } else {
    throw new Error("mcp server needs command (stdio) or url (http)");
  }
  const client = new Client({ name: "pi2py", version: "1" });
  await client.connect(transport);
  const { tools } = await client.listTools();
  return tools.map((tool) => ({
    // shortcut: no cross-server name prefixing; a duplicate name shadows earlier ones
    name: tool.name,
    label: tool.name,
    description: tool.description ?? "",
    parameters: Type.Unsafe(tool.inputSchema ?? { type: "object" }),
    execute: async (toolCallId, params, signal) => {
      const result = await client.callTool({ name: tool.name, arguments: params }, undefined, { signal });
      return {
        content: mcpContentToPi(result.content),
        details: result,
        ...(result.isError ? { isError: true } : {}),
      };
    },
  }));
}

// Get-or-connect every server in `defs`; failures evict the cache entry so
// a later run can retry.
async function mcpTools(defs) {
  const tools = [];
  for (const def of defs ?? []) {
    const key = JSON.stringify(def);
    if (!mcpConnections.has(key)) {
      mcpConnections.set(
        key,
        connectMcpServer(def).catch((err) => {
          mcpConnections.delete(key);
          throw err;
        }),
      );
    }
    tools.push(...(await mcpConnections.get(key)));
  }
  return tools;
}

// Wire shape for forwarded agent events: message_update keeps only the delta
// (the event's partial message is the whole transcript-so-far — O(n²) bytes
// over a stream); message_end resyncs complete state.
function slimAgentEvent(event) {
  if (event.type !== "message_update") return event;
  const ame = event.assistantMessageEvent ?? {};
  return {
    type: event.type,
    assistantMessageEvent: { type: ame.type, ...(ame.delta !== undefined ? { delta: ame.delta } : {}) },
  };
}

const commands = {
  ping: () => ({ pong: true, node: process.version }),

  echo: (msg) => ({ echo: msg.payload ?? null }),

  // Verify the bundled npm install actually loads the pi packages.
  selfcheck: async () => {
    const core = await import("@earendil-works/pi-agent-core");
    const ai = await import("@earendil-works/pi-ai");
    return {
      agentCoreLoaded: typeof core.Agent === "function",
      piAiLoaded: typeof ai.createModels === "function",
    };
  },

  // Transport test hooks (exercised by the Python test suite). Event records
  // echo the request id when present, so the client can route them per request.
  "emit-test-event": (msg) => {
    send({ type: "event", kind: "test", n: msg.n ?? 1, ...(msg.id !== undefined ? { id: msg.id } : {}) });
    return { emitted: true };
  },

  // Run one prompt to completion on a fresh in-process agent.
  //
  // `provider` is a models.json custom-provider entry (id, name, baseUrl,
  // api, apiKey, compat, models[]); `model` picks one of its model ids.
  // `mcpServers[]` (command/args/env for stdio, or url for streamable HTTP)
  // are connected and their tools exposed to the agent.
  // With `stream: true`, pi AgentEvents are forwarded as event records
  // (tagged with this request's id) before the response.
  "agent.run": async (msg) => {
    const { Agent, piAi, openAICompletionsApi } = await loadPi();

    const entry = msg.provider ?? {};
    if (!entry.id || !entry.baseUrl || !Array.isArray(entry.models)) {
      throw new Error("provider entry needs id, baseUrl and models[]");
    }
    const apiImpls = { "openai-completions": openAICompletionsApi };
    const api = entry.api ?? "openai-completions";
    const apiImpl = apiImpls[api];
    if (!apiImpl) {
      throw new Error(`unsupported provider api: ${api}`);
    }

    const provider = piAi.createProvider({
      id: entry.id,
      name: entry.name ?? entry.id,
      baseUrl: entry.baseUrl,
      auth: {
        // Static key from the provider entry — models.json carries it inline.
        apiKey: {
          name: `${entry.name ?? entry.id} API key`,
          resolve: async () => ({ auth: { apiKey: entry.apiKey } }),
        },
      },
      models: entry.models.map((m) => ({
        ...m,
        api,
        provider: entry.id,
        baseUrl: entry.baseUrl,
        ...(entry.compat ? { compat: entry.compat } : {}),
      })),
      api: apiImpl(),
    });
    const models = piAi.createModels();
    models.setProvider(provider);
    const model = models.getModel(entry.id, msg.model);
    if (!model) {
      throw new Error(
        `model ${msg.model} not in provider ${entry.id}; available: ${entry.models.map((m) => m.id).join(", ")}`,
      );
    }

    // MCP servers connect (once per process, cached) before any model call,
    // so a dead server fails the command without touching the provider.
    const tools = await mcpTools(msg.mcpServers);

    const agent = new Agent({
      streamFn: (m, context, options) => models.streamSimple(m, context, options),
      initialState: {
        model,
        ...(msg.instructions ? { systemPrompt: msg.instructions } : {}),
        ...(tools.length > 0 ? { tools } : {}),
      },
      ...(msg.sessionId ? { sessionId: msg.sessionId } : {}),
    });
    const unsubscribe = msg.stream
      ? agent.subscribe((event) => {
          try {
            send({ type: "event", kind: "agent", id: msg.id ?? null, event: slimAgentEvent(event) });
          } catch (err) {
            log("event forward failed:", err?.message ?? String(err));
          }
        })
      : null;
    try {
      await agent.prompt(msg.input);
      await agent.waitForIdle();
    } finally {
      unsubscribe?.();
    }

    if (agent.state.errorMessage) {
      throw new Error(agent.state.errorMessage);
    }
    const last = [...agent.state.messages].reverse().find((m) => m.role === "assistant");
    if (!last) {
      throw new Error("agent produced no assistant message");
    }
    return {
      content: piAi.contentText(last.content),
      usage: last.usage ?? null,
      messages: agent.state.messages,
    };
  },

  die: (msg) => {
    log("die command received");
    const code = Number.isInteger(msg.code) ? msg.code : 1;
    setTimeout(() => process.exit(code), 25);
    return { dying: true };
  },
};

async function handle(msg) {
  const fn = commands[msg.type];
  if (fn === undefined) {
    return { ok: false, error: `unknown command: ${msg.type}` };
  }
  try {
    return { ok: true, data: await fn(msg) };
  } catch (err) {
    // Command failures (bad provider config, network, model errors) must
    // surface as error responses, not kill the process.
    return { ok: false, error: err?.message ?? String(err) };
  }
}

// --- JSONL framing --------------------------------------------------------
// Split on \n only. Node's readline must NOT be used: it also splits on
// U+2028/U+2029, which are legal inside JSON strings.

let buf = "";
let busy = false;
const queue = [];

process.stdin.setEncoding("utf8");

process.stdin.on("data", (chunk) => {
  buf += chunk;
  let idx;
  while ((idx = buf.indexOf("\n")) >= 0) {
    const line = buf.slice(0, idx);
    buf = buf.slice(idx + 1);
    queue.push(line.replace(/\r$/, ""));
  }
  void drain();
});

async function drain() {
  if (busy) return;
  busy = true;
  try {
    while (queue.length > 0) {
      const line = queue.shift();
      if (line.trim() === "") continue;
      let msg;
      try {
        msg = JSON.parse(line);
      } catch {
        send({ type: "response", id: null, success: false, error: "parse error: line is not JSON" });
        continue;
      }
      const outcome = await handle(msg);
      if (outcome.ok) {
        send({ type: "response", id: msg.id ?? null, success: true, data: outcome.data });
      } else {
        send({ type: "response", id: msg.id ?? null, success: false, error: outcome.error });
      }
    }
  } finally {
    busy = false;
  }
}

process.stdin.on("end", () => {
  log("stdin closed, exiting");
  process.exit(0);
});

process.on("uncaughtException", (err) => {
  log("uncaught exception:", err?.stack ?? String(err));
  try {
    send({ type: "fatal", message: String(err?.message ?? err) });
  } catch {
    // stdout already gone; stderr log above is all we can do
  }
  process.exit(1);
});

process.on("unhandledRejection", (reason) => {
  log("unhandled rejection:", String(reason));
  try {
    send({ type: "fatal", message: String(reason) });
  } catch {
    // stdout already gone
  }
  process.exit(1);
});

// --- startup --------------------------------------------------------------

const version = nodeVersion();
if (!versionAtLeast(version, MIN_NODE)) {
  send({ type: "fatal", message: `Node >= ${MIN_NODE.join(".")} required, found ${process.version}` });
  process.exit(1);
}

send({ type: "ready", protocol: PROTOCOL, node: process.version });
log("sidecar ready");
