"""Forward simulation: can we predict *movement* in crypto? (magnitude, not direction).

The honest target. Notebook 10 already showed 1-minute return *direction* is
~random (white spectrum, ~0 ACF, ~50% accuracy) but *volatility* clusters and is
forecastable. This script turns that observation into a proper out-of-sample
**simulation** with two stages, exactly as a prediction lab should:

  STAGE 1 - prediction quality (the real deliverable)
    Walk-forward (expanding window, refit each step, strictly no look-ahead) HAR
    forecast of next-block realized variance vs two baselines:
      naive   RV_{t+1} = RV_t                 (random walk in variance)
      ewma    RiskMetrics lambda=0.94          (recursive, no fit)
      HAR     OLS on log-RV over short/mid/long horizons + log-volume + intraday
              seasonality (Corsi 2009, adapted to intraday blocks)
    Scored OOS by R^2 on log-RV, QLIKE (the robust vol loss), and a
    Mincer-Zarnowitz calibration regression (realized = a + b*forecast).

  STAGE 2 - gated backtest (the reality check)
    Runs ONLY if HAR beats both baselines on QLIKE in stage 1. A vol forecast on
    a single perp has exactly one honest tradeable expression: **vol-targeting**
    (scale exposure to hit constant risk -- it needs no directional edge, which
    we know we don't have). We measure what that actually buys: (a) risk control
    -- does it stabilise realised vol? -- and (b) Sharpe/return/drawdown net of
    turnover fees vs flat buy-and-hold. Honest caveat baked in: ~14 days is one
    regime, so the *return* comparison is noise; the robust claim is risk control.

Reads data/defi.duckdb read-only (cex_candles, Coinbase 1m). No look-ahead by
construction: every forecast at block t uses only blocks <= t; the only
"future" input is the wall clock (hour-of-day of the target block), which is
deterministic, not information.

    python research/defi/vol_forecast_sim.py
    python research/defi/vol_forecast_sim.py --assets BTC,ETH,SOL --block-min 15
    python research/defi/vol_forecast_sim.py --no-plot
"""
from __future__ import annotations

import argparse

import duckdb
import numpy as np
import pandas as pd

from _common import DEFI_DB, get_logger

log = get_logger("vol_forecast_sim")

EPS = 1e-12
LAMBDA = 0.94          # RiskMetrics EWMA decay
FEE_BPS = 2.0          # round-trip-ish per unit turnover, basis points
WMAX = 3.0             # vol-target leverage cap


# ----------------------------------------------------------------------------- data
def load_blocks(con, venue: str, asset: str, block_min: int) -> pd.DataFrame:
    """1m candles -> per-block realised variance, return, volume, clock.

    RV_block = sum of 1m squared log-returns in the block (the standard realised
    variance estimator). Blocks missing >20% of their minutes are dropped so a
    data gap never masquerades as a calm period.
    """
    df = con.execute(
        "SELECT t, close, volume FROM cex_candles "
        "WHERE venue=? AND asset=? ORDER BY t",
        [venue, asset],
    ).df()
    if len(df) < 1000:
        return pd.DataFrame()
    df["t"] = pd.to_datetime(df["t"])
    df = df.set_index("t").sort_index()
    df["r"] = np.log(df["close"]).diff()
    df = df.dropna(subset=["r"])

    def _bv(s):
        """Bipower variation: (pi/2) * sum |r_i||r_{i-1}|. Robust to jumps -> the
        *continuous* part of variance. RV - BV (floored at 0) is the jump part."""
        a = np.abs(s.to_numpy())
        if len(a) < 2:
            return 0.0
        return float((np.pi / 2.0) * np.sum(a[1:] * a[:-1]))

    rule = f"{block_min}min"
    g = df.resample(rule)
    blk = pd.DataFrame({
        "rv": g["r"].apply(lambda s: float(np.sum(s.to_numpy() ** 2))),
        "bv": g["r"].apply(_bv),                    # continuous-variance estimator
        "ret": g["r"].sum(),                       # log-return over the block
        "volume": g["volume"].sum(),
        "n": g["r"].count(),
    })
    full = int(block_min * 0.8)
    blk = blk[blk["n"] >= full].copy()
    blk = blk[blk["rv"] > 0]
    blk["hour"] = blk.index.hour + blk.index.minute / 60.0
    return blk.reset_index(drop=False)


