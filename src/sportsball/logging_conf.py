"""Uniform logging setup for every agent and tool.

The old code repeated ``logging.basicConfig(...)`` (with slightly different
formats) in nearly every file, and some modules used bare ``print``. This gives
one consistent, timestamped, level-aware logger keyed by component name.
"""
from __future__ import annotations

import logging
import os

_CONFIGURED = False
_FORMAT = "%(asctime)s | %(levelname)-7s | %(name)s | %(message)s"


def get_logger(name: str) -> logging.Logger:
    """Return a configured logger for ``name`` (idempotent across calls)."""
    global _CONFIGURED
    if not _CONFIGURED:
        level = os.getenv("LOG_LEVEL", "INFO").upper()
        logging.basicConfig(level=level, format=_FORMAT)
        _CONFIGURED = True
    return logging.getLogger(name)
