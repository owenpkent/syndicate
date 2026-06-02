//! L2 **order-book reconstruction**: maintain a full limit-order book from a
//! snapshot plus a stream of sequenced deltas, and detect sequence gaps (the
//! signal that the book is stale and must be re-synced from a fresh snapshot).
//!
//! This is the canonical market-data exercise. The book is **venue-agnostic**:
//! a per-venue adapter converts that exchange's wire format into fixed-point
//! `Ticks` prices and a monotonically increasing sequence number, then drives
//! [`OrderBook`]. Prices are integers (`i64` ticks) so the book never compares
//! floats — exact, and what real engines do.
//!
//! Side semantics here are **resting levels** (`Bid`/`Ask`), distinct from
//! `tape::Side` (`Buy`/`Sell`, the aggressor of a trade).

use std::collections::BTreeMap;

/// Fixed-point price in a venue's tick units (adapter divides raw px by tick size).
pub type Ticks = i64;
/// Resting size at a level. `f64` for v1; a fixed-point upgrade is plausible later.
pub type Qty = f64;

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Side {
    Bid,
    Ask,
}

/// A sequence discontinuity: the book is now stale and the caller must resync
/// from a fresh snapshot. `expected` is the seq we needed; `got` is what arrived.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct Gap {
    pub expected: u64,
    pub got: u64,
}

/// A reconstructed L2 book. Each side is a `BTreeMap<Ticks, Qty>` keyed by price
/// ascending, so best bid = max key (`last`), best ask = min key (`first`).
#[derive(Debug, Default, Clone)]
pub struct OrderBook {
    bids: BTreeMap<Ticks, Qty>,
    asks: BTreeMap<Ticks, Qty>,
    seq: Option<u64>,
}

impl OrderBook {
    pub fn new() -> Self {
        Self::default()
    }

    /// Last applied sequence number, or `None` before the first snapshot.
    pub fn seq(&self) -> Option<u64> {
        self.seq
    }

    /// (bid levels, ask levels).
    pub fn depth(&self) -> (usize, usize) {
        (self.bids.len(), self.asks.len())
    }

    pub fn is_empty(&self) -> bool {
        self.bids.is_empty() && self.asks.is_empty()
    }

    /// Replace the entire book from a snapshot taken at `seq`. Zero/negative-qty
    /// levels are dropped.
    pub fn apply_snapshot(&mut self, seq: u64, bids: &[(Ticks, Qty)], asks: &[(Ticks, Qty)]) {
        self.bids.clear();
        self.asks.clear();
        for &(px, q) in bids {
            if q > 0.0 {
                self.bids.insert(px, q);
            }
        }
        for &(px, q) in asks {
            if q > 0.0 {
                self.asks.insert(px, q);
            }
        }
        self.seq = Some(seq);
    }

    /// Apply one delta at sequence `seq` (must equal `prev_seq + 1`). A `qty` of
    /// `0` removes the level; otherwise it sets the resting size. Returns
    /// `Err(Gap)` on a sequence discontinuity, leaving the (now stale) book
    /// unchanged so the caller can resync.
    pub fn apply_delta(&mut self, seq: u64, side: Side, px: Ticks, qty: Qty) -> Result<(), Gap> {
        if let Some(prev) = self.seq {
            if seq != prev + 1 {
                return Err(Gap { expected: prev + 1, got: seq });
            }
        }
        let levels = match side {
            Side::Bid => &mut self.bids,
            Side::Ask => &mut self.asks,
        };
        if qty > 0.0 {
            levels.insert(px, qty);
        } else {
            levels.remove(&px);
        }
        self.seq = Some(seq);
        Ok(())
    }

    pub fn best_bid(&self) -> Option<(Ticks, Qty)> {
        self.bids.last_key_value().map(|(&p, &q)| (p, q))
    }

    pub fn best_ask(&self) -> Option<(Ticks, Qty)> {
        self.asks.first_key_value().map(|(&p, &q)| (p, q))
    }

    /// Ask − bid in ticks (negative iff crossed).
    pub fn spread(&self) -> Option<Ticks> {
        Some(self.best_ask()?.0 - self.best_bid()?.0)
    }

