//! Native Rust **AI-agent runtime** — a hand-rolled Anthropic Messages API client
//! plus the agentic tool-use loop, with zero dependency on any Python/SDK layer.
//!
//! The loop is the classic one: send `messages` → model replies; if it stops with
//! `stop_reason: "tool_use"`, execute each requested tool, append the results as a
//! user turn, and resend — until the model stops with `end_turn`.
//!
//! Wire facts baked in (see <https://platform.claude.com> Messages API):
//! - `POST https://api.anthropic.com/v1/messages`
//! - headers `x-api-key`, `anthropic-version: 2023-06-01`, `content-type: application/json`
//! - default model `claude-opus-4-8`; **no** `temperature`/`top_p`/`budget_tokens`
//!   (those 400 on Opus 4.7+). Thinking is left off in v1 for a simple, lossless
//!   message round-trip (adaptive thinking is a planned add).
//! - `cache_control: {type: "ephemeral"}` on the system block (prompt caching).
//!
//! The runtime is domain-agnostic: register any [`Tool`] and point it at any task —
//! ops/config, market data, research. See [`tools`] for the starter set.

use anyhow::{bail, Context, Result};
use async_trait::async_trait;
use serde::{Deserialize, Serialize};
use serde_json::{json, Value};

pub mod tools;

const API_URL: &str = "https://api.anthropic.com/v1/messages";
const API_VERSION: &str = "2023-06-01";
pub const DEFAULT_MODEL: &str = "claude-opus-4-8";
const DEFAULT_MAX_TOKENS: u32 = 16_000;

// ---------------------------------------------------------------- wire types
/// A content block — the tagged union the Messages API uses inside `content`.
/// We model the blocks we send (`text`, `tool_result`) and receive (`text`,
/// `thinking`, `tool_use`); anything else round-trips through `Other` losslessly
/// enough for v1 (we only re-send blocks we produced).
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(tag = "type", rename_all = "snake_case")]
pub enum Block {
    Text {
        text: String,
    },
    Thinking {
        thinking: String,
    },
    ToolUse {
        id: String,
        name: String,
        input: Value,
    },
    ToolResult {
        tool_use_id: String,
        content: String,
        #[serde(default, skip_serializing_if = "is_false")]
        is_error: bool,
    },
    #[serde(other)]
    Other,
}

fn is_false(b: &bool) -> bool {
    !*b
}

/// One conversation turn. `content` is always the block array (the API also
/// accepts a bare string for user turns, but normalizing to blocks keeps the
/// loop uniform).
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Message {
    pub role: String,
    pub content: Vec<Block>,
}

impl Message {
    pub fn user(text: impl Into<String>) -> Self {
        Self { role: "user".into(), content: vec![Block::Text { text: text.into() }] }
    }
}

#[derive(Debug, Clone, Default, Deserialize)]
pub struct Usage {
    #[serde(default)]
    pub input_tokens: u32,
    #[serde(default)]
    pub output_tokens: u32,
    #[serde(default)]
    pub cache_read_input_tokens: u32,
    #[serde(default)]
    pub cache_creation_input_tokens: u32,
}

#[derive(Debug, Deserialize)]
pub struct ApiResponse {
    pub content: Vec<Block>,
    pub stop_reason: Option<String>,
    #[serde(default)]
    pub usage: Usage,
}

// ---------------------------------------------------------------- tool surface
/// A capability the agent can invoke. Implementors describe themselves (name,
/// description, JSON-schema for inputs) and execute a call.
#[async_trait]
pub trait Tool: Send + Sync {
    fn name(&self) -> &str;
    fn description(&self) -> &str;
    /// JSON Schema (object) for the tool's input.
    fn input_schema(&self) -> Value;
    /// Execute the tool. Return the text result, or an error (surfaced to the
    /// model as an `is_error` tool_result so it can recover).
    async fn call(&self, input: Value) -> Result<String>;

    /// The `tools[]` entry sent to the API.
    fn definition(&self) -> Value {
        json!({
            "name": self.name(),
            "description": self.description(),
            "input_schema": self.input_schema(),
        })
    }
}

// ---------------------------------------------------------------- client
/// Thin async client over `POST /v1/messages`.
pub struct Client {
    http: reqwest::Client,
    api_key: String,
    pub model: String,
    pub max_tokens: u32,
}

