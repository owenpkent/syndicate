//! **Sim** — the player's paper-trading ledger. Tracks cash, a signed position,
//! realized/unrealized PnL, fees, equity, and max drawdown; fills market orders
//! (taker) against a supplied best bid/ask and marks-to-market each tick.
//!
//! Equity is the source of truth: `equity = cash + position * mark`. Every fill
//! moves `cash` by `-signed_qty * px - fee`, so total PnL is just
//! `equity − starting_cash`. `realized`/`unrealized` are reporting splits derived
//! from a running average entry price. Pure — it takes prices and depends on
//! nothing, so the [`replay`](../replay) engine and the TUI both drive it.

/// A point-in-time account report for the UI.
#[derive(Debug, Clone, PartialEq)]
pub struct Report {
    pub cash: f64,
    pub position: f64,
    pub avg_price: f64,
    pub mark_px: f64,
    pub equity: f64,
    pub unrealized: f64,
    pub realized: f64,
    pub fees: f64,
    pub total_pnl: f64,
    pub return_pct: f64,
    pub max_drawdown_pct: f64,
    pub fills: u64,
}

/// A paper-trading account.
#[derive(Debug, Clone)]
pub struct Sim {
    start_cash: f64,
    cash: f64,
    position: f64,
    avg_price: f64,
    realized: f64,
    fees: f64,
    fee_rate: f64, // taker fee as a fraction (10 bps -> 0.001)
    fills: u64,
    peak_equity: f64,
    max_drawdown: f64, // most-negative equity/peak − 1 seen (≤ 0)
}

impl Sim {
    /// `fee_bps` is the taker fee in basis points (e.g. `5.0` = 0.05%).
    pub fn new(start_cash: f64, fee_bps: f64) -> Self {
        Self {
            start_cash,
            cash: start_cash,
            position: 0.0,
            avg_price: 0.0,
            realized: 0.0,
            fees: 0.0,
            fee_rate: fee_bps / 10_000.0,
            fills: 0,
            peak_equity: start_cash,
            max_drawdown: 0.0,
        }
    }

    /// Market buy `qty` at the current best ask (taker). Returns the fill price,
    /// or `None` if there's no offer to lift.
    pub fn market_buy(&mut self, qty: f64, best_ask: Option<f64>) -> Option<f64> {
        let px = best_ask?;
        if qty > 0.0 {
            self.fill(qty, px);
        }
        Some(px)
    }

    /// Market sell `qty` at the current best bid (taker). Returns the fill price,
    /// or `None` if there's no bid to hit.
    pub fn market_sell(&mut self, qty: f64, best_bid: Option<f64>) -> Option<f64> {
        let px = best_bid?;
        if qty > 0.0 {
            self.fill(-qty, px);
        }
        Some(px)
    }

    /// Flatten the position at the given best bid/ask (whichever side closes it).
    pub fn flatten(&mut self, best_bid: Option<f64>, best_ask: Option<f64>) {
        if self.position > 0.0 {
            let q = self.position;
            self.market_sell(q, best_bid);
        } else if self.position < 0.0 {
            let q = -self.position;
            self.market_buy(q, best_ask);
        }
    }

    fn fill(&mut self, signed_qty: f64, px: f64) {
        let fee = px * signed_qty.abs() * self.fee_rate;
        self.fees += fee;
        self.cash -= signed_qty * px + fee;
        self.fills += 1;

        let pos = self.position;
        let new_pos = pos + signed_qty;
        if pos == 0.0 || pos.signum() == signed_qty.signum() {
            // open from flat or extend in the same direction → blend avg entry
            let total = pos.abs() + signed_qty.abs();
            if total > 0.0 {
                self.avg_price = (pos.abs() * self.avg_price + signed_qty.abs() * px) / total;
            }
        } else {
            // opposite direction → realize PnL on the closed amount
            let closing = signed_qty.abs().min(pos.abs());
            self.realized += pos.signum() * (px - self.avg_price) * closing;
            if new_pos == 0.0 {
                self.avg_price = 0.0;
            } else if new_pos.signum() != pos.signum() {
                self.avg_price = px; // flipped past zero → new position at fill price
            }
            // else: reduced but same side → avg entry unchanged
        }
        self.position = new_pos;
    }

    /// Update peak equity + drawdown using the current mark price. Call each tick.
    pub fn mark(&mut self, mark_px: f64) {
        let eq = self.equity(mark_px);
        if eq > self.peak_equity {
            self.peak_equity = eq;
        }
        if self.peak_equity > 0.0 {
            let dd = eq / self.peak_equity - 1.0;
            if dd < self.max_drawdown {
                self.max_drawdown = dd;
            }
        }
    }