    /// Arithmetic mid of best bid/ask, in ticks.
    pub fn mid(&self) -> Option<f64> {
        let (b, _) = self.best_bid()?;
        let (a, _) = self.best_ask()?;
        Some((b as f64 + a as f64) / 2.0)
    }

    /// Size-weighted **microprice**: `(P_bid·Q_ask + P_ask·Q_bid)/(Q_bid+Q_ask)`.
    /// Leans toward the side with more opposing depth — a better short-horizon
    /// fair-value estimate than the plain mid.
    pub fn microprice(&self) -> Option<f64> {
        let (bp, bq) = self.best_bid()?;
        let (ap, aq) = self.best_ask()?;
        let w = bq + aq;
        if w <= 0.0 {
            return None;
        }
        Some((bp as f64 * aq + ap as f64 * bq) / w)
    }

    /// True if best bid ≥ best ask — should never persist in a consistent book.
    pub fn is_crossed(&self) -> bool {
        matches!((self.best_bid(), self.best_ask()), (Some((b, _)), Some((a, _))) if b >= a)
    }

    /// Top `n` levels of `side`, best first (bids descending, asks ascending).
    pub fn top_n(&self, side: Side, n: usize) -> Vec<(Ticks, Qty)> {
        match side {
            Side::Bid => self.bids.iter().rev().take(n).map(|(&p, &q)| (p, q)).collect(),
            Side::Ask => self.asks.iter().take(n).map(|(&p, &q)| (p, q)).collect(),
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn seeded() -> OrderBook {
        let mut ob = OrderBook::new();
        ob.apply_snapshot(
            10,
            &[(100, 5.0), (99, 3.0), (98, 1.0)],
            &[(101, 2.0), (102, 4.0), (103, 6.0)],
        );
        ob
    }

    #[test]
    fn snapshot_reads() {
        let ob = seeded();
        assert_eq!(ob.best_bid(), Some((100, 5.0)));
        assert_eq!(ob.best_ask(), Some((101, 2.0)));
        assert_eq!(ob.spread(), Some(1));
        assert_eq!(ob.mid(), Some(100.5));
        assert!(!ob.is_crossed());
        assert_eq!(ob.top_n(Side::Bid, 2), vec![(100, 5.0), (99, 3.0)]);
        assert_eq!(ob.top_n(Side::Ask, 2), vec![(101, 2.0), (102, 4.0)]);
    }

    #[test]
    fn microprice_leans_to_heavier_pressure() {
        // bid size 5 >> ask size 2 -> buy pressure -> micro above mid (100.5)
        let ob = seeded();
        let micro = ob.microprice().unwrap();
        assert!(micro > 100.5, "micro {micro} should exceed mid with bid pressure");
    }

    #[test]
    fn delta_insert_modify_remove() {
        let mut ob = seeded();
        ob.apply_delta(11, Side::Bid, 100, 9.0).unwrap(); // modify top
        assert_eq!(ob.best_bid(), Some((100, 9.0)));
        ob.apply_delta(12, Side::Bid, 100, 0.0).unwrap(); // remove top -> 99 promotes
        assert_eq!(ob.best_bid(), Some((99, 3.0)));
        ob.apply_delta(13, Side::Ask, 100, 1.5).unwrap(); // new ask inside
        assert_eq!(ob.best_ask(), Some((100, 1.5)));
        assert_eq!(ob.seq(), Some(13));
    }

    #[test]
    fn gap_detected_and_book_unchanged() {
        let mut ob = seeded();
        let before = ob.best_bid();
        let err = ob.apply_delta(13, Side::Bid, 97, 1.0).unwrap_err(); // expected 11
        assert_eq!(err, Gap { expected: 11, got: 13 });
        assert_eq!(ob.best_bid(), before); // stale, untouched
        assert_eq!(ob.seq(), Some(10));
    }

    #[test]
    fn snapshot_resyncs_after_gap() {
        let mut ob = seeded();
        assert!(ob.apply_delta(99, Side::Bid, 97, 1.0).is_err());
        ob.apply_snapshot(99, &[(50, 1.0)], &[(60, 1.0)]); // resync
        assert_eq!(ob.best_bid(), Some((50, 1.0)));
        ob.apply_delta(100, Side::Ask, 55, 2.0).unwrap(); // contiguous again
        assert_eq!(ob.best_ask(), Some((55, 2.0)));
    }
}
