//! `agent` — run the native Rust AI-agent runtime from the CLI.
//!
//! ```text
//! export ANTHROPIC_API_KEY=sk-ant-...
//! cargo run -p agent -- "what crates are in this workspace and do they build?"
//!
//! # attach MCP servers (repeatable) — their tools join the toolbox:
//! cargo run -p agent -- --mcp "npx -y @modelcontextprotocol/server-filesystem ." \
//!                       "summarize the README files"
//! ```
//! Built-in tools: `bash`, `read_file`. Plus any tools exposed by `--mcp` servers
//! (namespaced `mcp<N>__<tool>`).

use agent::mcp;
use agent::tools::{BashTool, ReadFileTool};
use agent::{Agent, Client, Tool};

const SYSTEM: &str = "You are a native Rust agent embedded in a quant-systems repo \
(lob-engine: a market-data/order-book/replay engine + AI trading-sim, plus a Python \
research lab). You have `bash` and `read_file` tools (and any attached MCP tools) to \
inspect the repo, run builds/tests, and edit config. Be concise and concrete. Prefer \
reading/running over guessing. When done, give a short, direct answer.";

#[tokio::main]
async fn main() {
    // arg parse: --mcp "<command and args>" (repeatable); everything else is the prompt
    let mut prompt = String::new();
    let mut mcp_specs: Vec<String> = Vec::new();
    let mut it = std::env::args().skip(1);
    while let Some(a) = it.next() {
        match a.as_str() {
            "--mcp" => {
                if let Some(spec) = it.next() {
                    mcp_specs.push(spec);
                }
            }
            _ => {
                if !prompt.is_empty() {
                    prompt.push(' ');
                }
                prompt.push_str(&a);
            }
        }
    }
    if prompt.trim().is_empty() {
        eprintln!("usage: agent [--mcp \"<server cmd>\"]... \"<your request>\"");
        std::process::exit(2);
    }

    let client = match Client::from_env() {
        Ok(c) => c,
        Err(e) => {
            eprintln!("error: {e}");
            eprintln!("set ANTHROPIC_API_KEY in the environment or a .env file, then retry.");
            std::process::exit(1);
        }
    };
    eprintln!("[agent] model={} — working…", client.model);

    let mut tools: Vec<Box<dyn Tool>> = vec![Box::new(BashTool), Box::new(ReadFileTool)];

    // Attach MCP servers; their tools join the toolbox.
    for (i, spec) in mcp_specs.iter().enumerate() {
        let parts: Vec<String> = spec.split_whitespace().map(String::from).collect();
        let Some((cmd, args)) = parts.split_first() else { continue };
        let label = format!("mcp{}", i + 1);
        match mcp::connect_tools(cmd, args, &label).await {
            Ok(mcp_tools) => {
                eprintln!("[agent] {label}: connected '{spec}' — {} tool(s)", mcp_tools.len());
                tools.extend(mcp_tools);
            }
            Err(e) => eprintln!("[agent] {label}: failed to connect '{spec}': {e:#}"),
        }
    }

    let agent = Agent::new(client, SYSTEM, tools);

    match agent.run(&prompt, |ev| eprintln!("[agent] {ev}")).await {
        Ok(out) => {
            println!("{}", out.text);
            eprintln!(
                "[agent] done: {} turn(s), {} tool call(s), {} in / {} out tokens ({} cached)",
                out.turns,
                out.tool_calls,
                out.usage.input_tokens,
                out.usage.output_tokens,
                out.usage.cache_read_input_tokens
            );
        }
        Err(e) => {
            eprintln!("[agent] failed: {e:#}");
            std::process::exit(1);
        }
    }
}