def build_features(blk: pd.DataFrame) -> pd.DataFrame:
    """HAR design matrix predicting log-RV of the NEXT block (target = logrv_next).

    Features at row t (all from info <= t, except the deterministic target clock):
      har_s  log-RV of block t              (short / "daily" term)
      har_m  mean log-RV over last 4 blocks (mid / "weekly")
      har_l  mean log-RV over last 16 blocks(long / "monthly")
      logvol log block volume at t
      sin,cos of target hour-of-day         (the intraday cycle nb10 found)

    HAR-CJ extension (ABD 2007): split RV into continuous (BV) + jump
    (max(RV-BV,0)) parts and give the regression separate log-continuous terms
    plus a short log-jump term, testing whether jumps forecast differently than
    smooth vol.
      harc_s/m/l  log continuous (BV) over short/mid/long
      harj_s      log(1+jump) of block t
    """
    b = blk.copy()
    b["logrv"] = np.log(b["rv"] + EPS)
    b["har_s"] = b["logrv"]
    b["har_m"] = b["logrv"].rolling(4).mean()
    b["har_l"] = b["logrv"].rolling(16).mean()
    b["logvol"] = np.log(b["volume"] + 1.0)
    # jump decomposition
    cont = np.minimum(b["bv"].to_numpy(), b["rv"].to_numpy())   # BV<=RV by construction
    jump = np.maximum(b["rv"].to_numpy() - cont, 0.0)
    b["logc"] = np.log(cont + EPS)
    b["harc_s"] = b["logc"]
    b["harc_m"] = b["logc"].rolling(4).mean()
    b["harc_l"] = b["logc"].rolling(16).mean()
    b["harj_s"] = np.log(jump + EPS)                            # log jump, EPS-floored
    # target: next block's log-RV / RV / return
    b["logrv_next"] = b["logrv"].shift(-1)
    b["rv_next"] = b["rv"].shift(-1)
    b["ret_next"] = b["ret"].shift(-1)
    hour_next = b["hour"].shift(-1)
    b["sin"] = np.sin(2 * np.pi * hour_next / 24.0)
    b["cos"] = np.cos(2 * np.pi * hour_next / 24.0)
    return b.dropna().reset_index(drop=True)


# ----------------------------------------------------------------------------- models
FEATS = ["har_s", "har_m", "har_l", "logvol", "sin", "cos"]                  # plain HAR
FEATS_J = ["harc_s", "harc_m", "harc_l", "harj_s", "logvol", "sin", "cos"]   # HAR-CJ


def _ols_forecast(b, feats, min_train, refit):
    """Expanding-window OLS on log-RV over `feats`. Returns OOS variance forecasts.

    Refits every `refit` steps for speed; between refits the last coefficients
    are reused (still strictly causal). exp() of a log-forecast is biased low, so
    we add the standard +0.5*sigma^2 smearing correction using the train-residual
    variance -> an (approximately) unbiased RV forecast for QLIKE/calibration.
    """
    y = b["logrv_next"].to_numpy()
    n = len(b)
    Xd = np.column_stack([np.ones(n), b[feats].to_numpy()])     # intercept
    f = np.full(n, np.nan)
    beta = None
    smear = 0.0
    for i in range(min_train, n):
        if beta is None or (i - min_train) % refit == 0:
            Xtr, ytr = Xd[:i], y[:i]
            beta, *_ = np.linalg.lstsq(Xtr, ytr, rcond=None)
            resid = ytr - Xtr @ beta
            smear = 0.5 * float(np.var(resid))
        f[i] = np.exp(Xd[i] @ beta + smear)                     # variance scale
    return f


