//! Microbenchmarks for the hot paths: applying a delta and reading top-of-book,
//! on a realistic 1000-level book. The start of the measure-then-optimize loop —
//! BTreeMap first, then we'll know what (if anything) to hand-tune.

use book::{OrderBook, Side};
use criterion::{black_box, criterion_group, criterion_main, Criterion};

fn bench(c: &mut Criterion) {
    let bids: Vec<(i64, f64)> = (1..=500).map(|i| (i, 1.0)).collect();
    let asks: Vec<(i64, f64)> = (501..=1000).map(|i| (i, 1.0)).collect();

    c.bench_function("apply_delta modify-top (1000 levels)", |b| {
        let mut ob = OrderBook::new();
        ob.apply_snapshot(0, &bids, &asks);
        let mut seq = 0u64;
        b.iter(|| {
            seq += 1;
            ob.apply_delta(black_box(seq), Side::Bid, black_box(500), black_box(2.0))
                .unwrap();
        });
    });

    c.bench_function("top-of-book reads (mid+micro+best)", |b| {
        let mut ob = OrderBook::new();
        ob.apply_snapshot(0, &bids, &asks);
        b.iter(|| {
            black_box(ob.mid());
            black_box(ob.microprice());
            black_box(ob.best_bid());
            black_box(ob.best_ask());
        });
    });
}

criterion_group!(benches, bench);
criterion_main!(benches);
