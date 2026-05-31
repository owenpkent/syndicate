"""In-memory test doubles for Redis, the DB layer, the broker, and the model.

These let the agent/settlement logic be unit-tested without a running Redis or
PostgreSQL, by injecting fakes into the functions' explicit dependencies.
"""
from __future__ import annotations

from collections import defaultdict


class FakeRedis:
    """Minimal Redis stand-in covering the commands Broker uses."""

    def __init__(self):
        self.lists: dict[str, list] = defaultdict(list)
        self.hashes: dict[str, dict] = defaultdict(dict)

    def ping(self):
        return True

    # lists
    def rpush(self, key, value):
        self.lists[key].append(value)

    def lpop(self, key):
        return self.lists[key].pop(0) if self.lists[key] else None

    def blpop(self, key, timeout=0):
        return (key, self.lists[key].pop(0)) if self.lists[key] else None

    def llen(self, key):
        return len(self.lists[key])

    def lindex(self, key, index):
        try:
            return self.lists[key][index]
        except IndexError:
            return None

    def lrem(self, key, count, value):
        removed = 0
        while value in self.lists[key] and (count == 0 or removed < count):
            self.lists[key].remove(value)
            removed += 1
        return removed

    def brpoplpush(self, src, dst, timeout=0):
        if self.lists[src]:
            value = self.lists[src].pop(0)
            self.lists[dst].append(value)
            return value
        return None

    # hashes
    def hset(self, name, key, value):
        self.hashes[name][key] = str(value)

    def hget(self, name, key):
        return self.hashes[name].get(key)

    def hdel(self, name, key):
        return 1 if self.hashes[name].pop(key, None) is not None else 0

    def hgetall(self, name):
        return dict(self.hashes[name])


class FakeBroker:
    """Records pushes and holds in-memory exposure; same surface as Broker."""

    def __init__(self):
        self.pushed: dict[str, list] = defaultdict(list)
        self._exposure: dict[str, float] = {}
        self.cleared: list[str] = []
        self._pending: dict[str, dict] = {}

    def push(self, queue, payload):
        self.pushed[queue].append(payload)

    def set_exposure(self, market_id, size):
        self._exposure[market_id] = size

    def clear_exposure(self, market_id):
        self._exposure.pop(market_id, None)
        self.cleared.append(market_id)

    def active_trades(self):
        return [{"market_id": k, "size": v} for k, v in self._exposure.items()]

    def total_exposure(self):
        return sum(self._exposure.values())

    # approval gate parity
    def stash_pending(self, approval_id, record):
        self._pending[approval_id] = record

    def get_pending(self, approval_id):
        return self._pending.get(approval_id)

    def pop_pending(self, approval_id):
        return self._pending.pop(approval_id, None)

    def all_pending(self):
        return list(self._pending.values())


class FakeSlackClient:
    """Stand-in for slack_sdk.WebClient: records calls; can simulate failures."""

    def __init__(self, raise_on=None):
        self.raise_on = set(raise_on or [])
        self.posts: list[dict] = []
        self.updates: list[dict] = []
        self._ts = 0

    def chat_postMessage(self, channel, blocks, text):
        if "post" in self.raise_on:
            raise RuntimeError("boom")
        self._ts += 1
        ts = f"{self._ts}.0"
        self.posts.append({"channel": channel, "blocks": blocks, "text": text, "ts": ts})
        return {"ts": ts}

    def chat_update(self, channel, ts, blocks, text):
        if "update" in self.raise_on:
            raise RuntimeError("boom")
        self.updates.append({"channel": channel, "ts": ts, "blocks": blocks, "text": text})
        return {"ts": ts}


class FakeDB:
    """Stand-in for sportsball.db.Database with scripted query results."""

    def __init__(self, available=True, rows=None, one=None):
        self.available = available
        self._rows = rows if rows is not None else []
        self._one = one
        self.executed: list[tuple] = []

    def connect(self):
        return object() if self.available else None

    def execute(self, sql, params=()):
        self.executed.append((sql, params))

    def executemany(self, sql, rows):
        self.executed.append((sql, list(rows)))

    def query(self, sql, params=()):
        return self._rows

    def query_one(self, sql, params=()):
        return self._one


class FakeBundle:
    """Returns a fixed participant probability regardless of inputs."""

    def __init__(self, prob):
        self.prob = prob

    def predict_participant_prob(self, home_team, away_team, participant, **stats):
        return self.prob

    def predict_home_prob(self, home_team, away_team, **stats):
        return self.prob
