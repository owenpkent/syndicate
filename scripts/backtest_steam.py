"""Backtest the steam (line-movement) strategy with realistic execution + risk.

The validated edge: the total's move from open->close is informative. But you can't
bet the open knowing the close — you react *after* the move starts, capturing only
a FRACTION phi of it. Entry line = close - phi*(close-open): phi=1 is the open
(hindsight upper bound), phi=0 is the close (just the vig, losing). Realistic
chasing is phi ~ 0.3-0.6.

Reports proper risk metrics (not just ROI): max drawdown, Sharpe, longest losing
streak, on flat 1-unit stakes at -110, per sport, across a phi sweep. Honest about
the fact that the deployable edge is a fraction of the hindsight number.

    python scripts/backtest_steam.py [--minmove-sd 0.15]
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
from scipy.stats import norm

DATA = Path(__file__).resolve().parent.parent / "data"
PLOTS = DATA / "plots"
JUICE = 0.91  # -110 -> profit per 1 unit on a win
DEC = 1.0 + JUICE  # decimal odds at -110


def _f(x):
    try:
        v = float(x); return None if v != v else v
    except (TypeError, ValueError):
        return None


def load(sport):
    out = []
    for r in json.load(open(DATA / f"{sport}_archive_10Y.json")):
        ot, ct = _f(r.get("open_over_under")), _f(r.get("close_over_under"))
        hf = _f(r.get("home_final")) if r.get("home_final") is not None else _f(r.get("home_score"))
        af = _f(r.get("away_final")) if r.get("away_final") is not None else _f(r.get("away_score"))
        if None in (ot, ct, hf, af):
            continue
        out.append((ot, ct, hf + af))
    return out


def simulate(games, phi, minmove):
    """Flat 1-unit bets; return per-bet pnl array (chronological)."""
    pnl = []
    for ot, ct, tot in games:
        move = ct - ot
        if abs(move) < minmove:
            continue
        L = ct - phi * move                  # entry line: phi of the way from close to open
        if tot == L:
            continue                          # push
        if move > 0:                          # Over steam
            win = tot > L
        else:                                 # Under steam
            win = tot < L
        pnl.append(JUICE if win else -1.0)
    return np.array(pnl)


def bet_list(games, phi, minmove, sigma):
    """Per-bet (p_model, won): p_model = the bettor's own win prob, using the
    close as the fair mean (info available at bet time). Note this can be
    overconfident — the close isn't perfectly fair on big-move games."""
    out = []
    for ot, ct, tot in games:
        move = ct - ot
        if abs(move) < minmove:
            continue
        L = ct - phi * move
        if tot == L:
            continue
        if move > 0:                                   # Over steam
            p = 1 - norm.cdf((L - ct) / sigma); won = tot > L
        else:                                          # Under steam
            p = norm.cdf((L - ct) / sigma); won = tot < L
        out.append((p, won))
    return out


def run_bankroll(bets, start, mode, pct, kfrac, max_stake=0.05):
    """Compounding equity over (p_model, won) bets. Returns (equity_array, metrics).
    max_stake caps any single bet to a % of bankroll — the standard safety against
    an overconfident model blowing up under Kelly."""
    bank = start; eq = [start]
    for p, won in bets:
        if mode == "kelly":
            edge = p * DEC - 1.0                       # f* = edge / net-odds
            frac = max(0.0, kfrac * edge / JUICE)
        else:
            frac = pct
        frac = min(frac, max_stake)
        stake = bank * frac
        bank += stake * JUICE if won else -stake
        if bank <= 0:
            bank = 0.0; eq.append(bank); break
        eq.append(bank)
    eq = np.array(eq)
    peak = np.maximum.accumulate(eq)
    dd = (peak - eq) / peak
    # longest losing streak by equity decreases
    streak = mx = 0
    for i in range(1, len(eq)):
        streak = streak + 1 if eq[i] < eq[i - 1] else 0
        mx = max(mx, streak)
    return eq, {
        "final": eq[-1], "return%": (eq[-1] / start - 1) * 100, "maxDD%": dd.max() * 100,
        "min_bank%": eq.min() / start * 100, "ruin": bool(eq[-1] <= 0), "streak": mx, "n": len(bets),
    }


