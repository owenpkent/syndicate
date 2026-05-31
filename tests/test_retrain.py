"""Retrain orchestration sequencing (optimize -> train)."""
from sportsball.pipelines import retrain


def test_runs_optimize_then_train(monkeypatch):
    calls = []
    monkeypatch.setattr(retrain.optimize, "run", lambda db: calls.append("optimize") or {"k_factor": 20})
    monkeypatch.setattr(retrain.train, "run", lambda db: calls.append("train") or True)
    assert retrain.run(db=None) is True
    assert calls == ["optimize", "train"]


def test_aborts_when_optimize_yields_no_params(monkeypatch):
    calls = []
    monkeypatch.setattr(retrain.optimize, "run", lambda db: None)  # no data
    monkeypatch.setattr(retrain.train, "run", lambda db: calls.append("train") or True)
    assert retrain.run(db=None) is False
    assert calls == []  # training never runs without params
