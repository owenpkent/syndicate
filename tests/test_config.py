"""Configuration loading from env and settings.json."""
import json

from sportsball.config import DBConfig, Settings, StrategyConfig, load_settings


class TestStrategyConfig:
    def test_defaults_when_file_missing(self, tmp_path):
        cfg = StrategyConfig.load(tmp_path / "nope.json")
        assert cfg.kelly_multiplier == 0.25
        assert cfg.require_model is True

    def test_loads_known_keys_ignores_unknown(self, tmp_path):
        path = tmp_path / "settings.json"
        path.write_text(json.dumps({"kelly_multiplier": 0.5, "bogus": 99}))
        cfg = StrategyConfig.load(path)
        assert cfg.kelly_multiplier == 0.5
        assert not hasattr(cfg, "bogus")


class TestDBConfig:
    def test_reads_env(self, monkeypatch):
        monkeypatch.setenv("POSTGRES_PASSWORD", "s3cret")
        monkeypatch.setenv("POSTGRES_USER", "alice")
        cfg = DBConfig()
        assert cfg.password == "s3cret"
        assert cfg.dsn_kwargs()["user"] == "alice"


class TestSettings:
    def test_rundown_key_detection(self, monkeypatch):
        monkeypatch.setenv("RUNDOWN_API_KEY", "your_rundown_api_key_here")
        assert load_settings().has_live_rundown_key() is False
        monkeypatch.setenv("RUNDOWN_API_KEY", "real-key-123")
        assert load_settings().has_live_rundown_key() is True

    def test_execution_mode_default(self, monkeypatch):
        monkeypatch.delenv("EXECUTION_MODE", raising=False)
        assert Settings().execution_mode == "PAPER"
