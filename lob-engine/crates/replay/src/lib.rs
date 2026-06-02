//! **Replay** — the trading-sim's game clock. Streams a recorded [`tape`] forward
//! in tape-time order, applying each book snapshot to a [`book::OrderBook`] and
//! tracking the last trade, then exposes the evolving market in *real price units*
//! for the sim and TUI to render.
//!
//! Hyperliquid tapes carry full `l2Book` snapshots (not sequenced deltas), so each
//! `Book` event replaces the book via `apply_snapshot`. Prices are converted to the
//! book's fixed-point ticks on the way in (`px * tick_scale`, rounded) and back to
//! real units on the way out — so the book stays integer/exact while the UI shows
//! dollars.
//!
//! [`Replay::step`] does no sleeping; pacing is the driver's job — call [`pace`]
//! with consecutive event timestamps and a speed multiplier to get a sleep
//! duration (so a TUI can play a 10-minute session in 10 seconds at `speed = 60`).

use std::io::Read;
use std::time::Duration;

use book::{OrderBook, Qty, Side as BookSide, Ticks};
use tape::{Event, Reader, Side as TapeSide};

/// Default ticks per price unit (cents). Fine for BTC/ETH/SOL; raise for
/// sub-dollar symbols that need more resolution.
pub const DEFAULT_TICK_SCALE: f64 = 100.0;

#[inline]
fn to_ticks(px: f64, scale: f64) -> Ticks {
    (px * scale).round() as Ticks
}

#[inline]
fn to_px(ticks: Ticks, scale: f64) -> f64 {
    ticks as f64 / scale
}

/// What one [`Replay::step`] advanced past.
#[derive(Debug, Clone, PartialEq)]
pub enum Tick {
    /// A book snapshot was applied at `ts_ms`.
    Book { ts_ms: u64 },
    /// A trade printed: `px`/`sz` in real units, `buy` = taker lifted the offer.
    Trade { ts_ms: u64, px: f64, sz: f64, buy: bool },
}

impl Tick {
    pub fn ts_ms(&self) -> u64 {
        match self {
            Tick::Book { ts_ms } | Tick::Trade { ts_ms, .. } => *ts_ms,
        }
    }
}

/// A snapshot of the market in real price units — what the sim/TUI render.
#[derive(Debug, Clone, Default, PartialEq)]
pub struct MarketState {
    pub ts_ms: u64,
    pub symbol: String,
    pub best_bid: Option<(f64, f64)>,
    pub best_ask: Option<(f64, f64)>,
    pub mid: Option<f64>,
    pub microprice: Option<f64>,
    pub last_trade_px: Option<f64>,
}

/// Streams a tape into a live order book.
pub struct Replay<R: Read> {
    reader: Reader<R>,
    book: OrderBook,
    scale: f64,
    /// Symbol to play. `None` auto-locks onto the first symbol seen (so a
    /// multi-symbol tape still yields one coherent book).
    symbol: Option<String>,
    last_ts: Option<u64>,
    last_trade_px: Option<f64>,
    seq: u64,
    applied: u64,
}

impl<R: Read> Replay<R> {
    pub fn new(reader: R, tick_scale: f64, symbol: Option<String>) -> Self {
        Self {
            reader: Reader::new(reader),
            book: OrderBook::new(),
            scale: if tick_scale > 0.0 { tick_scale } else { DEFAULT_TICK_SCALE },
            symbol,
            last_ts: None,
            last_trade_px: None,
            seq: 0,
            applied: 0,
        }
    }

    fn accept(&mut self, sym: &str) -> bool {
        match &self.symbol {
            Some(s) => s == sym,
            None => {
                self.symbol = Some(sym.to_string());
                true
            }
        }
    }

    /// Advance to the next event for the played symbol. `Ok(None)` at end of tape.
    pub fn step(&mut self) -> std::io::Result<Option<Tick>> {
        loop {
            let Some(ev) = self.reader.read()? else { return Ok(None) };
            match ev {
                Event::Book { ts_ms, symbol, bids, asks, .. } => {
                    if !self.accept(&symbol) {
                        continue;
                    }
                    self.seq += 1;
                    let b: Vec<(Ticks, Qty)> =
                        bids.iter().map(|l| (to_ticks(l.px, self.scale), l.sz)).collect();
                    let a: Vec<(Ticks, Qty)> =
                        asks.iter().map(|l| (to_ticks(l.px, self.scale), l.sz)).collect();
                    self.book.apply_snapshot(self.seq, &b, &a);
                    self.last_ts = Some(ts_ms);
                    self.applied += 1;
                    return Ok(Some(Tick::Book { ts_ms }));
                }
                Event::Trade { ts_ms, symbol, side, px, sz, .. } => {
                    if !self.accept(&symbol) {
                        continue;
                    }
                    self.last_trade_px = Some(px);
                    self.last_ts = Some(ts_ms);
                    self.applied += 1;
                    return Ok(Some(Tick::Trade {
                        ts_ms,
                        px,
                        sz,
                        buy: matches!(side, TapeSide::Buy),
                    }));
                }
            }
        }
    }

    /// Current market in real price units.
    pub fn market(&self) -> MarketState {
        MarketState {
            ts_ms: self.last_ts.unwrap_or(0),
            symbol: self.symbol.clone().unwrap_or_default(),
            best_bid: self.book.best_bid().map(|(t, q)| (to_px(t, self.scale), q)),
            best_ask: self.book.best_ask().map(|(t, q)| (to_px(t, self.scale), q)),
            mid: self.book.mid().map(|m| m / self.scale),
            microprice: self.book.microprice().map(|m| m / self.scale),
            last_trade_px: self.last_trade_px,
        }
    }