def walk_forward(b: pd.DataFrame, min_train: int, refit: int):
    """OOS forecasts (variance scale) for HAR, HAR-CJ, and two baselines."""
    f_har = _ols_forecast(b, FEATS, min_train, refit)
    f_harj = _ols_forecast(b, FEATS_J, min_train, refit)

    # baselines on the variance scale, aligned to the same target (rv_next)
    rv = b["rv"].to_numpy()
    f_naive = rv.copy()                              # RV_{t+1} ~= RV_t
    f_ewma = np.full(len(b), np.nan)
    var = rv[0]
    for i in range(len(b)):
        var = LAMBDA * var + (1 - LAMBDA) * rv[i]    # fold in block i (current info)
        f_ewma[i] = var                              # forecast for block i+1, made at i

    mask = ~np.isnan(f_har)
    return {
        "mask": mask,
        "rv_next": b["rv_next"].to_numpy(),
        "ret_next": b["ret_next"].to_numpy(),
        "har": f_har, "harj": f_harj, "naive": f_naive, "ewma": f_ewma,
        "train_logrv_mean": float(np.mean(b["logrv_next"].to_numpy()[:min_train])),
    }


# ----------------------------------------------------------------------------- metrics
def qlike(rv_true, f):
    """QLIKE loss on variance: RV/f - log(RV/f) - 1, mean. Robust to vol spikes."""
    rv_true = np.maximum(rv_true, EPS); f = np.maximum(f, EPS)
    x = rv_true / f
    return float(np.mean(x - np.log(x) - 1.0))


def r2_logrv(rv_true, f, train_log_mean):
    yt = np.log(np.maximum(rv_true, EPS))
    yp = np.log(np.maximum(f, EPS))
    ss_res = np.sum((yt - yp) ** 2)
    ss_tot = np.sum((yt - train_log_mean) ** 2)
    return float(1 - ss_res / ss_tot)


def mincer_zarnowitz(rv_true, f):
    """Realised vol = a + b*forecast vol. Calibrated => a~=0, b~=1."""
    vt = np.sqrt(np.maximum(rv_true, EPS))
    vf = np.sqrt(np.maximum(f, EPS))
    A = np.column_stack([np.ones_like(vf), vf])
    coef, *_ = np.linalg.lstsq(A, vt, rcond=None)
    return float(coef[0]), float(coef[1])


def score(res):
    m = res["mask"]
    rv = res["rv_next"][m]
    out = {}
    for name in ("har", "harj", "naive", "ewma"):
        f = res[name][m]
        a, bcoef = mincer_zarnowitz(rv, f)
        out[name] = {
            "qlike": qlike(rv, f),
            "r2_logrv": r2_logrv(rv, f, res["train_logrv_mean"]),
            "mz_a": a, "mz_b": bcoef,
        }
    return out


# ----------------------------------------------------------------------------- backtest
def annualize_sharpe(rets, blocks_per_year):
    mu = np.mean(rets); sd = np.std(rets)
    if sd <= 0:
        return 0.0
    return float(mu / sd * np.sqrt(blocks_per_year))


def max_drawdown(equity):
    peak = np.maximum.accumulate(equity)
    return float(np.min(equity / peak - 1.0))


def rolling_vol_cov(rets, win=16):
    """Coefficient of variation of rolling realised vol -> how STABLE the risk is.

    Lower = the strategy's risk is more constant (the point of vol-targeting),
    independent of any directional edge."""
    s = pd.Series(rets)
    rv = s.rolling(win).std().dropna().to_numpy()
    rv = rv[rv > 0]
    if len(rv) < 2:
        return float("nan")
    return float(np.std(rv) / np.mean(rv))


