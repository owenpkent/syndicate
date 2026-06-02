//! End-to-end MCP stdio transport test: drive the real `McpClient` against a
//! minimal JSON-RPC MCP server (a few lines of Python). Proves the handshake,
//! `tools/list`, and `tools/call` round-trip over an actual subprocess pipe.
//! Skips cleanly if `python3` isn't available.

use agent::mcp::McpClient;

const SERVER: &str = r#"
import sys, json
def send(o):
    sys.stdout.write(json.dumps(o) + "\n"); sys.stdout.flush()
for line in sys.stdin:
    line = line.strip()
    if not line: continue
    msg = json.loads(line); mid = msg.get("id"); method = msg.get("method")
    if method == "initialize":
        send({"jsonrpc":"2.0","id":mid,"result":{"protocolVersion":"2025-06-18","capabilities":{},"serverInfo":{"name":"t","version":"0"}}})
    elif method == "notifications/initialized":
        pass
    elif method == "tools/list":
        send({"jsonrpc":"2.0","id":mid,"result":{"tools":[{"name":"ping","description":"returns pong","inputSchema":{"type":"object"}}]}})
    elif method == "tools/call":
        name = msg["params"]["name"]
        if name == "ping":
            send({"jsonrpc":"2.0","id":mid,"result":{"content":[{"type":"text","text":"pong"}]}})
        else:
            send({"jsonrpc":"2.0","id":mid,"result":{"isError":True,"content":[{"type":"text","text":"unknown tool"}]}})
    elif mid is not None:
        send({"jsonrpc":"2.0","id":mid,"error":{"code":-32601,"message":"method not found"}})
"#;

#[tokio::test]
async fn stdio_roundtrip_against_python_server() {
    if std::process::Command::new("python3").arg("--version").output().is_err() {
        eprintln!("skipping: python3 not available");
        return;
    }
    let args = vec!["-c".to_string(), SERVER.to_string()];
    let client = McpClient::connect("python3", &args).await.expect("connect + handshake");

    let tools = client.list_tools().await.expect("tools/list");
    assert_eq!(tools.len(), 1);
    assert_eq!(tools[0].name, "ping");
    assert_eq!(tools[0].description, "returns pong");

    let out = client.call_tool("ping", serde_json::json!({})).await.expect("tools/call");
    assert_eq!(out, "pong");

    // an erroring tool result surfaces as Err
    assert!(client.call_tool("nope", serde_json::json!({})).await.is_err());
}
