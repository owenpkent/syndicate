//! Watch a recorded tape play out as a live market — a preview of the game loop.
//!
//! ```text
//! cargo run -p replay --example play -- /tmp/probe.tape               # as fast as possible
//! cargo run -p replay --example play -- /tmp/probe.tape --symbol BTC --speed 60
//! ```

use std::fs::File;
use std::io::BufReader;

use replay::{pace, Replay, Tick, DEFAULT_TICK_SCALE};

fn main() {
    let mut args = std::env::args().skip(1);
    let path = match args.next() {
        Some(p) => p,
        None => {
            eprintln!("usage: play <tape> [--symbol S] [--speed N]");
            std::process::exit(2);
        }
    };
    let mut symbol = None;
    let mut speed = 0.0_f64; // 0 = no pacing, replay as fast as possible
    while let Some(a) = args.next() {
        match a.as_str() {
            "--symbol" => symbol = args.next(),
            "--speed" => speed = args.next().and_then(|s| s.parse().ok()).unwrap_or(0.0),
            other => eprintln!("warn: ignoring {other}"),
        }
    }

    let file = File::open(&path).unwrap_or_else(|e| {
        eprintln!("open {path}: {e}");
        std::process::exit(1);
    });
    let mut r = Replay::new(BufReader::new(file), DEFAULT_TICK_SCALE, symbol);

    let (mut books, mut trades) = (0u64, 0u64);
    let mut prev_ts: Option<u64> = None;
    loop {
        let tick = match r.step() {
            Ok(Some(t)) => t,
            Ok(None) => break,
            Err(e) => {
                eprintln!("replay error: {e}");
                break;
            }
        };
        if speed > 0.0 {
            if let Some(p) = prev_ts {
                std::thread::sleep(pace(p, tick.ts_ms(), speed));
            }
            prev_ts = Some(tick.ts_ms());
        }
        match tick {
            Tick::Book { .. } => {
                books += 1;
                // throttle output when running flat-out; show every update when paced
                if speed > 0.0 || books % 10 == 0 {
                    let m = r.market();
                    if let (Some((bb, _)), Some((ba, _))) = (m.best_bid, m.best_ask) {
                        println!(
                            "{} {:<5} bid {:>10.2}  ask {:>10.2}  mid {:>10.2}  spread {:.2}",
                            m.ts_ms, m.symbol, bb, ba, m.mid.unwrap_or(0.0), ba - bb
                        );
                    }
                }
            }
            Tick::Trade { px, sz, buy, .. } => {
                trades += 1;
                if speed > 0.0 {
                    println!("    {} {:.4} @ {:.2}", if buy { "BUY " } else { "SELL" }, sz, px);
                }
            }
        }
    }
    println!(
        "\n— replayed {books} book updates, {trades} trades (symbol {:?}) —",
        r.symbol()
    );
}
