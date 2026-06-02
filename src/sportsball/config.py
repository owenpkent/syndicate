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
    # Shrink the Kelly stake by the model's calibration-confidence (less certain
    # model -> smaller stake). On by default; set false for plain fractional Kelly.
    uncertainty_scaling: bool = True
    # Blend the logistic with a gradient-boosted tree (ensemble) at train time.
    # On by default; set false to serve the logistic alone.
    model_ensemble: bool = True
    # GBT share of the ensemble blend (logistic gets 1 - this). Validated GBT-
    # dominant: a 3-way train/val/test sweep (notebooks/05) put the optimum at the
    # boundary (GBT-only), with GBT-only beating 50/50 out-of-sample on accuracy
    # AND log-loss. Kept at 0.75 (not 1.0) to retain the better-calibrated logistic,
    # since the Engine shrinks Kelly by calibration confidence.
    ensemble_gbt_weight: float = 0.75
    default_slippage: float = 0.005
    max_global_exposure_pct: float = 0.15
    correlation_penalty_multiplier: float = 0.5
    # When True (default) the Engine refuses to trade on a probability it did
    # not model itself — i.e. it never stakes on a producer-supplied or random
    # ``true_prob``. This is the switch that makes "no edge" mean "no bet".
    require_model: bool = True

    # Elo / feature knobs (used by the modeling pipeline, not the optimizer's
    # search space — the optimizer still tunes only K-factor + HFA).
    elo_mov_enabled: bool = True          # margin-of-victory multiplier on the K update
    elo_carry: float = 0.75               # season carryover: regress toward 1500 each offseason
    elo_offseason_gap_days: int = 90      # a gap this long triggers carryover
    form_window: int = 10                 # rolling win% window for the "form" feature

    @classmethod
    def load(cls, path: str | os.PathLike = DEFAULT_SETTINGS_PATH) -> "StrategyConfig":
        try:
            data = json.loads(Path(path).read_text())
        except (FileNotFoundError, json.JSONDecodeError):
            data = {}
        known = {k: data[k] for k in data if k in cls.__dataclass_fields__}
        return cls(**known)


def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class SlackConfig:
    """Optional Slack integration — alerts, digest, and the approval gate.

    Every field is env-sourced and optional; with nothing set the notifier is a
    no-op and the approval gate stays disabled, so the pipeline behaves exactly
    as it did before Slack existed. Mirrors the ``rundown_api_key`` precedent.
    """

    bot_token: str | None = field(default_factory=lambda: os.getenv("SLACK_BOT_TOKEN"))
    app_token: str | None = field(default_factory=lambda: os.getenv("SLACK_APP_TOKEN"))
    webhook_url: str | None = field(default_factory=lambda: os.getenv("SLACK_WEBHOOK_URL"))
    channel: str = field(default_factory=lambda: _env("SLACK_CHANNEL", "#sportsball"))
    require_approval: bool = field(
        default_factory=lambda: _env_bool("SLACK_REQUIRE_APPROVAL", False)
    )
    approval_ev_threshold: float = field(
        default_factory=lambda: float(_env("SLACK_APPROVAL_EV_THRESHOLD", "0.10"))
    )
    approval_ttl_secs: int = field(
        default_factory=lambda: int(_env("SLACK_APPROVAL_TTL_SECS", "900"))
    )

    @staticmethod
    def _real(token: str | None, prefix: str) -> bool:
        return bool(token) and token != f"your_{prefix}_token_here"

    def has_alerts(self) -> bool:
        """True if one-way alerts can be sent (bot token or webhook present)."""
        return self._real(self.bot_token, "slack_bot") or bool(self.webhook_url)

    def has_socket_mode(self) -> bool:
        """True if interactive (Approve/Reject) buttons can round-trip."""
        return self._real(self.bot_token, "slack_bot") and self._real(self.app_token, "slack_app")

    def gate_enabled(self) -> bool:
        """The approval gate only engages with interactivity AND the flag on."""
        return self.require_approval and self.has_socket_mode()


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
    odds_api_key: str | None = field(
        default_factory=lambda: os.getenv("ODDS_API_KEY")
    )
    slack: SlackConfig = field(default_factory=SlackConfig)

    def has_live_rundown_key(self) -> bool:
        key = self.rundown_api_key
        return bool(key) and key != "your_rundown_api_key_here"

    def has_odds_api_key(self) -> bool:
        key = self.odds_api_key
        return bool(key) and key != "your_odds_api_key_here"

    def as_dict(self) -> dict:
        return asdict(self)


def load_settings() -> Settings:
    """Build a fresh :class:`Settings` from the current environment."""
    return Settings()
