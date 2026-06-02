//! Property tests: book invariants must hold under arbitrary delta streams.
//!
//! Bids are generated in [1,100], asks in [200,300] — disjoint price ranges, so
//! a consistent book can never cross by construction. We then fire random
//! size-updates/removals and assert the structural invariants after every one.

use book::{OrderBook, Side};
use proptest::prelude::*;

#[derive(Debug, Clone)]
struct Op {
    side: Side,
    px: i64,
    qty: f64, // 0 => remove
}

fn op_strategy() -> impl Strategy<Value = Op> {
    (any::<bool>(), 0u64..100, 0u32..50).prop_map(|(is_bid, off, q)| {
        if is_bid {
            Op { side: Side::Bid, px: 1 + off as i64, qty: q as f64 }
        } else {
            Op { side: Side::Ask, px: 200 + off as i64, qty: q as f64 }
        }
    })
}

proptest! {
    #[test]
    fn invariants_hold(ops in proptest::collection::vec(op_strategy(), 0..300)) {
        let mut ob = OrderBook::new();
        ob.apply_snapshot(0, &[(50, 1.0)], &[(250, 1.0)]);
        let mut seq = 0u64;

        for op in ops {
            seq += 1;
            ob.apply_delta(seq, op.side, op.px, op.qty).unwrap();

            prop_assert!(!ob.is_crossed());

            if let (Some((b, _)), Some((a, _))) = (ob.best_bid(), ob.best_ask()) {
                prop_assert!(b < a, "best bid {b} must be below best ask {a}");
                let mid = ob.mid().unwrap();
                prop_assert!(mid >= b as f64 && mid <= a as f64);
            }

            let tb = ob.top_n(Side::Bid, 5);
            prop_assert!(tb.windows(2).all(|w| w[0].0 > w[1].0)); // strictly descending
            prop_assert!(tb.iter().all(|&(_, q)| q > 0.0));       // no zero/neg sizes

            let ta = ob.top_n(Side::Ask, 5);
            prop_assert!(ta.windows(2).all(|w| w[0].0 < w[1].0)); // strictly ascending
            prop_assert!(ta.iter().all(|&(_, q)| q > 0.0));
        }
    }
}
