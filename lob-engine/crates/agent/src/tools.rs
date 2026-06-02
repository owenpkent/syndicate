//! Starter toolbox for the agent runtime: enough to make it a useful local
//! ops/coding assistant (run commands, read files). Domain tools — market book,
//! position, PnL — get added where the agent is wired into the trading sim.

use anyhow::Result;
use async_trait::async_trait;
use serde_json::{json, Value};
use tokio::process::Command;

use crate::Tool;

/// Run a shell command and return its combined stdout+stderr.
///
/// This is broad by design (a local dev assistant). A real deployment would gate
/// it behind confirmation — see the bash-vs-dedicated-tool tradeoff: a dedicated
/// tool gives the harness a typed hook it can intercept; bash gives breadth.
pub struct BashTool;

#[async_trait]
impl Tool for BashTool {
    fn name(&self) -> &str {
        "bash"
    }
    fn description(&self) -> &str {
        "Run a bash command on the local machine and return its stdout and stderr. \
         Use for inspecting the repo, running builds/tests, and editing config."
    }
    fn input_schema(&self) -> Value {
        json!({
            "type": "object",
            "properties": {
                "command": {"type": "string", "description": "The bash command to run."}
            },
            "required": ["command"]
        })
    }
    async fn call(&self, input: Value) -> Result<String> {
        let cmd = input["command"].as_str().unwrap_or_default();
        if cmd.is_empty() {
            anyhow::bail!("missing 'command'");
        }
        let out = Command::new("bash").arg("-lc").arg(cmd).output().await?;
        let mut s = String::new();
        s.push_str(&String::from_utf8_lossy(&out.stdout));
        let err = String::from_utf8_lossy(&out.stderr);
        if !err.trim().is_empty() {
            s.push_str("\n[stderr]\n");
            s.push_str(&err);
        }
        if !out.status.success() {
            s.push_str(&format!("\n[exit: {}]", out.status.code().unwrap_or(-1)));
        }
        Ok(if s.trim().is_empty() { "(no output)".into() } else { s })
    }
}

/// Read a UTF-8 file from disk.
pub struct ReadFileTool;

#[async_trait]
impl Tool for ReadFileTool {
    fn name(&self) -> &str {
        "read_file"
    }
    fn description(&self) -> &str {
        "Read a UTF-8 text file from the local filesystem and return its contents."
    }
    fn input_schema(&self) -> Value {
        json!({
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Path to the file to read."}
            },
            "required": ["path"]
        })
    }
    async fn call(&self, input: Value) -> Result<String> {
        let path = input["path"].as_str().unwrap_or_default();
        if path.is_empty() {
            anyhow::bail!("missing 'path'");
        }
        Ok(tokio::fs::read_to_string(path).await?)
    }
}
