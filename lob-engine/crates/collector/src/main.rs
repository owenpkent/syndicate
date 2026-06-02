//! Hyperliquid WebSocket collector -> normalized [`tape`] file.
//!
//! Subscribes to `l2Book` (top-of-book snapshots) and `trades` for a set of
//! coins, normalizes each message into a `tape::Event`, and appends it to an
//! on-disk tape. Reconnects with backoff on drop; logs throughput and an
//! ingest-latency proxy (local receive time − venue event time) every 5s.
//!
//! ```text
//! collector --coins BTC,ETH,SOL --out data/tape/hl.tape   # run until Ctrl-C
//! collector --coins BTC --secs 10 --out /tmp/probe.tape    # bounded probe
//! ```
//!
//! Note on book reconstruction: Hyperliquid's `l2Book` pushes periodic full
//! snapshots (not sequenced deltas), so this feed exercises capture + snapshot
//! handling. The sequenced-delta reconstruction problem (buffer → snapshot sync
//! → apply, with gap detection) lands when the Coinbase `level2` venue is added.

use std::fs::{create_dir_all, OpenOptions};
use std::io::BufWriter;
use std::path::Path;
use std::time::{Duration, Instant, SystemTime, UNIX_EPOCH};

use anyhow::{Context, Result};
use futures_util::{SinkExt, StreamExt};
use serde_json::Value;
use tape::{Event, Level, Side, Writer};
use tokio::time::{interval, sleep, Instant as TokioInstant};
use tokio_tungstenite::{connect_async, tungstenite::Message};

const WS_URL: &str = "wss://api.hyperliquid.xyz/ws";
const VENUE: &str = "hyperliquid";

struct Args {
    coins: Vec<String>,
    out: String,
    secs: Option<u64>,
}

fn parse_args() -> Args {
    let mut coins = vec!["BTC".to_string(), "ETH".to_string(), "SOL".to_string()];
    let mut out = "data/tape/hl.tape".to_string();
    let mut secs = None;
    let mut it = std::env::args().skip(1);
    while let Some(a) = it.next() {
        match a.as_str() {
            "--coins" => {
                if let Some(v) = it.next() {
                    coins = v.split(',').map(|s| s.trim().to_uppercase()).filter(|s| !s.is_empty()).collect();
                }
            }
            "--out" => out = it.next().unwrap_or(out),
            "--secs" => secs = it.next().and_then(|s| s.parse().ok()),
            other => eprintln!("warn: ignoring unknown arg {other}"),
        }
    }
    Args { coins, out, secs }
}

fn now_ms() -> u64 {
    SystemTime::now().duration_since(UNIX_EPOCH).unwrap().as_millis() as u64
}

fn log(msg: &str) {
    println!("{} | collector | {msg}", now_ms());
}

#[tokio::main]
async fn main() -> Result<()> {
    // rustls 0.23 requires a process-wide crypto provider to be selected.
    let _ = rustls::crypto::ring::default_provider().install_default();
    let args = parse_args();
    if let Some(parent) = Path::new(&args.out).parent() {
        if !parent.as_os_str().is_empty() {
            create_dir_all(parent).ok();
        }
    }
    let file = OpenOptions::new()
        .create(true)
        .append(true)
        .open(&args.out)
        .with_context(|| format!("opening tape {}", args.out))?;
    let mut writer = Writer::new(BufWriter::new(file));

    log(&format!(
        "starting: coins={:?} out={} {}",
        args.coins,
        args.out,
        args.secs.map(|s| format!("for {s}s")).unwrap_or_else(|| "until Ctrl-C".into())
    ));

    let deadline = args.secs.map(|s| TokioInstant::now() + Duration::from_secs(s));
    let mut backoff = 1u64;

    loop {
        match run_session(&args.coins, &mut writer, deadline).await {
            Ok(Done::Deadline) => {
                log("deadline reached; flushing and exiting");
                break;
            }
            Ok(Done::Disconnected) => {
                log(&format!("disconnected; reconnecting in {backoff}s"));
            }
            Err(e) => {
                log(&format!("session error: {e:#}; reconnecting in {backoff}s"));
            }
        }
        if let Some(d) = deadline {
            if TokioInstant::now() >= d {
                break;
            }
        }
        sleep(Duration::from_secs(backoff)).await;
        backoff = (backoff * 2).min(30);
    }

    writer.flush()?;
    log("flushed tape, bye");
    Ok(())
}

enum Done {
    Deadline,
    Disconnected,
}

