"""Minimal stdio MCP server for the test suite — stdlib only.

Just enough JSON-RPC for the sidecar's MCP client: initialize handshake,
tools/list, tools/call. Echoes the client's protocolVersion back (the SDK
rejects mismatches). Line iteration is safe here: raw CR cannot appear
inside a JSON string on the wire.
"""

import json
import sys

TOOLS = [
    {
        "name": "add",
        "description": "Add two integers.",
        "inputSchema": {
            "type": "object",
            "properties": {"a": {"type": "integer"}, "b": {"type": "integer"}},
            "required": ["a", "b"],
        },
    },
    {
        "name": "fail",
        "description": "Always fails.",
        "inputSchema": {"type": "object", "properties": {}},
    },
]


def handle(msg: dict) -> dict | None:
    method = msg.get("method")
    if method == "initialize":
        return {
            "protocolVersion": msg["params"]["protocolVersion"],
            "capabilities": {},
            "serverInfo": {"name": "test-mcp", "version": "1.0"},
        }
    if method == "notifications/initialized":
        return None  # notification — no id, no response
    if method == "tools/list":
        return {"tools": TOOLS}
    if method == "tools/call":
        name = msg["params"]["name"]
        args = msg["params"].get("arguments") or {}
        if name == "add":
            return {"content": [{"type": "text", "text": str(args["a"] + args["b"])}]}
        if name == "fail":
            return {"content": [{"type": "text", "text": "boom"}], "isError": True}
    raise ValueError(f"unknown method: {method}")


def main() -> None:
    for line in sys.stdin:
        if not line.strip():
            continue
        msg = json.loads(line)
        result = handle(msg)
        if result is not None and msg.get("id") is not None:
            sys.stdout.write(
                json.dumps({"jsonrpc": "2.0", "id": msg["id"], "result": result}) + "\n"
            )
            sys.stdout.flush()


if __name__ == "__main__":
    main()