    /// Real-unit best bid/ask price (taker reference for the sim's fills).
    pub fn best_bid_px(&self) -> Option<f64> {
        self.book.best_bid().map(|(t, _)| to_px(t, self.scale))
    }
    pub fn best_ask_px(&self) -> Option<f64> {
        self.book.best_ask().map(|(t, _)| to_px(t, self.scale))
    }

    /// Top `n` levels of a side as (price, size) in real units, best first.
    pub fn ladder(&self, side: BookSide, n: usize) -> Vec<(f64, f64)> {
        self.book.top_n(side, n).into_iter().map(|(t, q)| (to_px(t, self.scale), q)).collect()
    }

    pub fn book(&self) -> &OrderBook {
        &self.book
    }
    pub fn symbol(&self) -> Option<&str> {
        self.symbol.as_deref()
    }
    pub fn applied(&self) -> u64 {
        self.applied
    }
    pub fn tick_scale(&self) -> f64 {
        self.scale
    }
}

/// Real-time sleep before the next event, given consecutive tape timestamps and a
/// speed multiplier (`speed = 60` → one real second per tape minute). Capped at 2s
/// so a long quiet gap doesn't stall the game.
pub fn pace(prev_ms: u64, cur_ms: u64, speed: f64) -> Duration {
    if speed <= 0.0 || cur_ms <= prev_ms {
        return Duration::ZERO;
    }
    let real_ms = (cur_ms - prev_ms) as f64 / speed;
    Duration::from_secs_f64((real_ms / 1000.0).min(2.0))
}

#[cfg(test)]
mod tests {
    use super::*;
    use book::Side;
    use tape::{Event, Level, Side as TSide, Writer};

    /// Build an in-memory tape of the given events.
    fn tape_bytes(events: &[Event]) -> Vec<u8> {
        let mut buf = Vec::new();
        let mut w = Writer::new(&mut buf);
        for e in events {
            w.write(e).unwrap();
        }
        w.flush().unwrap();
        buf
    }

    fn book_ev(ts: u64, bids: &[(f64, f64)], asks: &[(f64, f64)]) -> Event {
        Event::Book {
            ts_ms: ts,
            venue: "test".into(),
            symbol: "BTC".into(),
            bids: bids.iter().map(|&(px, sz)| Level { px, sz }).collect(),
            asks: asks.iter().map(|&(px, sz)| Level { px, sz }).collect(),
        }
    }

    #[test]
    fn replays_book_and_trade_in_real_units() {
        let bytes = tape_bytes(&[
            book_ev(1000, &[(100.00, 5.0), (99.99, 2.0)], &[(100.02, 3.0), (100.03, 1.0)]),
            Event::Trade {
                ts_ms: 1001,
                venue: "test".into(),
                symbol: "BTC".into(),
                side: TSide::Buy,
                px: 100.02,
                sz: 0.5,
            },
            book_ev(1002, &[(100.01, 4.0)], &[(100.04, 2.0)]),
        ]);
        let mut r = Replay::new(bytes.as_slice(), 100.0, None);

        assert_eq!(r.step().unwrap(), Some(Tick::Book { ts_ms: 1000 }));
        let m = r.market();
        assert_eq!(m.symbol, "BTC"); // auto-locked
        assert_eq!(m.best_bid, Some((100.00, 5.0)));
        assert_eq!(m.best_ask, Some((100.02, 3.0)));
        assert_eq!(m.mid, Some(100.01));
        // microprice leans to the heavier side (bid 5 > ask 3 -> above mid)
        assert!(m.microprice.unwrap() > 100.01);
        assert_eq!(r.ladder(Side::Bid, 2), vec![(100.00, 5.0), (99.99, 2.0)]);

        assert_eq!(r.step().unwrap(), Some(Tick::Trade { ts_ms: 1001, px: 100.02, sz: 0.5, buy: true }));
        assert_eq!(r.market().last_trade_px, Some(100.02));

        assert_eq!(r.step().unwrap(), Some(Tick::Book { ts_ms: 1002 }));
        assert_eq!(r.best_bid_px(), Some(100.01)); // snapshot replaced the book
        assert_eq!(r.best_ask_px(), Some(100.04));

        assert_eq!(r.step().unwrap(), None); // end of tape
        assert_eq!(r.applied(), 3);
    }

    #[test]
    fn filters_to_chosen_symbol() {
        let mut eth = book_ev(1, &[(2000.0, 1.0)], &[(2001.0, 1.0)]);
        if let Event::Book { symbol, .. } = &mut eth {
            *symbol = "ETH".into();
        }
        let btc = book_ev(2, &[(100.0, 1.0)], &[(101.0, 1.0)]);
        let bytes = tape_bytes(&[eth, btc]);

        let mut r = Replay::new(bytes.as_slice(), 100.0, Some("BTC".into()));
        // ETH event skipped; first returned tick is the BTC book
        assert_eq!(r.step().unwrap(), Some(Tick::Book { ts_ms: 2 }));
        assert_eq!(r.market().best_bid, Some((100.0, 1.0)));
        assert_eq!(r.step().unwrap(), None);
    }

    #[test]
    fn pace_scales_and_caps() {
        assert_eq!(pace(1000, 1000, 1.0), Duration::ZERO); // no gap
        assert_eq!(pace(1000, 2000, 1.0), Duration::from_secs(1)); // 1000ms / 1x
        assert_eq!(pace(0, 6000, 60.0), Duration::from_millis(100)); // 6000/60 = 100ms
        assert_eq!(pace(0, 100_000_000, 1.0), Duration::from_secs(2)); // capped
        assert_eq!(pace(1000, 2000, 0.0), Duration::ZERO); // speed 0
    }
}
