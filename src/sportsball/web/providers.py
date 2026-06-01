"""Data providers for the web dashboard — one snapshot shape, three sources.

A :class:`DataProvider` returns a single ``snapshot()`` dict with four panels —
``performance``, ``edge``, ``live`` — plus ``source``/``generated_at``. The app
adds the ``model`` panel itself (always read from the real on-disk artifacts via
:func:`model_status`, so it's honest regardless of which data source is in use).

Providers:
- :class:`DemoProvider` — deterministic in-memory synthetic data; the offline
  default so the UI renders with zero infrastructure.
- :class:`DuckDBProvider` — reads FINAL games from a DuckDB ``events`` table (e.g.
  the one ``make dryrun`` writes); fills the panels it can, ``n/a`` for the rest.
- :class:`StoreProvider` — the real Postgres-backed repository (:mod:`store`).

Keeping the snapshot shape identical across all three means the page and the tests
never branch on the source.
"""
from __future__ import annotations

import json
import random
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional, Protocol

from ..quant import features as feat

MODEL_DIR = Path("models")
START_BANKROLL = 1000.0


# --------------------------------------------------------------------------- #
# Model status — always the real artifacts on disk (offline, honest).
# --------------------------------------------------------------------------- #
def model_status(model_dir: Path = MODEL_DIR) -> dict:
    """The shipped model's contract + whether the Engine would serve or abstain.

    Reads ``model_meta.json``; compares its schema to the code's
    ``feat.SCHEMA_VERSION``. ``status`` is one of:
      - ``absent``     — no artifact (Engine abstains, no model).
      - ``stale``      — artifact schema != code (Engine abstains until retrain).
      - ``live``       — artifact matches; the Engine would serve P_true.
    """
    meta_path = Path(model_dir) / "model_meta.json"
    pkl_path = Path(model_dir) / "win_prob_model.pkl"
    base = {
        "code_schema_version": feat.SCHEMA_VERSION,
        "code_n_features": feat.N_FEATURES,
        "code_feature_order": feat.FEATURE_ORDER,
    }
    if not meta_path.exists():
        return {**base, "status": "absent", "reason": "no model_meta.json",
                "schema_version": None, "n_features": None, "feature_order": [],
                "temperature": None, "hfa": None, "k_factor": None, "last_retrain": None}
    try:
        meta = json.loads(meta_path.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        return {**base, "status": "absent", "reason": f"unreadable meta ({exc})",
                "schema_version": None, "n_features": None, "feature_order": [],
                "temperature": None, "hfa": None, "k_factor": None, "last_retrain": None}
    schema_ok = (meta.get("schema_version") == feat.SCHEMA_VERSION
                 and meta.get("n_features") == feat.N_FEATURES
                 and meta.get("feature_order") == feat.FEATURE_ORDER)
    status = "live" if schema_ok else "stale"
    reason = ("" if schema_ok
              else f"artifact v{meta.get('schema_version')} != code v{feat.SCHEMA_VERSION}; run `make retrain`")
    last_retrain = None
    if pkl_path.exists():
        last_retrain = datetime.fromtimestamp(pkl_path.stat().st_mtime, timezone.utc).isoformat()
    return {
        **base,
        "status": status,
        "reason": reason,
        "schema_version": meta.get("schema_version"),
        "n_features": meta.get("n_features"),
        "feature_order": meta.get("feature_order", []),
        "temperature": meta.get("temperature"),
        "hfa": meta.get("hfa"),
        "k_factor": meta.get("k_factor"),
        "last_retrain": last_retrain,
    }


# --------------------------------------------------------------------------- #
# Snapshot assembly helpers (pure) — turn lists of records into the panels.
# --------------------------------------------------------------------------- #
def _performance(trades: list[dict]) -> dict:
    """Performance panel from a trade ledger (each: stake, pnl, status, ts)."""
    settled = [t for t in trades if t["status"] in ("WIN", "LOSS")]
    open_ = [t for t in trades if t["status"] == "OPEN"]
    realized = sum(t["pnl"] for t in settled)
    turnover = sum(t["stake"] for t in settled)
    wins = sum(1 for t in settled if t["status"] == "WIN")
    # Flat-base equity curve (constant base bankroll; see scripts/backtest.py).
    curve, bankroll = [], START_BANKROLL
    for t in sorted(settled, key=lambda r: r["ts"]):
        bankroll += t["pnl"]
        curve.append({"t": t["ts"], "bankroll": round(bankroll, 4)})
    return {
        "realized_pnl": round(realized, 4),
        "roi": round(realized / turnover, 4) if turnover else 0.0,
        "win_rate": round(wins / len(settled), 4) if settled else 0.0,
        "settled": len(settled),
        "open": len(open_),
        "total_trades": len(trades),
        "turnover": round(turnover, 4),
        "equity_curve": curve,
    }


def _edge(signals: list[dict], events: list[dict], trades: list[dict]) -> dict:
    """Edge panel: CLV, favorite baseline, abstain rate."""
    clvs = [t["clv"] for t in trades if t.get("clv") is not None]
    graded = [e for e in events if e.get("home_score") is not None
              and e.get("home_close") and e.get("away_close")]
    fav_correct = sum(1 for e in graded
                      if (e["home_close"] < e["away_close"]) == (e["home_score"] > e["away_score"]))
    evaluated = len(signals)
    bet = sum(1 for s in signals if s.get("bet"))
    return {
        "avg_clv": round(sum(clvs) / len(clvs), 4) if clvs else None,
        "clv_beat_rate": round(sum(1 for c in clvs if c > 0) / len(clvs), 4) if clvs else None,
        "n_clv": len(clvs),
        "favorite_hit_rate": round(fav_correct / len(graded), 4) if graded else None,
        "events_graded": len(graded),
        "signals_evaluated": evaluated,
        "signals_bet": bet,
        "abstain_rate": round(1 - bet / evaluated, 4) if evaluated else None,
    }


def _live(signals: list[dict], trades: list[dict], *, n: int = 12) -> dict:
    """Live panel: open exposure, arb count, recent signals & trades."""
    open_positions = [t for t in trades if t["status"] == "OPEN"]
    return {
        "open_exposure": round(sum(t["stake"] for t in open_positions), 4),
        "open_positions": sorted(open_positions, key=lambda r: r["ts"], reverse=True)[:n],
        "arb_count": sum(1 for t in trades if t.get("is_arb")),
        "recent_signals": sorted(signals, key=lambda r: r["ts"], reverse=True)[:n],
        "recent_trades": sorted(trades, key=lambda r: r["ts"], reverse=True)[:n],
    }


def assemble(source: str, signals: list[dict], events: list[dict], trades: list[dict]) -> dict:
    return {
        "source": source,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "performance": _performance(trades),
        "edge": _edge(signals, events, trades),
        "live": _live(signals, trades),
    }


# --------------------------------------------------------------------------- #
# Providers
# --------------------------------------------------------------------------- #
class DataProvider(Protocol):
    source: str

    def snapshot(self) -> dict: ...


class DemoProvider:
    """Deterministic synthetic pipeline activity — the offline default.

    Clearly SYNTHETIC; it exists so the dashboard renders (and screenshots) with
    no Postgres/DuckDB/network. Edges are modest and labelled, not fantasy.
    """

    source = "demo"

    def __init__(self, seed: int = 7, n_events: int = 160):
        self.seed = seed
        self.n_events = n_events

    def snapshot(self) -> dict:
        rng = random.Random(self.seed)
        teams = ["Lakers", "Celtics", "Warriors", "Nets", "Heat", "Bucks", "Suns", "Nuggets"]
        now = datetime.now(timezone.utc)
        signals: list[dict] = []
        events: list[dict] = []
        trades: list[dict] = []
        for i in range(self.n_events):
            ts = (now - timedelta(hours=6 * i)).isoformat()
            home, away = rng.sample(teams, 2)
            p_home = rng.uniform(0.35, 0.65)
            home_won = rng.random() < p_home
            hs, as_ = (112, 104) if home_won else (103, 109)
            # Closing line = true prob blurred + a small vig; model has a tiny, noisy edge.
            close_home = round(1 / min(0.97, max(0.05, p_home + rng.uniform(-0.05, 0.05)) * 1.045), 3)
            close_away = round(1 / min(0.97, max(0.05, (1 - p_home) + rng.uniform(-0.05, 0.05)) * 1.045), 3)
            model_p = min(0.95, max(0.05, p_home + rng.uniform(-0.04, 0.04)))
            side, side_p, side_close = (("HOME", model_p, close_home) if model_p >= 0.5
                                        else ("AWAY", 1 - model_p, close_away))
            ev = round(side_p * side_close - 1, 4)
            event_id = f"demo_{i:04d}_{away.lower()}_at_{home.lower()}"
            events.append({"event_id": event_id, "home": home, "away": away,
                           "home_score": hs, "away_score": as_,
                           "home_close": close_home, "away_close": close_away})
            bet = ev > 0.02
            signals.append({"ts": ts, "event": f"{away} @ {home}", "side": side,
                            "source": rng.choice(["Pinnacle", "DraftKings", "Polymarket"]),
                            "odds": side_close, "true_prob": round(side_p, 4),
                            "ev": ev, "bet": bet})
            if not bet:
                continue
            # Line-shopped execution: a hair better than the close.
            exec_odds = round(side_close * (1 + rng.uniform(0.0, 0.03)), 3)
            stake = round(0.25 * max(0.0, ev) / max(0.01, exec_odds - 1), 4)  # quarter-Kelly
            stake = min(stake, 0.05)
            won = (side == "HOME" and home_won) or (side == "AWAY" and not home_won)
            trades.append({"ts": ts, "market_id": f"{signals[-1]['source'][:4].upper()}-{event_id}-{side}",
                           "event": f"{away} @ {home}", "side": side, "odds": exec_odds,
                           "stake": stake, "status": "WIN" if won else "LOSS",
                           "pnl": round(stake * (exec_odds - 1) if won else -stake, 4),
                           "clv": round(exec_odds / side_close - 1, 4), "is_arb": False})
        # The newest few bets are still in flight (OPEN, ungraded) — gives the live
        # panel something to show. trades are appended newest-first.
        for t in trades[:min(4, len(trades))]:
            t["status"], t["pnl"] = "OPEN", 0.0
        return assemble(self.source, signals, events, trades)


class DuckDBProvider:
    """Reads FINAL games from a DuckDB ``events`` table (scores; closing odds if
    present). It has no trade/signal ledger, so performance/live stay empty and the
    edge panel reports the market favorite-baseline — useful on ``make dryrun`` data.
    """

    source = "duckdb"

    def __init__(self, path: str):
        self.path = path

    def snapshot(self) -> dict:
        import duckdb
        con = duckdb.connect(self.path, read_only=True)
        try:
            cols = {c[1] for c in con.execute("PRAGMA table_info('events')").fetchall()}
            has_close = {"home_close", "away_close"} <= cols
            sel = ("event_date, home_team, away_team, home_score, away_score"
                   + (", home_close, away_close" if has_close else ""))
            rows = con.execute(
                f"SELECT {sel} FROM events WHERE home_score IS NOT NULL "
                "ORDER BY event_date DESC LIMIT 5000").fetchall()
        finally:
            con.close()
        events = []
        for r in rows:
            e = {"event_id": f"{r[1]}_{r[2]}", "home": r[1], "away": r[2],
                 "home_score": r[3], "away_score": r[4],
                 "home_close": (float(r[5]) if has_close and r[5] else None),
                 "away_close": (float(r[6]) if has_close and r[6] else None)}
            events.append(e)
        snap = assemble(self.source, [], events, [])
        snap["edge"]["events_graded"] = len(events)
        return snap


class StoreProvider:
    """The real Postgres-backed repository (:mod:`store`). Degrades to an empty
    snapshot (not a crash) when the DB is unavailable."""

    source = "postgres"

    def __init__(self, store):
        self.store = store

    def snapshot(self) -> dict:
        if not self.store.available:
            return assemble(self.source, [], [], [])
        try:
            signals, events, trades = self.store.dashboard_data()
        except Exception:  # noqa: BLE001 - table may be empty/absent
            return assemble(self.source, [], [], [])
        return assemble(self.source, signals, events, trades)


def get_provider(*, mode: str = "auto", duckdb_path: Optional[str] = None, settings=None):
    """Pick a provider. ``mode`` ∈ {auto, demo, duckdb, postgres}.

    ``auto`` prefers a reachable Postgres, then a DuckDB file, then demo — so the
    dashboard always renders.
    """
    if mode == "demo":
        return DemoProvider()
    if mode == "duckdb":
        return DuckDBProvider(duckdb_path or "data/sportsball.duckdb")
    if mode == "postgres":
        from ..db import Database
        from ..store import Store
        from ..config import load_settings
        store = Store(Database((settings or load_settings()).db))
        store.db.connect()
        return StoreProvider(store)
    # auto
    from ..db import Database
    from ..store import Store
    from ..config import load_settings
    store = Store(Database((settings or load_settings()).db))
    try:
        store.db.connect()
    except Exception:  # noqa: BLE001
        pass
    if store.available:
        return StoreProvider(store)
    if duckdb_path and Path(duckdb_path).exists():
        return DuckDBProvider(duckdb_path)
    if Path("data/sportsball.duckdb").exists():
        return DuckDBProvider("data/sportsball.duckdb")
    return DemoProvider()
