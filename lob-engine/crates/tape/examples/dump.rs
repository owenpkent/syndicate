//! Inspect a tape file: counts events by kind and shows a few samples.
//!
//! ```text
//! cargo run -p tape --example dump -- /tmp/probe.tape
//! ```

use std::fs::File;
use std::io::BufReader;

use tape::{Event, Reader};

fn main() {
    let path = std::env::args().nth(1).expect("usage: dump <tape-file>");
    let file = File::open(&path).expect("open tape");
    let mut r = Reader::new(BufReader::new(file));

    let (mut trades, mut books) = (0u64, 0u64);
    let mut shown = 0;
    let (mut first_ts, mut last_ts) = (u64::MAX, 0u64);

    while let Some(ev) = r.read().expect("decode") {
        let ts = ev.ts_ms();
        first_ts = first_ts.min(ts);
        last_ts = last_ts.max(ts);
        match &ev {
            Event::Trade { .. } => trades += 1,
            Event::Book { bids, asks, symbol, .. } => {
                books += 1;
                if shown < 3 {
                    let bb = bids.first();
                    let ba = asks.first();
                    if let (Some(b), Some(a)) = (bb, ba) {
                        println!(
                            "{symbol}: best bid {:.2} x {:.4} | best ask {:.2} x {:.4} | spread {:.2}",
                            b.px, b.sz, a.px, a.sz, a.px - b.px
                        );
                    }
                    shown += 1;
                }
            }
        }
    }

    let span_s = last_ts.saturating_sub(first_ts) as f64 / 1000.0;
    println!(
        "\n{path}: {} events ({books} books, {trades} trades) over {span_s:.1}s",
        books + trades
    );
}
