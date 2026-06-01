"""Modeling the market: can open-time features predict the CLOSING total?

The honest game-modeling path is dead (no model beats the close). This tests the
market-modeling path instead: at OPEN, predict where the line will CLOSE. If our
features beat the opener at forecasting the close, we bet the opener when we expect
favorable movement -> positive CLV by construction (we don't need to beat the
close, only to anticipate it).

Target = close_total. Features available at open: the opening total itself, plus
point-in-time pace/efficiency (team_advanced_game_logs) and recent scoring form.
Baseline = opener alone (already a strong predictor of the close). The question:
does R^2(close | open + our features) beat R^2(close | open) out-of-sample?

Data: data/nba_archive_10Y.json (open+close totals, 2011-2022, already on disk).
"""
from __future__ import annotations

import json
import sys
from collections import defaultdict, deque
from pathlib import Path

import numpy as np
import duckdb
from numpy.linalg import lstsq

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
from sportsball.matching import normalize_team  # noqa: E402

ARCHIVE = Path(__file__).resolve().parent.parent / "data" / "nba_archive_10Y.json"
DUCKDB = Path(__file__).resolve().parent.parent / "data" / "sportsball.duckdb"


def _f(x):
    try:
        v = float(x); return None if v != v else v
    except (TypeError, ValueError):
        return None


def season(d):  # NBA season key
    return d.year if d.month >= 8 else d.year - 1


def build_pit(db):
    """(norm_team, date_iso) -> (off, def, pace) season-to-date, prior games only."""
    con = duckdb.connect(str(db), read_only=True)
    adv = con.execute("SELECT team_name, game_date, off_rating, def_rating, pace "
                      "FROM team_advanced_game_logs WHERE off_rating IS NOT NULL ORDER BY game_date").fetchall()
    con.close()
    acc = defaultdict(lambda: [0, 0.0, 0.0, 0.0]); pit = {}
    for name, d, o, df, p in adv:
        t = normalize_team(name); a = acc[(t, season(d))]
        if a[0] > 0:
            pit[(t, d.date().isoformat())] = (a[1] / a[0], a[2] / a[0], a[3] / a[0])
        a[0] += 1; a[1] += o; a[2] += df; a[3] += p
    return pit


def fit_pred(ftr, fte, ytr):
    A = np.column_stack([ftr, np.ones(len(ftr))]); coef, *_ = lstsq(A, ytr, rcond=None)
    return np.column_stack([fte, np.ones(len(fte))]) @ coef


def main():
    pit = build_pit(DUCKDB)
    raw = json.load(open(ARCHIVE))
    games = []
    for r in raw:
        ot, ct = _f(r.get("open_over_under")), _f(r.get("close_over_under"))
        hf, af = _f(r.get("home_final")), _f(r.get("away_final"))
        if None in (hf, af) or not ot or not ct or not (150 < ot < 280 and 150 < ct < 280):
            continue
        ds = str(int(r["date"])); iso = f"{ds[:4]}-{ds[4:6]}-{ds[6:8]}"
        games.append((iso, normalize_team(r["home_team"]), normalize_team(r["away_team"]),
                      ot, ct, hf + af))
    games.sort(key=lambda g: g[0])

    # recent scoring form: each team's rolling avg game-total over last 5 games (prior only)
    last = defaultdict(lambda: deque(maxlen=5))
    rows = []
    for iso, hn, an, ot, ct, tot in games:
        if (hn, iso) in pit and (an, iso) in pit and last[hn] and last[an]:
            ho, hd, hp = pit[(hn, iso)]; ao, ad, ap = pit[(an, iso)]
            feats = [ho, hd, hp, ao, ad, ap, np.mean(last[hn]), np.mean(last[an])]
            rows.append((ot, ct, tot, feats))
        last[hn].append(tot); last[an].append(tot)

    n = len(rows); cut = int(n * 0.8)
    ot = np.array([r[0] for r in rows]); ct = np.array([r[1] for r in rows])
    y = np.array([r[2] for r in rows]); X = np.array([r[3] for r in rows])
    print(f"{n} games with open+close total + PIT features (chronological 80/20)")

    # Can we predict the CLOSE better than the opener alone?
    base = np.abs(ct[cut:] - ot[cut:]).mean()                      # opener predicting close
    pred_full = fit_pred(np.column_stack([ot, X])[:cut], np.column_stack([ot, X])[cut:], ct[:cut])
    print(f"\nPredicting the CLOSING total:")
    print(f"  opener alone        -> MAE {base:.3f}")
    print(f"  opener + our feats  -> MAE {np.abs(ct[cut:]-pred_full).mean():.3f}")
    # does the move (close-open) have signal in our features?
    mv = ct - ot
    pm = fit_pred(X[:cut], X[cut:], mv[:cut]); mte = mv[cut:]
    ssr = ((mte - pm) ** 2).sum(); sst = ((mte - mte.mean()) ** 2).sum()
    print(f"  predict the MOVE (close-open) from our feats -> OOS R^2 {1-ssr/sst:+.4f}  corr {np.corrcoef(pm,mte)[0,1]:+.3f}")

    # Betting test: predicted close diverges from open by >K -> bet opener that way
    diff = pred_full - ot[cut:]; ytest = y[cut:]; otest = ot[cut:]; ctest = ct[cut:]
    print("\n  Bet the opener when predicted-close diverges by >K (settle vs actual, -110):")
    for K in (1, 2, 3):
        over = diff > K; under = diff < -K; nb = int(over.sum() + under.sum())
        if nb < 25:
            continue
        wins = int((ytest[over] > otest[over]).sum() + (ytest[under] < otest[under]).sum())
        clv = np.concatenate([ctest[over] - otest[over], otest[under] - ctest[under]]).mean()
        print(f"    >{K}: {nb} bets, win {wins/nb*100:.1f}%, ROI {(wins*0.91-(nb-wins))/nb*100:+.1f}%, "
              f"CLV {clv:+.2f} pts (line moved our way)")


if __name__ == "__main__":
    main()
