//! `agent` — run the native Rust AI-agent runtime from the CLI.
//!
//! ```text
//! export ANTHROPIC_API_KEY=sk-ant-...
//! cargo run -p agent -- "what crates are in this workspace and do they build?"
//! ```
//! The agent has `bash` and `read_file` tools, so it can inspect the repo, run
//! builds/tests, and read config to answer.

use agent::tools::{BashTool, ReadFileTool};
use agent::{Agent, Client, Tool};

const SYSTEM: &str = "You are a native Rust agent embedded in a quant-systems repo \
(lob-engine: a market-data/order-book/replay engine, plus a Python research lab). \
You have `bash` and `read_file` tools to inspect the repo, run builds/tests, and edit \
config. Be concise and concrete. Prefer reading/running over guessing. When done, give a \
short, direct answer.";

#[tokio::main]
async fn main() {
    let prompt: String = std::env::args().skip(1).collect::<Vec<_>>().join(" ");
    if prompt.trim().is_empty() {
        eprintln!("usage: agent \"<your request>\"");
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

    let tools: Vec<Box<dyn Tool>> = vec![Box::new(BashTool), Box::new(ReadFileTool)];
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