async fn run_session<W: std::io::Write>(
    coins: &[String],
    writer: &mut Writer<W>,
    deadline: Option<TokioInstant>,
) -> Result<Done> {
    let (ws, _resp) = connect_async(WS_URL).await.context("ws connect")?;
    let (mut tx, mut rx) = ws.split();

    for coin in coins {
        for kind in ["l2Book", "trades"] {
            let sub = serde_json::json!({
                "method": "subscribe",
                "subscription": { "type": kind, "coin": coin }
            });
            tx.send(Message::Text(sub.to_string())).await.context("send subscribe")?;
        }
    }
    log(&format!("connected, subscribed {} coins x (l2Book, trades)", coins.len()));

    let mut stats = interval(Duration::from_secs(5));
    stats.tick().await; // consume immediate first tick
    let mut ping = interval(Duration::from_secs(30));
    ping.tick().await;

    let mut events: u64 = 0;
    let mut bytes_in: u64 = 0;
    let mut lat_sum_ms: i128 = 0;
    let mut lat_n: u64 = 0;
    let mut window_start = Instant::now();

    loop {
        let sleep_to_deadline = async {
            match deadline {
                Some(d) => tokio::time::sleep_until(d).await,
                None => std::future::pending::<()>().await,
            }
        };

        tokio::select! {
            _ = sleep_to_deadline => {
                writer.flush().ok();
                return Ok(Done::Deadline);
            }
            _ = stats.tick() => {
                let secs = window_start.elapsed().as_secs_f64().max(1e-6);
                let avg_lat = if lat_n > 0 { (lat_sum_ms / lat_n as i128) as i64 } else { 0 };
                log(&format!(
                    "+{events} ev ({:.0}/s, {:.1} KiB/s) avg ingest latency {avg_lat}ms",
                    events as f64 / secs, bytes_in as f64 / 1024.0 / secs
                ));
                writer.flush().ok();
                events = 0; bytes_in = 0; lat_sum_ms = 0; lat_n = 0;
                window_start = Instant::now();
            }
            _ = ping.tick() => {
                tx.send(Message::Text(r#"{"method":"ping"}"#.to_string())).await.ok();
            }
            msg = rx.next() => {
                let Some(msg) = msg else { return Ok(Done::Disconnected); };
                let msg = msg.context("ws read")?;
                let Message::Text(txt) = msg else { continue };
                bytes_in += txt.len() as u64;
                let recv_ms = now_ms();
                let v: Value = match serde_json::from_str(&txt) { Ok(v) => v, Err(_) => continue };
                for ev in parse_hl(&v) {
                    lat_sum_ms += recv_ms as i128 - ev.ts_ms() as i128;
                    lat_n += 1;
                    writer.write(&ev)?;
                    events += 1;
                }
            }
        }
    }
}

/// Map a Hyperliquid WS message to zero or more normalized events.
fn parse_hl(v: &Value) -> Vec<Event> {
    let channel = v.get("channel").and_then(Value::as_str).unwrap_or("");
    let data = match v.get("data") {
        Some(d) => d,
        None => return Vec::new(),
    };
    match channel {
        "l2Book" => parse_book(data).into_iter().collect(),
        "trades" => parse_trades(data),
        _ => Vec::new(),
    }
}

fn px_sz(level: &Value) -> Option<Level> {
    let px = level.get("px")?.as_str()?.parse().ok()?;
    let sz = level.get("sz")?.as_str()?.parse().ok()?;
    Some(Level { px, sz })
}

fn parse_book(data: &Value) -> Option<Event> {
    let symbol = data.get("coin")?.as_str()?.to_string();
    let ts_ms = data.get("time").and_then(Value::as_u64).unwrap_or_else(now_ms);
    let levels = data.get("levels")?.as_array()?;
    let bids = levels.first()?.as_array()?.iter().filter_map(px_sz).collect();
    let asks = levels.get(1)?.as_array()?.iter().filter_map(px_sz).collect();
    Some(Event::Book { ts_ms, venue: VENUE.into(), symbol, bids, asks })
}

fn parse_trades(data: &Value) -> Vec<Event> {
    let Some(arr) = data.as_array() else { return Vec::new() };
    arr.iter()
        .filter_map(|t| {
            let symbol = t.get("coin")?.as_str()?.to_string();
            let ts_ms = t.get("time").and_then(Value::as_u64).unwrap_or_else(now_ms);
            let side = match t.get("side").and_then(Value::as_str) {
                Some("B") => Side::Buy,
                _ => Side::Sell,
            };
            let px = t.get("px")?.as_str()?.parse().ok()?;
            let sz = t.get("sz")?.as_str()?.parse().ok()?;
            Some(Event::Trade { ts_ms, venue: VENUE.into(), symbol, side, px, sz })
        })
        .collect()
}
