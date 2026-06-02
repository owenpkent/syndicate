//! Minimal **MCP (Model Context Protocol)** client over stdio JSON-RPC.
//!
//! Spawns an MCP server as a subprocess, does the `initialize` handshake, lists
//! its tools, and calls them — then wraps each remote tool as an [`crate::Tool`]
//! so it drops straight into the agent's toolbox alongside the built-ins. This is
//! what lets the coach use *any* MCP server (filesystem, git, sqlite, a custom
//! market-data server, …) with no bespoke glue.
//!
//! Transport: newline-delimited JSON-RPC 2.0 on the server's stdin/stdout (the
//! standard MCP stdio transport). stderr is inherited for server logs. Calls are
//! serialized through a mutex, which matches the agent loop (one tool at a time).

use std::process::Stdio;
use std::sync::Arc;

use anyhow::{anyhow, bail, Context, Result};
use async_trait::async_trait;
use serde_json::{json, Value};
use tokio::io::{AsyncBufReadExt, AsyncWriteExt, BufReader, Lines};
use tokio::process::{Child, ChildStdin, ChildStdout, Command};
use tokio::sync::Mutex;

use crate::Tool;

const PROTOCOL_VERSION: &str = "2025-06-18";

/// One tool advertised by an MCP server.
#[derive(Debug, Clone)]
pub struct McpToolInfo {
    pub name: String,
    pub description: String,
    pub input_schema: Value,
}

struct Conn {
    _child: Child,
    stdin: ChildStdin,
    stdout: Lines<BufReader<ChildStdout>>,
    next_id: i64,
}

/// A connected MCP server. Cheap to clone behind an `Arc`; shared by all the
/// [`McpTool`]s it exposes.
pub struct McpClient {
    conn: Mutex<Conn>,
    pub server: String,
}

impl McpClient {
    /// Spawn `command args…`, perform the MCP `initialize` handshake, and return
    /// a ready client.
    pub async fn connect(command: &str, args: &[String]) -> Result<Self> {
        let mut child = Command::new(command)
            .args(args)
            .stdin(Stdio::piped())
            .stdout(Stdio::piped())
            .stderr(Stdio::inherit())
            .spawn()
            .with_context(|| format!("spawning MCP server '{command}'"))?;
        let stdin = child.stdin.take().ok_or_else(|| anyhow!("no stdin"))?;
        let stdout = BufReader::new(child.stdout.take().ok_or_else(|| anyhow!("no stdout"))?).lines();
        let client = Self {
            conn: Mutex::new(Conn { _child: child, stdin, stdout, next_id: 1 }),
            server: command.to_string(),
        };
        client
            .request(
                "initialize",
                json!({
                    "protocolVersion": PROTOCOL_VERSION,
                    "capabilities": {},
                    "clientInfo": {"name": "lob-engine-agent", "version": "0.1.0"}
                }),
            )
            .await
            .context("MCP initialize")?;
        client.notify("notifications/initialized", json!({})).await?;
        Ok(client)
    }

    /// Send a JSON-RPC request and read until the matching response id, skipping
    /// notifications / unrelated lines the server may interleave.
    async fn request(&self, method: &str, params: Value) -> Result<Value> {
        let mut c = self.conn.lock().await;
        let id = c.next_id;
        c.next_id += 1;
        let req = json!({"jsonrpc": "2.0", "id": id, "method": method, "params": params});
        c.stdin.write_all(format!("{}\n", serde_json::to_string(&req)?).as_bytes()).await?;
        c.stdin.flush().await?;
        loop {
            let line = match c.stdout.next_line().await? {
                Some(l) => l,
                None => bail!("MCP server '{}' closed stdout", self.server),
            };
            let Ok(v) = serde_json::from_str::<Value>(&line) else { continue };
            if v.get("id").and_then(Value::as_i64) == Some(id) {
                if let Some(err) = v.get("error") {
                    bail!("MCP error: {err}");
                }
                return Ok(v.get("result").cloned().unwrap_or(Value::Null));
            }
            // different id or a notification → ignore and keep reading
        }
    }

    async fn notify(&self, method: &str, params: Value) -> Result<()> {
        let mut c = self.conn.lock().await;
        let msg = json!({"jsonrpc": "2.0", "method": method, "params": params});
        c.stdin.write_all(format!("{}\n", serde_json::to_string(&msg)?).as_bytes()).await?;
        c.stdin.flush().await?;
        Ok(())
    }

