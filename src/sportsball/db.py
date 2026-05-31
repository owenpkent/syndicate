"""PostgreSQL access layer.

A thin wrapper over psycopg2 that owns the (previously duplicated 14 times)
connection logic, with automatic reconnect on a dropped/aborted connection so a
transient DB blip doesn't kill a long-running agent.
"""
from __future__ import annotations

from contextlib import contextmanager
from typing import Iterable, Iterator, Optional

import psycopg2

from .config import DBConfig
from .logging_conf import get_logger

log = get_logger("db")


class Database:
    """Lazy, self-healing PostgreSQL connection holder."""

    def __init__(self, config: Optional[DBConfig] = None):
        self._config = config or DBConfig()
        self._conn = None

    # -- connection management ------------------------------------------------
    def connect(self):
        """Open (or reuse) a live connection. Returns None if unreachable."""
        if self._conn is not None and self._conn.closed == 0:
            return self._conn
        try:
            self._conn = psycopg2.connect(**self._config.dsn_kwargs())
            log.info("Connected to PostgreSQL at %s", self._config.host)
        except Exception as exc:  # noqa: BLE001 - want to keep agents alive
            log.warning("Could not connect to PostgreSQL: %s", exc)
            self._conn = None
        return self._conn

    @property
    def available(self) -> bool:
        return self.connect() is not None

    def close(self) -> None:
        if self._conn is not None:
            try:
                self._conn.close()
            finally:
                self._conn = None

    # -- query helpers --------------------------------------------------------
    @contextmanager
    def cursor(self) -> Iterator:
        """Yield a cursor, committing on success and reconnecting on failure."""
        conn = self.connect()
        if conn is None:
            raise ConnectionError("PostgreSQL unavailable")
        try:
            with conn.cursor() as cur:
                yield cur
            conn.commit()
        except Exception:
            conn.rollback()
            # Drop the (possibly aborted) connection so the next call reconnects.
            self.close()
            raise

    def execute(self, sql: str, params: Iterable = ()) -> None:
        with self.cursor() as cur:
            cur.execute(sql, params)

    def executemany(self, sql: str, rows) -> None:
        with self.cursor() as cur:
            cur.executemany(sql, rows)

    def query(self, sql: str, params: Iterable = ()) -> list[tuple]:
        with self.cursor() as cur:
            cur.execute(sql, params)
            return cur.fetchall()

    def query_one(self, sql: str, params: Iterable = ()):
        with self.cursor() as cur:
            cur.execute(sql, params)
            return cur.fetchone()