impl Client {
    pub fn new(api_key: impl Into<String>) -> Self {
        Self {
            http: reqwest::Client::new(),
            api_key: api_key.into(),
            model: DEFAULT_MODEL.to_string(),
            max_tokens: DEFAULT_MAX_TOKENS,
        }
    }

    /// Build from `ANTHROPIC_API_KEY` (env, or an `ANTHROPIC_API_KEY=` line in `.env`).
    pub fn from_env() -> Result<Self> {
        if let Ok(k) = std::env::var("ANTHROPIC_API_KEY") {
            if !k.is_empty() {
                return Ok(Self::new(k));
            }
        }
        if let Ok(contents) = std::fs::read_to_string(".env") {
            for line in contents.lines() {
                if let Some(v) = line.strip_prefix("ANTHROPIC_API_KEY=") {
                    let v = v.trim().trim_matches('"');
                    if !v.is_empty() {
                        return Ok(Self::new(v));
                    }
                }
            }
        }
        bail!("ANTHROPIC_API_KEY not set (env or .env)")
    }

    /// Assemble the request body. Pure + testable — no I/O.
    pub fn build_body(&self, system: &str, messages: &[Message], tools: &[Value]) -> Value {
        json!({
            "model": self.model,
            "max_tokens": self.max_tokens,
            "system": [{
                "type": "text",
                "text": system,
                "cache_control": {"type": "ephemeral"}  // prompt caching
            }],
            "messages": messages,
            "tools": tools,
        })
    }

    async fn send(&self, system: &str, messages: &[Message], tools: &[Value]) -> Result<ApiResponse> {
        let body = self.build_body(system, messages, tools);
        let resp = self
            .http
            .post(API_URL)
            .header("x-api-key", &self.api_key)
            .header("anthropic-version", API_VERSION)
            .header("content-type", "application/json")
            .json(&body)
            .send()
            .await
            .context("messages request")?;
        let status = resp.status();
        let text = resp.text().await.context("read response body")?;
        if !status.is_success() {
            bail!("API {status}: {text}");
        }
        serde_json::from_str(&text).with_context(|| format!("decode response: {text}"))
    }
}

// ---------------------------------------------------------------- agent loop
/// An agent: a system prompt, a toolbox, and the loop that drives them.
pub struct Agent {
    client: Client,
    system: String,
    tools: Vec<Box<dyn Tool>>,
    max_turns: usize,
}

/// What happened on one `run` — final text plus a tiny trace for visibility.
#[derive(Debug, Default)]
pub struct RunOutcome {
    pub text: String,
    pub turns: usize,
    pub tool_calls: usize,
    pub usage: Usage,
}

impl Agent {
    pub fn new(client: Client, system: impl Into<String>, tools: Vec<Box<dyn Tool>>) -> Self {
        Self { client, system: system.into(), tools, max_turns: 16 }
    }

    pub fn max_turns(mut self, n: usize) -> Self {
        self.max_turns = n;
        self
    }

    fn tool_defs(&self) -> Vec<Value> {
        self.tools.iter().map(|t| t.definition()).collect()
    }

    fn find(&self, name: &str) -> Option<&dyn Tool> {
        self.tools.iter().find(|t| t.name() == name).map(|b| b.as_ref())
    }