def backtest(res, blk_for_train_target, min_train, block_min, model="har"):
    """Vol-targeting vs buy-and-hold over the OOS region, net of turnover fees."""
    m = res["mask"]
    f = res[model][m]                                # variance forecast for t+1
    ret = res["ret_next"][m]                         # realised block return t+1
    fvol = np.sqrt(np.maximum(f, EPS))               # forecast vol
    target = float(np.median(np.sqrt(blk_for_train_target[:min_train])))  # train RV->vol

    w = np.clip(target / fvol, 0.0, WMAX)
    turn = np.abs(np.diff(np.concatenate([[0.0], w])))
    fee = turn * (FEE_BPS / 1e4)
    strat = w * ret - fee
    hold = ret

    bpy = 365 * 24 * (60 / block_min)
    eq_s = np.cumprod(1 + strat); eq_h = np.cumprod(1 + hold)
    return {
        "target_vol": target,
        "n": int(len(strat)),
        "strat": {
            "total_ret": float(eq_s[-1] - 1), "sharpe": annualize_sharpe(strat, bpy),
            "maxdd": max_drawdown(eq_s), "vol_cov": rolling_vol_cov(strat),
            "avg_lev": float(np.mean(w)),
        },
        "hold": {
            "total_ret": float(eq_h[-1] - 1), "sharpe": annualize_sharpe(hold, bpy),
            "maxdd": max_drawdown(eq_h), "vol_cov": rolling_vol_cov(hold),
            "avg_lev": 1.0,
        },
    }


# ----------------------------------------------------------------------------- plot
def save_calibration_plot(per_asset, path):
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except Exception:  # noqa: BLE001
        log.info("matplotlib unavailable - skipping plot")
        return None
    fig, ax = plt.subplots(1, 2, figsize=(13, 5))
    for asset, d in per_asset.items():
        res = d["res"]; m = res["mask"]
        rv = np.sqrt(np.maximum(res["rv_next"][m], EPS)) * 1e4   # bps
        f = np.sqrt(np.maximum(res[d["winner"]][m], EPS)) * 1e4
        order = np.argsort(f)
        q = np.array_split(order, 10)
        px = [f[idx].mean() for idx in q]
        py = [rv[idx].mean() for idx in q]
        ax[0].plot(px, py, marker="o", label=f"{asset} ({d['winner']})")
        # equity curves
        bt = d["bt"]
        ax[1].plot(np.cumprod(1 + (res["ret_next"][m])), ls="--", alpha=.5,
                   label=f"{asset} hold")
    lim = max(ax[0].get_xlim()[1], ax[0].get_ylim()[1])
    ax[0].plot([0, lim], [0, lim], color="gray", lw=.8, ls=":")
    ax[0].set_title("HAR vol-forecast calibration (decile)")
    ax[0].set_xlabel("forecast vol (bps/block)"); ax[0].set_ylabel("realised vol (bps/block)")
    ax[0].legend()
    ax[1].set_title("buy-and-hold equity (OOS blocks)")
    ax[1].set_xlabel("block"); ax[1].legend()
    fig.tight_layout(); fig.savefig(path, dpi=110)
    return path