    /// `tools/list`.
    pub async fn list_tools(&self) -> Result<Vec<McpToolInfo>> {
        parse_tools_list(&self.request("tools/list", json!({})).await?)
    }

    /// `tools/call`.
    pub async fn call_tool(&self, name: &str, arguments: Value) -> Result<String> {
        let result = self
            .request("tools/call", json!({"name": name, "arguments": arguments}))
            .await?;
        extract_tool_result(&result)
    }
}

// pure helpers — unit-testable without a live server
fn parse_tools_list(result: &Value) -> Result<Vec<McpToolInfo>> {
    let arr = result
        .get("tools")
        .and_then(Value::as_array)
        .ok_or_else(|| anyhow!("tools/list: no 'tools' array"))?;
    Ok(arr
        .iter()
        .filter_map(|t| {
            Some(McpToolInfo {
                name: t.get("name")?.as_str()?.to_string(),
                description: t.get("description").and_then(Value::as_str).unwrap_or("").to_string(),
                input_schema: t.get("inputSchema").cloned().unwrap_or_else(|| json!({"type": "object"})),
            })
        })
        .collect())
}

fn extract_tool_result(result: &Value) -> Result<String> {
    let is_error = result.get("isError").and_then(Value::as_bool).unwrap_or(false);
    let text = result
        .get("content")
        .and_then(Value::as_array)
        .map(|blocks| {
            blocks
                .iter()
                .filter_map(|b| b.get("text").and_then(Value::as_str))
                .collect::<Vec<_>>()
                .join("\n")
        })
        .unwrap_or_default();
    let text = if text.is_empty() { result.to_string() } else { text };
    if is_error {
        bail!("{text}")
    }
    Ok(text)
}

/// An MCP server's tool, presented to the agent as a [`Tool`]. The model-facing
/// name is namespaced (`<label>__<tool>`) so tools from different servers can't
/// collide; calls forward the bare name to the server.
pub struct McpTool {
    client: Arc<McpClient>,
    info: McpToolInfo,
    exposed_name: String,
}

#[async_trait]
impl Tool for McpTool {
    fn name(&self) -> &str {
        &self.exposed_name
    }
    fn description(&self) -> &str {
        &self.info.description
    }
    fn input_schema(&self) -> Value {
        self.info.input_schema.clone()
    }
    async fn call(&self, input: Value) -> Result<String> {
        self.client.call_tool(&self.info.name, input).await
    }
}

/// Connect to an MCP server and return its tools as boxed [`Tool`]s, namespaced
/// by `label`, ready to add to an [`crate::Agent`].
pub async fn connect_tools(command: &str, args: &[String], label: &str) -> Result<Vec<Box<dyn Tool>>> {
    let client = Arc::new(McpClient::connect(command, args).await?);
    let infos = client.list_tools().await?;
    Ok(infos
        .into_iter()
        .map(|info| {
            let exposed_name = format!("{label}__{}", info.name);
            Box::new(McpTool { client: Arc::clone(&client), info, exposed_name }) as Box<dyn Tool>
        })
        .collect())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn parses_tools_list() {
        let v = json!({"tools": [
            {"name": "read_file", "description": "read a file",
             "inputSchema": {"type": "object", "properties": {"path": {"type": "string"}}}},
            {"name": "write_file"}  // missing description/schema → defaults
        ]});
        let tools = parse_tools_list(&v).unwrap();
        assert_eq!(tools.len(), 2);
        assert_eq!(tools[0].name, "read_file");
        assert_eq!(tools[0].description, "read a file");
        assert_eq!(tools[0].input_schema["properties"]["path"]["type"], "string");
        assert_eq!(tools[1].description, ""); // defaulted
        assert_eq!(tools[1].input_schema["type"], "object"); // defaulted
    }

    #[test]
    fn extracts_text_content() {
        let v = json!({"content": [{"type": "text", "text": "hello"}, {"type": "text", "text": "world"}]});
        assert_eq!(extract_tool_result(&v).unwrap(), "hello\nworld");
    }

    #[test]
    fn surfaces_tool_error() {
        let v = json!({"isError": true, "content": [{"type": "text", "text": "boom"}]});
        let err = extract_tool_result(&v).unwrap_err();
        assert!(err.to_string().contains("boom"));
    }

    #[test]
    fn missing_tools_array_errors() {
        assert!(parse_tools_list(&json!({"nope": 1})).is_err());
    }
}
