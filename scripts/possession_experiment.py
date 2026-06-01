"""Does a possession-based net rating beat the margin-based one? (measurement)

The model's `net_rating_diff` is point-in-time *margin* (season-to-date avg point
differential). We now have possession data (team_advanced_game_logs: off/def
rating per team-game). Hypothesis: a PIT *possession* net rating (season-to-date
avg of off_rating - def_rating, prior games only) carries signal the margin
version / Elo doesn't.

This tests it cheaply BEFORE any feature-contract change: it builds both PIT
columns aligned to the standard walk_forward rows and reports the holdout delta
over Elo + rest/b2b/form. If possession ≈ margin ≈ 0, Elo already captures it and
we don't wire it in (avoids overfitting churn).
"""
from __future__ import annotations

import shutil
import sys
import tempfile
from pathlib import Path

import duckdb
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
from sportsball.matching import normalize_team  # noqa: E402
from sportsball.pipelines._elo import _coerce_date, walk_forward  # noqa: E402

import train_eval_duckdb as ted  # noqa: E402
from pit_experiment import pit_net_eff, _season  # noqa: E402  (reuse margin-net baseline)

SPLIT = 0.85


def possession_pit(db_path: str) -> dict:
    """{(norm_team, date_iso): season-to-date avg(off-def), prior games only}.

    Built by replaying team_advanced_game_logs in date order so each value uses
    only that team's earlier games this season (leakage-free), exactly like the
    margin walk.
    """
    con = duckdb.connect(db_path, read_only=True)
    rows = con.execute(
        """
        SELECT team_name, game_date, off_rating, def_rating
        FROM team_advanced_game_logs
        WHERE off_rating IS NOT NULL AND def_rating IS NOT NULL
        ORDER BY game_date ASC
        """
    ).fetchall()
    con.close()
    acc: dict[tuple, list] = {}            # (team, season) -> [games, net_sum]
    out: dict = {}
    for name, gd, off, dfn in rows:
        team = normalize_team(name)
        d = gd.date() if hasattr(gd, "date") else gd
        season = _season(d)
        key = (team, season)
        g = acc.get(key, [0, 0.0])
        std = g[1] / g[0] if g[0] else 0.0   # season-to-date BEFORE this game
        out[(team, d.isoformat())] = std
        acc[key] = [g[0] + 1, g[1] + (float(off) - float(dfn))]
    return out


def possession_col(rows_raw, pit: dict) -> np.ndarray:
    out = []
    for i, row in enumerate(rows_raw):
        raw_date, home, away = row[0], row[1], row[2]
        iso = _coerce_date(raw_date, i).isoformat()
        h = pit.get((normalize_team(home), iso), 0.0)
        a = pit.get((normalize_team(away), iso), 0.0)
        out.append(h - a)
    return np.array(out).reshape(-1, 1)


def _metrics(X, y, split=SPLIT):
    return ted.holdout_metrics(X, y, list(range(X.shape[1])), split)


def main() -> None:
    src = ted.DEFAULT_DB
    tmp = Path(tempfile.gettempdir()) / "poss_exp.duckdb"
    shutil.copy(src, tmp)
    rows_raw = ted.load_events(str(tmp))          # 7-tuples (…, home_close, away_close)
    pit = possession_pit(str(tmp))
    tmp.unlink(missing_ok=True)
    print(f"Loaded {len(rows_raw)} games; {len(pit)} possession-PIT keys.")

    # walk_forward wants 5-tuples; strip the closing-odds columns.
    results = [(d, h, a, hs, as_) for (d, h, a, hs, as_, _hc, _ac) in rows_raw]
    frows, _ = walk_forward(results, ted.K, ted.HFA, mov_enabled=True,
                            carry=0.75, gap_days=90, form_window=10)
    base = np.array([r.features for r in frows])
    y = np.array([1 if r.actual >= 1.0 else 0 for r in frows])

    margin = pit_net_eff(results)                 # PIT margin net (existing approach)
    poss = possession_col(results, pit)           # PIT possession net (new data)

    ctx_cols = [0, 2, 3, 4, 5]                    # Elo + rest/b2b/form (net/player are 0 here)
    ctx = base[:, ctx_cols]
    n_live = int(np.count_nonzero(poss[int(len(poss) * SPLIT):]))

    rows = [
        ("Elo + rest/b2b/form",        ctx),
        ("  + PIT margin net",         np.hstack([ctx, margin])),
        ("  + PIT possession net",     np.hstack([ctx, poss])),
        ("  + both",                   np.hstack([ctx, margin, poss])),
    ]
    print(f"\nChronological holdout (split={SPLIT}; {n_live} test games carry possession data):")
    print(f"{'feature set':<26}{'brier':>9}{'log_loss':>11}{'accuracy':>11}{'Δll':>10}")
    base_ll = None
    for name, X in rows:
        m = _metrics(X, y)
        if base_ll is None:
            base_ll = m["log_loss"]; delta = ""
        else:
            delta = f"{m['log_loss'] - base_ll:+.4f}"
        print(f"{name:<26}{m['brier']:>9.4f}{m['log_loss']:>11.4f}{m['accuracy']:>11.4f}{delta:>10}")
    print("\n(Δlog_loss vs 'Elo + rest/b2b/form'; negative = better. If possession ≈ margin ≈ 0, "
          "Elo already captures team strength and a possession feature isn't worth a contract change.)")


if __name__ == "__main__":
    main()
