"""Web dashboard: provider snapshots + FastAPI endpoints (no infra needed)."""
import json

import pytest

from sportsball.web.providers import (
    DemoProvider,
    StoreProvider,
    assemble,
    model_status,
)

from fakes import FakeDB

fastapi = pytest.importorskip("fastapi")  # skip cleanly if the web extra isn't installed
from fastapi.testclient import TestClient  # noqa: E402

from sportsball.web.app import create_app  # noqa: E402

PANELS = {"source", "generated_at", "performance", "edge", "live"}


class TestDemoProvider:
    def test_snapshot_shape_and_seed_stable_aggregates(self):
        a = DemoProvider(seed=7).snapshot()
        b = DemoProvider(seed=7).snapshot()
        assert PANELS <= set(a)
        assert a["source"] == "demo"
        # The record timestamps anchor to wall-clock (so the demo looks live), but
        # the seed-driven aggregates must be identical across runs.
        for panel, key in [("performance", "realized_pnl"), ("performance", "settled"),
                           ("performance", "win_rate"), ("performance", "total_trades"),
                           ("edge", "avg_clv"), ("edge", "signals_bet")]:
            assert a[panel][key] == b[panel][key]

    def test_panels_are_internally_consistent(self):
        s = DemoProvider(seed=3).snapshot()
        p, e, l = s["performance"], s["edge"], s["live"]
        assert p["settled"] + p["open"] == p["total_trades"]
        assert 0.0 <= p["win_rate"] <= 1.0
        assert len(p["equity_curve"]) == p["settled"]
        assert e["signals_bet"] <= e["signals_evaluated"]
        assert l["open_positions"]  # the newest few trades stay OPEN
        assert all(t["status"] == "OPEN" for t in l["open_positions"])

    def test_json_serializable(self):
        # The endpoint returns this dict; it must be JSON-native (no datetimes).
        json.dumps(DemoProvider().snapshot())


class TestModelStatus:
    def test_absent_when_no_artifact(self, tmp_path):
        m = model_status(tmp_path)
        assert m["status"] == "absent"
        assert m["schema_version"] is None

    def test_stale_when_schema_mismatch(self, tmp_path):
        (tmp_path / "model_meta.json").write_text(json.dumps(
            {"schema_version": 1, "n_features": 1, "feature_order": ["elo_diff_hfa"]}))
        m = model_status(tmp_path)
        assert m["status"] == "stale"
        assert "retrain" in m["reason"]

    def test_live_when_schema_matches_code(self, tmp_path):
        from sportsball.quant import features as feat
        (tmp_path / "win_prob_model.pkl").write_bytes(b"x")
        (tmp_path / "model_meta.json").write_text(json.dumps(
            {"schema_version": feat.SCHEMA_VERSION, "n_features": feat.N_FEATURES,
             "feature_order": feat.FEATURE_ORDER, "temperature": 1.2, "hfa": 55.0}))
        m = model_status(tmp_path)
        assert m["status"] == "live"
        assert m["last_retrain"] is not None


class TestStoreProvider:
    def test_unavailable_db_gives_empty_snapshot_not_crash(self):
        s = StoreProvider(_FakeStore(available=False)).snapshot()
        assert s["source"] == "postgres"
        assert s["performance"]["total_trades"] == 0


class TestApp:
    def setup_method(self):
        self.client = TestClient(create_app(DemoProvider(seed=1)))

    def test_healthz(self):
        r = self.client.get("/healthz")
        assert r.status_code == 200 and r.json()["ok"] is True

    def test_index_serves_html(self):
        r = self.client.get("/")
        assert r.status_code == 200
        assert "Sportsball" in r.text and "api/snapshot" in r.text

    def test_snapshot_endpoint_includes_model(self):
        r = self.client.get("/api/snapshot")
        assert r.status_code == 200
        body = r.json()
        assert PANELS <= set(body)
        assert "model" in body and "status" in body["model"]


class _FakeStore:
    """Minimal Store stand-in for the unavailable-DB path."""
    def __init__(self, available):
        self.available = available


def test_assemble_empty_is_well_formed():
    s = assemble("demo", [], [], [])
    assert s["performance"]["total_trades"] == 0
    assert s["edge"]["avg_clv"] is None
    assert s["live"]["recent_trades"] == []