    /// Run the agentic loop until the model ends its turn (or `max_turns`).
    /// `on_event` is called with human-readable progress lines (tool calls, etc.).
    pub async fn run<F: FnMut(&str)>(&self, user: &str, mut on_event: F) -> Result<RunOutcome> {
        let mut messages = vec![Message::user(user)];
        let tool_defs = self.tool_defs();
        let mut out = RunOutcome::default();

        for _ in 0..self.max_turns {
            out.turns += 1;
            let resp = self.client.send(&self.system, &messages, &tool_defs).await?;
            out.usage = resp.usage.clone();

            // Surface any text the model produced this turn.
            for b in &resp.content {
                if let Block::Text { text } = b {
                    out.text = text.clone();
                }
            }

            // Record the assistant turn verbatim (required so tool_use ids line up).
            messages.push(Message { role: "assistant".into(), content: resp.content.clone() });

            let tool_uses: Vec<(&str, &str, &Value)> = resp
                .content
                .iter()
                .filter_map(|b| match b {
                    Block::ToolUse { id, name, input } => Some((id.as_str(), name.as_str(), input)),
                    _ => None,
                })
                .collect();

            if resp.stop_reason.as_deref() == Some("end_turn") || tool_uses.is_empty() {
                return Ok(out);
            }

            // Execute each requested tool, collect results into one user turn.
            let mut results = Vec::with_capacity(tool_uses.len());
            for (id, name, input) in tool_uses {
                out.tool_calls += 1;
                on_event(&format!("tool: {name}({})", compact(input)));
                let (content, is_error) = match self.find(name) {
                    Some(tool) => match tool.call(input.clone()).await {
                        Ok(s) => (truncate(s, 16_000), false),
                        Err(e) => (format!("error: {e}"), true),
                    },
                    None => (format!("error: unknown tool '{name}'"), true),
                };
                results.push(Block::ToolResult { tool_use_id: id.to_string(), content, is_error });
            }
            messages.push(Message { role: "user".into(), content: results });
        }
        bail!("agent did not finish within {} turns", self.max_turns)
    }
}

fn compact(v: &Value) -> String {
    truncate(v.to_string(), 120)
}

fn truncate(s: String, max: usize) -> String {
    if s.len() <= max {
        s
    } else {
        let mut t: String = s.chars().take(max).collect();
        t.push_str("…[truncated]");
        t
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn tool_use_block_deserializes() {
        let j = r#"{"type":"tool_use","id":"toolu_1","name":"bash","input":{"cmd":"ls"}}"#;
        let b: Block = serde_json::from_str(j).unwrap();
        match b {
            Block::ToolUse { id, name, input } => {
                assert_eq!(id, "toolu_1");
                assert_eq!(name, "bash");
                assert_eq!(input["cmd"], "ls");
            }
            _ => panic!("wrong variant"),
        }
    }

    #[test]
    fn unknown_block_is_other_not_error() {
        let j = r#"{"type":"server_tool_use","id":"x","name":"web_search","input":{}}"#;
        assert!(matches!(serde_json::from_str::<Block>(j).unwrap(), Block::Other));
    }

    #[test]
    fn tool_result_serializes_minimally() {
        let b = Block::ToolResult {
            tool_use_id: "toolu_1".into(),
            content: "ok".into(),
            is_error: false,
        };
        let v = serde_json::to_value(&b).unwrap();
        assert_eq!(v["type"], "tool_result");
        assert_eq!(v["tool_use_id"], "toolu_1");
        assert_eq!(v["content"], "ok");
        assert!(v.get("is_error").is_none(), "is_error omitted when false");
    }

    #[test]
    fn request_body_shape() {
        let c = Client::new("sk-test");
        let body = c.build_body("be terse", &[Message::user("hi")], &[json!({"name": "t"})]);
        assert_eq!(body["model"], DEFAULT_MODEL);
        assert_eq!(body["max_tokens"], DEFAULT_MAX_TOKENS);
        assert_eq!(body["system"][0]["cache_control"]["type"], "ephemeral");
        assert_eq!(body["messages"][0]["role"], "user");
        assert_eq!(body["tools"][0]["name"], "t");
        // sampling params must be absent (they 400 on Opus 4.7+)
        assert!(body.get("temperature").is_none());
        assert!(body.get("top_p").is_none());
    }

    struct EchoTool;
    #[async_trait]
    impl Tool for EchoTool {
        fn name(&self) -> &str {
            "echo"
        }
        fn description(&self) -> &str {
            "echoes its input"
        }
        fn input_schema(&self) -> Value {
            json!({"type": "object", "properties": {"msg": {"type": "string"}}, "required": ["msg"]})
        }
        async fn call(&self, input: Value) -> Result<String> {
            Ok(input["msg"].as_str().unwrap_or("").to_string())
        }
    }

    #[tokio::test]
    async fn tool_dispatch_by_name() {
        let agent = Agent::new(Client::new("k"), "sys", vec![Box::new(EchoTool)]);
        let t = agent.find("echo").expect("found");
        assert_eq!(t.call(json!({"msg": "hello"})).await.unwrap(), "hello");
        assert!(agent.find("missing").is_none());
        assert_eq!(agent.tool_defs()[0]["name"], "echo");
    }
}