    pub fn equity(&self, mark_px: f64) -> f64 {
        self.cash + self.position * mark_px
    }
    pub fn unrealized(&self, mark_px: f64) -> f64 {
        self.position * (mark_px - self.avg_price)
    }
    pub fn total_pnl(&self, mark_px: f64) -> f64 {
        self.equity(mark_px) - self.start_cash
    }

    pub fn position(&self) -> f64 {
        self.position
    }
    pub fn cash(&self) -> f64 {
        self.cash
    }
    pub fn fills(&self) -> u64 {
        self.fills
    }

    pub fn snapshot(&self, mark_px: f64) -> Report {
        let equity = self.equity(mark_px);
        Report {
            cash: self.cash,
            position: self.position,
            avg_price: self.avg_price,
            mark_px,
            equity,
            unrealized: self.unrealized(mark_px),
            realized: self.realized,
            fees: self.fees,
            total_pnl: equity - self.start_cash,
            return_pct: (equity / self.start_cash - 1.0) * 100.0,
            max_drawdown_pct: self.max_drawdown * 100.0,
            fills: self.fills,
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn approx(a: f64, b: f64) {
        assert!((a - b).abs() < 1e-6, "{a} != {b}");
    }

    #[test]
    fn long_round_trip_profit() {
        let mut s = Sim::new(10_000.0, 0.0);
        s.market_buy(1.0, Some(100.0));
        approx(s.cash(), 9_900.0);
        approx(s.position(), 1.0);
        // mark up
        approx(s.unrealized(110.0), 10.0);
        approx(s.equity(110.0), 10_010.0);
        // close at 110 -> realized +10, flat
        s.market_sell(1.0, Some(110.0));
        approx(s.position(), 0.0);
        approx(s.snapshot(110.0).realized, 10.0);
        approx(s.total_pnl(999.0), 10.0); // flat: PnL independent of mark
    }

    #[test]
    fn short_round_trip_profit() {
        let mut s = Sim::new(10_000.0, 0.0);
        s.market_sell(2.0, Some(100.0)); // short 2 @ 100
        approx(s.position(), -2.0);
        approx(s.cash(), 10_200.0);
        s.market_buy(2.0, Some(90.0)); // cover @ 90 -> +20
        approx(s.position(), 0.0);
        approx(s.snapshot(90.0).realized, 20.0);
        approx(s.total_pnl(0.0), 20.0);
    }

    #[test]
    fn flip_realizes_then_reopens() {
        let mut s = Sim::new(10_000.0, 0.0);
        s.market_buy(1.0, Some(100.0)); // long 1 @ 100
        s.market_sell(3.0, Some(90.0)); // close 1 (realize -10), open short 2 @ 90
        approx(s.position(), -2.0);
        approx(s.snapshot(90.0).realized, -10.0);
        approx(s.snapshot(90.0).avg_price, 90.0);
        approx(s.unrealized(90.0), 0.0);
    }

    #[test]
    fn fees_reduce_pnl() {
        let mut s = Sim::new(10_000.0, 10.0); // 10 bps
        s.market_buy(1.0, Some(100.0)); // fee 0.1
        s.market_sell(1.0, Some(100.0)); // fee 0.1
        approx(s.snapshot(100.0).fees, 0.2);
        approx(s.total_pnl(100.0), -0.2); // flat, only fees lost
    }

    #[test]
    fn drawdown_tracks_worst_dip() {
        let mut s = Sim::new(1_000.0, 0.0);
        s.market_buy(1.0, Some(100.0)); // equity at mark 100 = 1000
        s.mark(100.0);
        s.mark(110.0); // peak equity 1010
        s.mark(95.0); // equity 995 -> dd = 995/1010 - 1 ≈ -1.485%
        let dd = s.snapshot(95.0).max_drawdown_pct;
        assert!(dd < -1.4 && dd > -1.6, "dd {dd}");
        s.mark(120.0); // new peak; drawdown stays at the worst seen
        approx(s.snapshot(120.0).max_drawdown_pct, dd);
    }

    #[test]
    fn no_liquidity_no_fill() {
        let mut s = Sim::new(1_000.0, 0.0);
        assert_eq!(s.market_buy(1.0, None), None);
        approx(s.position(), 0.0);
        approx(s.cash(), 1_000.0);
    }
}
