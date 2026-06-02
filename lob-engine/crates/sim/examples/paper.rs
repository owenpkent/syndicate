//! Buy-and-hold a replayed tape, then print the paper-trading report — a preview
//! of `replay` + `sim` composing (the TUI will drive them interactively instead).
//!
//! ```text
//! cargo run -p sim --example paper -- /tmp/play.tape --cash 10000 --fee-bps 5
//! ```

use std::fs::File;
use std::io::BufReader;

use replay::{Replay, DEFAULT_TICK_SCALE};
use sim::Sim;

fn main() {
    let mut args = std::env::args().skip(1);
    let path = match args.next() {
        Some(p) => p,
        None => {
            eprintln!("usage: paper <tape> [--cash N] [--fee-bps N]");
            std::process::exit(2);
        }
    };
    let mut cash = 10_000.0_f64;
    let mut fee_bps = 5.0_f64;
    while let Some(a) = args.next() {
        match a.as_str() {
            "--cash" => cash = args.next().and_then(|s| s.parse().ok()).unwrap_or(cash),
            "--fee-bps" => fee_bps = args.next().and_then(|s| s.parse().ok()).unwrap_or(fee_bps),
            other => eprintln!("warn: ignoring {other}"),
        }
    }

    let file = File::open(&path).unwrap_or_else(|e| {
        eprintln!("open {path}: {e}");
        std::process::exit(1);
    });
    let mut r = Replay::new(BufReader::new(file), DEFAULT_TICK_SCALE, None);
    let mut s = Sim::new(cash, fee_bps);

    let mut bought = false;
    let mut last_mid: Option<f64> = None;
    while let Ok(Some(_)) = r.step() {
        let m = r.market();
        let Some(mid) = m.mid else { continue };
        last_mid = Some(mid);
        if !bought {
            if let Some(ask) = r.best_ask_px() {
                let qty = s.cash() / ask; // spend all cash
                s.market_buy(qty, Some(ask));
                bought = true;
                println!("bought {qty:.6} {} @ {ask:.2}", m.symbol);
            }
        }
        s.mark(mid);
    }

    let mark = last_mid.unwrap_or(0.0);
    let rep = s.snapshot(mark);
    println!("\n=== buy & hold report ({}) ===", r.symbol().unwrap_or("?"));
    println!("position {:.6} @ avg {:.2}   mark {:.2}", rep.position, rep.avg_price, mark);
    println!(
        "equity {:.2}   PnL {:+.2} ({:+.3}%)   fees {:.2}   maxDD {:.2}%   fills {}",
        rep.equity, rep.total_pnl, rep.return_pct, rep.fees, rep.max_drawdown_pct, rep.fills
    );
}