def metrics(pnl):
    if len(pnl) == 0:
        return None
    cum = np.cumsum(pnl)
    peak = np.maximum.accumulate(cum)
    dd = peak - cum
    # longest losing streak
    streak = mx = 0
    for x in pnl:
        streak = streak + 1 if x < 0 else 0
        mx = max(mx, streak)
    return {
        "n": len(pnl), "win%": (pnl > 0).mean() * 100, "roi%": pnl.mean() * 100,
        "units": cum[-1], "maxDD": dd.max(),
        "sharpe": pnl.mean() / pnl.std() * np.sqrt(len(pnl)) if pnl.std() else 0,
        "streak": mx,
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--minmove-sd", type=float, default=0.12,
                    help="min move to bet, as a fraction of the sport's total SD")
    ap.add_argument("--bankroll", type=float, default=1000.0)
    ap.add_argument("--mode", choices=("flat", "kelly"), default="flat")
    ap.add_argument("--pct", type=float, default=0.01, help="flat stake fraction of bankroll")
    ap.add_argument("--kelly-frac", type=float, default=0.25, help="fraction of full Kelly")
    ap.add_argument("--equity-sport", default="nba")
    ap.add_argument("--no-plot", action="store_true")
    args = ap.parse_args()

    print("Steam backtest — flat 1u @ -110, entry = close - phi*(close-open).")
    print("phi=1 hindsight upper bound; realistic chasing ~0.3-0.6.\n")
    sigmas = {}
    for sport in ("nba", "mlb", "nhl", "nfl"):
        if not (DATA / f"{sport}_archive_10Y.json").exists():
            continue
        g = load(sport); sd = np.std([x[2] for x in g])
        sigmas[sport] = np.std([t - c for o, c, t in g])  # actual-vs-close SD for p_model
        mm = args.minmove_sd * sd
        print(f"=== {sport.upper()} ({len(g)} games, total SD {sd:.1f}, min move {mm:.1f}) ===")
        print(f"{'phi':>5}{'bets':>7}{'win%':>7}{'roi%':>7}{'units':>8}{'maxDD':>7}{'sharpe':>8}{'L-streak':>9}")
        for phi in (1.0, 0.6, 0.5, 0.4, 0.3, 0.0):
            m = metrics(simulate(g, phi, mm))
            if m:
                print(f"{phi:>5.1f}{m['n']:>7}{m['win%']:>7.1f}{m['roi%']:>7.2f}"
                      f"{m['units']:>8.0f}{m['maxDD']:>7.0f}{m['sharpe']:>8.1f}{m['streak']:>9}")
        print()

    # --- Bankroll / equity-curve mode ---
    sport = args.equity_sport
    g = load(sport); sd = np.std([x[2] for x in g]); mm = args.minmove_sd * sd; sig = sigmas[sport]
    sizing = f"{args.mode}" + (f" {args.pct:.1%}" if args.mode == "flat" else f" {args.kelly_frac:g}x-Kelly")
    print(f"=== Bankroll equity ({sport.upper()}, ${args.bankroll:.0f} start, {sizing}) ===")
    print(f"{'phi':>5}{'bets':>7}{'final$':>11}{'return%':>9}{'maxDD%':>8}{'min-bank%':>10}{'ruin':>6}")
    curves = {}
    for phi in (1.0, 0.5, 0.3):
        eq, m = run_bankroll(bet_list(g, phi, mm, sig), args.bankroll, args.mode, args.pct, args.kelly_frac)
        curves[phi] = eq
        print(f"{phi:>5.1f}{m['n']:>7}{m['final']:>11.0f}{m['return%']:>9.1f}{m['maxDD%']:>8.1f}"
              f"{m['min_bank%']:>10.1f}{('YES' if m['ruin'] else 'no'):>6}")

    if not args.no_plot:
        try:
            import matplotlib
            matplotlib.use("Agg")
            import matplotlib.pyplot as plt
            PLOTS.mkdir(parents=True, exist_ok=True)
            plt.figure(figsize=(9, 5))
            for phi, eq in curves.items():
                plt.plot(eq, label=f"φ={phi}")
            plt.axhline(args.bankroll, color="gray", ls=":", lw=1)
            plt.title(f"Steam bankroll — {sport.upper()} ({sizing})")
            plt.xlabel("bet #"); plt.ylabel("bankroll ($)"); plt.legend(); plt.tight_layout()
            out = PLOTS / f"steam_equity_{sport}.png"
            plt.savefig(out, dpi=110)
            print(f"\nEquity curve -> {out}")
        except Exception as exc:  # noqa: BLE001
            print(f"(plot skipped: {exc})")


if __name__ == "__main__":
    main()