# ----------------------------------------------------------------------------- main
def run(assets, venue, block_min, min_train, refit, do_plot):
    con = duckdb.connect(DEFI_DB, read_only=True)
    per_asset = {}
    try:
        for asset in assets:
            blk = load_blocks(con, venue, asset, block_min)
            if blk.empty:
                log.info("%s: insufficient data, skipping", asset); continue
            b = build_features(blk)
            if len(b) < min_train + 50:
                log.info("%s: only %d blocks, need >%d, skipping",
                         asset, len(b), min_train + 50); continue
            res = walk_forward(b, min_train, refit)
            sc = score(res)
            winner = "harj" if sc["harj"]["qlike"] < sc["har"]["qlike"] else "har"
            bt = backtest(res, b["rv"].to_numpy(), min_train, block_min, model=winner)
            per_asset[asset] = {"b": b, "res": res, "score": sc, "bt": bt,
                                "winner": winner}
    finally:
        con.close()

    if not per_asset:
        log.error("no assets had enough data"); return

    # ---- report -------------------------------------------------------------
    print("\n" + "=" * 78)
    print(f"STAGE 1 - PREDICTION QUALITY  (venue={venue}, block={block_min}m, "
          f"walk-forward OOS, refit/{refit})")
    print("=" * 78)
    print(f"{'asset':6} {'model':6} {'QLIKE':>9} {'R2(logRV)':>10} "
          f"{'MZ a':>9} {'MZ b':>7}   (lower QLIKE / higher R2 / b~1 = better)")
    gate_pass = {}
    for asset, d in per_asset.items():
        sc = d["score"]; win = d["winner"]
        for name in ("naive", "ewma", "har", "harj"):
            s = sc[name]
            tag = " <-win" if name == win else ""
            print(f"{asset:6} {name:6} {s['qlike']:9.4f} {s['r2_logrv']:10.3f} "
                  f"{s['mz_a']:9.2e} {s['mz_b']:7.3f}{tag}")
        base_q = min(sc["naive"]["qlike"], sc["ewma"]["qlike"])
        base_r = max(sc["naive"]["r2_logrv"], sc["ewma"]["r2_logrv"])
        beats = sc[win]["qlike"] < base_q and sc[win]["r2_logrv"] > base_r
        gate_pass[asset] = beats
        dj = sc["har"]["qlike"] - sc["harj"]["qlike"]   # >0 => jumps help
        print(f"       -> {win.upper()} beats both baselines: {beats}  "
              f"(jump term delta-QLIKE {dj:+.4f}: >0 means HAR-CJ helps)\n")

    print("=" * 78)
    print("STAGE 2 - GATED BACKTEST  (vol-targeting vs buy-and-hold, net of fees)")
    print("=" * 78)
    bpy = 365 * 24 * (60 / block_min)
    print(f"  fee={FEE_BPS}bps/turnover  lev_cap={WMAX}  blocks/yr~={bpy:.0f}")
    print(f"{'asset':6} {'strat':>26} | {'buy&hold':>26}")
    print(f"{'':6} {'ret':>7}{'Shrp':>7}{'mDD':>7}{'volCoV':>8} | "
          f"{'ret':>7}{'Shrp':>7}{'mDD':>7}{'volCoV':>8}")
    for asset, d in per_asset.items():
        if not gate_pass[asset]:
            print(f"{asset:6}  -- gate not passed, backtest skipped --")
            continue
        s = d["bt"]["strat"]; h = d["bt"]["hold"]
        print(f"{asset:6} {s['total_ret']:7.3f}{s['sharpe']:7.2f}{s['maxdd']:7.2f}"
              f"{s['vol_cov']:8.3f} | "
              f"{h['total_ret']:7.3f}{h['sharpe']:7.2f}{h['maxdd']:7.2f}{h['vol_cov']:8.3f}")
    print()
    print("  Read: volCoV = coeff-of-variation of rolling realised vol; LOWER = more")
    print("  stable risk (the real payoff of a vol forecast, needs no price edge).")
    print("  Sharpe/ret over ~2wk single regime is NOISE - do not read as alpha.")

    if do_plot:
        out = save_calibration_plot(per_asset, str(_plot_path(block_min)))
        if out:
            print(f"\n  calibration + equity plot -> {out}")
    print()


def _plot_path(block_min):
    from _common import _ROOT  # type: ignore
    p = _ROOT / "data" / "plots"
    p.mkdir(exist_ok=True)
    return p / f"vol_forecast_{block_min}m.png"


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--assets", default="BTC,ETH,SOL")
    ap.add_argument("--venue", default="coinbase")
    ap.add_argument("--block-min", type=int, default=15,
                    help="forecast block size in minutes")
    ap.add_argument("--min-train", type=int, default=400,
                    help="blocks before OOS forecasting begins")
    ap.add_argument("--refit", type=int, default=4, help="refit HAR every N blocks")
    ap.add_argument("--no-plot", action="store_true")
    a = ap.parse_args()
    run([x.strip().upper() for x in a.assets.split(",") if x.strip()],
        a.venue, a.block_min, a.min_train, a.refit, not a.no_plot)


if __name__ == "__main__":
    main()
