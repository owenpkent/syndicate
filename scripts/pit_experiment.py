"""Does *point-in-time* enrichment beat the current-season constant? (measurement)

The ablation showed `net_rating`/`player_strength` add ~0 because they're
current-season aggregates smeared across all history. The hypothesis: a
**season-to-date, prior-games-only** version would carry real signal. This tests
it the cheap way before any pipeline integration — it computes a leakage-free
season-to-date net-efficiency (avg point differential) per team as the game
stream advances, appends it as an extra feature aligned to the standard
`walk_forward` rows, and reports the holdout delta.

If the delta is ~0, that's a confirmed finding: Elo (with MOV) already captures
team strength, so PIT net-efficiency is redundant — and we don't integrate it.
"""
from __future__ import annotations

import shutil
import sys
import tempfile
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
from sportsball.pipelines._elo import _coerce_date, walk_forward  # noqa: E402

import train_eval_duckdb as ted  # noqa: E402

SPLIT = 0.85


def _season(d) -> int:
    """NBA season key: games from Aug onward belong to that year's season."""
    return d.year if d.month >= 8 else d.year - 1


def pit_net_eff(rows_raw) -> np.ndarray:
    """Per-game `home_sd - away_sd` season-to-date avg margin (prior games only).

    Iterates in the same order as ``walk_forward`` so the output aligns row-for-row.
    """
    acc: dict[tuple, list] = {}   # (team, season) -> [games, margin_sum]
    out = []
    for i, (raw_date, home, away, hs, as_) in enumerate(rows_raw):
        season = _season(_coerce_date(raw_date, i))
        hk, ak = (home, season), (away, season)
        h = acc.get(hk, [0, 0.0])
        a = acc.get(ak, [0, 0.0])
        h_sd = h[1] / h[0] if h[0] else 0.0
        a_sd = a[1] / a[0] if a[0] else 0.0
        out.append(h_sd - a_sd)
        margin = hs - as_
        acc[hk] = [h[0] + 1, h[1] + margin]
        acc[ak] = [a[0] + 1, a[1] - margin]
    return np.array(out).reshape(-1, 1)


def _metrics(X, y, split=SPLIT):
    return ted.holdout_metrics(X, y, list(range(X.shape[1])), split)


def main() -> None:
    src = ted.DEFAULT_DB
    tmp = Path(tempfile.gettempdir()) / "pit_exp.duckdb"
    shutil.copy(src, tmp)
    rows_raw = ted.load_events(str(tmp))
    tmp.unlink(missing_ok=True)
    print(f"Loaded {len(rows_raw)} games.")

    # load_events returns 7-tuples (…, home_close, away_close); the walk + the
    # pit_net_eff baseline below both want 5-tuples.
    rows_raw = [(d, h, a, hs, as_) for (d, h, a, hs, as_, _hc, _ac) in rows_raw]
    frows, _ = walk_forward(rows_raw, ted.K, ted.HFA, mov_enabled=True,
                            carry=0.75, gap_days=90, form_window=10)
    base = np.array([r.features for r in frows])          # 7 std features (net/player are 0 here)
    y = np.array([1 if r.actual >= 1.0 else 0 for r in frows])
    pit = pit_net_eff(rows_raw)                            # +1 PIT feature

    # Compare: Elo+context (drop the always-0 net/player cols) vs + PIT net-eff.
    ctx_cols = [0, 2, 3, 4, 5]
    ctx = base[:, ctx_cols]
    ctx_pit = np.hstack([ctx, pit])

    m_ctx = _metrics(ctx, y)
    m_pit = _metrics(ctx_pit, y)
    print("\nHoldout (Elo + rest/b2b/form):")
    print(f"  brier={m_ctx['brier']:.4f}  log_loss={m_ctx['log_loss']:.4f}  acc={m_ctx['accuracy']:.4f}")
    print("+ point-in-time net-efficiency:")
    print(f"  brier={m_pit['brier']:.4f}  log_loss={m_pit['log_loss']:.4f}  acc={m_pit['accuracy']:.4f}")
    d = m_pit["log_loss"] - m_ctx["log_loss"]
    print(f"\nΔlog_loss = {d:+.4f}  ->  {'PIT net-eff helps' if d < -0.0005 else 'negligible (redundant with Elo)'}")


if __name__ == "__main__":
    main()
