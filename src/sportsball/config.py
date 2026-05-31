"""Centralized, typed configuration.

Replaces the ~14 copy-pasted ``get_db_connection`` blocks and ad-hoc
``os.getenv`` calls scattered across the old codebase. Every value has a single
documented home here, sourced from the environment with sane fallbacks, plus the
strategy parameters loaded from ``config/settings.json``.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field, asdict
from pathlib import Path

# Default location of the JSON strategy parameters. Overridable via env so the
# same code runs from a container (/app/config) or a host checkout.
DEFAULT_SETTINGS_PATH = os.getenv(
    "SPORTSBALL_SETTINGS", "/app/config/settings.json"
)


def _env(name: str, default: str) -> str:
    return os.getenv(name, default)


@dataclass(frozen=True)
class DBConfig:
    host: str = field(default_factory=lambda: _env("DB_HOST", "localhost"))
    port: int = field(default_factory=lambda: int(_env("DB_PORT", "5432")))
    name: str = field(default_factory=lambda: _env("POSTGRES_DB", "market_history"))
    user: str = field(default_factory=lambda: _env("POSTGRES_USER", "sportsball_admin"))
    password: str = field(
        default_factory=lambda: _env("POSTGRES_PASSWORD", "changeme_in_env")
    )

    def dsn_kwargs(self) -> dict:
        return {
            "host": self.host,
            "port": self.port,
            "dbname": self.name,
            "user": self.user,
            "password": self.password,
        }


@dataclass(frozen=True)
class RedisConfig:
    host: str = field(default_factory=lambda: _env("REDIS_HOST", "localhost"))
    port: int = field(default_factory=lambda: int(_env("REDIS_PORT", "6379")))
    db: int = field(default_factory=lambda: int(_env("REDIS_DB", "0")))


@dataclass(frozen=True)
class StrategyConfig:
    """Tunable trading parameters, loaded from ``config/settings.json``."""

    safety_buffer_ev: float = 0.02
    kelly_multiplier: float = 0.25
    default_slippage: float = 0.005
    max_global_exposure_pct: float = 0.15
    correlation_penalty_multiplier: float = 0.5
    # When True (default) the Engine refuses to trade on a probability it did
    # not model itself — i.e. it never stakes on a producer-supplied or random
    # ``true_prob``. This is the switch that makes "no edge" mean "no bet".
    require_model: bool = True

    @classmethod
    def load(cls, path: str | os.PathLike = DEFAULT_SETTINGS_PATH) -> "StrategyConfig":
        try:
            data = json.loads(Path(path).read_text())
        except (FileNotFoundError, json.JSONDecodeError):
            data = {}
        known = {k: data[k] for k in data if k in cls.__dataclass_fields__}
        return cls(**known)


@dataclass(frozen=True)
class Settings:
    db: DBConfig = field(default_factory=DBConfig)
    redis: RedisConfig = field(default_factory=RedisConfig)
    strategy: StrategyConfig = field(default_factory=StrategyConfig.load)

    # Agent-level knobs.
    execution_mode: str = field(default_factory=lambda: _env("EXECUTION_MODE", "PAPER"))
    slippage_tolerance: float = field(
        default_factory=lambda: float(_env("SLIPPAGE_TOLERANCE_PCT", "0.005"))
    )
    polling_interval: int = field(
        default_factory=lambda: int(_env("POLLING_INTERVAL", "30"))
    )
    settlement_interval: int = field(
        default_factory=lambda: int(_env("SETTLEMENT_INTERVAL", "60"))
    )
    retrain_interval: int = field(
        default_factory=lambda: int(_env("RETRAIN_INTERVAL", "86400"))  # daily
    )
    rundown_api_key: str | None = field(
        default_factory=lambda: os.getenv("RUNDOWN_API_KEY")
    )

    def has_live_rundown_key(self) -> bool:
        key = self.rundown_api_key
        return bool(key) and key != "your_rundown_api_key_here"

    def as_dict(self) -> dict:
        return asdict(self)


def load_settings() -> Settings:
    """Build a fresh :class:`Settings` from the current environment."""
    return Settings()
