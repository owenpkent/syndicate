"""Slack integration: config flags, pure blocks, notifier, gate, approver, digest.

Everything here runs with no network and no ``slack_sdk`` import on the default
path — clients are injected fakes (tests/fakes.py: FakeBroker, FakeSlackClient).
"""
from __future__ import annotations

import pytest

from fakes import FakeBroker, FakeDB, FakeSlackClient

from sportsball.agents import approver
from sportsball.broker import EXECUTION_SIGNALS, PENDING_APPROVAL
from sportsball.config import SlackConfig
from sportsball.notify import blocks
from sportsball.notify.blocks import APPROVE_ACTION, REJECT_ACTION
from sportsball.notify.gate import ApprovalGate
from sportsball.notify.slack import NULL_NOTIFIER, Notifier
from sportsball.tools import digest


SIG = {"market_id": "POLY-nba_x-LAL", "event_id": "nba_x", "side": "HOME",
       "home_team": "Lakers", "away_team": "Celtics", "source": "poly",
       "ev": 0.15, "fraction": 0.05, "odds": 1.9}


# -- SlackConfig capability flags -------------------------------------------
class TestSlackConfig:
    def test_unconfigured_is_inert(self):
        cfg = SlackConfig(bot_token=None, app_token=None, webhook_url=None)
        assert not cfg.has_alerts()
        assert not cfg.has_socket_mode()
        assert not cfg.gate_enabled()

    def test_sentinel_placeholders_dont_count(self):
        cfg = SlackConfig(bot_token="your_slack_bot_token_here",
                          app_token="your_slack_app_token_here")
        assert not cfg.has_alerts()
        assert not cfg.has_socket_mode()

    def test_webhook_enables_alerts_only(self):
        cfg = SlackConfig(bot_token=None, app_token=None, webhook_url="https://hook")
        assert cfg.has_alerts()
        assert not cfg.has_socket_mode()

    def test_gate_needs_socket_and_flag(self):
        base = dict(bot_token="xoxb-1", app_token="xapp-1")
        assert SlackConfig(**base, require_approval=True).gate_enabled()
        assert not SlackConfig(**base, require_approval=False).gate_enabled()
        # Flag on but no app token -> still off.
        assert not SlackConfig(bot_token="xoxb-1", require_approval=True).gate_enabled()


# -- Pure block builders -----------------------------------------------------
class TestBlocks:
    def test_builders_return_serializable_lists(self):
        import json
        for b in (blocks.fill_blocks(SIG, 1.88),
                  blocks.settlement_blocks(event_id="e", side="HOME", status="WIN",
                                           pnl=0.5, home_score=110, away_score=99),
                  blocks.health_blocks(False, ["[FAIL] redis"]),
                  blocks.digest_blocks({"realized_pnl": 1.0}),
                  blocks.approval_blocks("aid", SIG),
                  blocks.approval_resolved_blocks(SIG, "APPROVED", "owen")):
            assert isinstance(b, list)
            json.dumps(b)  # must be JSON-serializable

    def test_approval_buttons_carry_id_and_actions(self):
        b = blocks.approval_blocks("aid123", SIG)
        actions = [blk for blk in b if blk["type"] == "actions"][0]["elements"]
        by_action = {e["action_id"]: e["value"] for e in actions}
        assert by_action == {APPROVE_ACTION: "aid123", REJECT_ACTION: "aid123"}


# -- Notifier: no-op, configured, failure isolation --------------------------
class TestNotifier:
    def test_null_notifier_is_disabled(self):
        assert not NULL_NOTIFIER.enabled
        NULL_NOTIFIER.notify_fill(SIG, 1.9)  # must not raise

    def test_unconfigured_does_not_send(self):
        client = FakeSlackClient()
        n = Notifier(SlackConfig(bot_token=None, webhook_url=None), client=client)
        n.notify_fill(SIG, 1.9)
        assert client.posts == []  # disabled -> never touches the client

    def test_configured_posts(self):
        client = FakeSlackClient()
        n = Notifier(SlackConfig(bot_token="xoxb-1", channel="#c"), client=client)
        n.notify_fill(SIG, 1.88)
        n.notify_settlement(event_id="e", side="HOME", status="WIN", pnl=0.5,
                            home_score=110, away_score=99)
        assert len(client.posts) == 2
        assert client.posts[0]["channel"] == "#c"

    def test_send_failure_is_swallowed(self):
        client = FakeSlackClient(raise_on={"post"})
        n = Notifier(SlackConfig(bot_token="xoxb-1"), client=client)
        n.notify_fill(SIG, 1.9)  # must not raise into the caller

    def test_post_approval_requires_interactivity(self):
        # Bot token but no app token -> not interactive -> no post, no ts.
        client = FakeSlackClient()
        n = Notifier(SlackConfig(bot_token="xoxb-1"), client=client)
        assert n.post_approval("aid", SIG) is None
        assert client.posts == []

    def test_resolve_approval_updates_message(self):
        client = FakeSlackClient()
        n = Notifier(SlackConfig(bot_token="xoxb-1", app_token="xapp-1"), client=client)
        ts = n.post_approval("aid", SIG)
        n.resolve_approval(ts, SIG, "APPROVED", "owen")
        assert len(client.updates) == 1
        assert client.updates[0]["ts"] == ts


# -- ApprovalGate routing ----------------------------------------------------
class TestGate:
    def _gate(self, **cfg):
        broker = FakeBroker()
        c = SlackConfig(bot_token="xoxb-1", app_token="xapp-1",
                        approval_ev_threshold=0.10, **cfg)
        return ApprovalGate(broker, c, id_factory=lambda: "fixed-id"), broker

    def test_should_gate_threshold(self):
        gate, _ = self._gate(require_approval=True)
        assert gate.should_gate(0.20)
        assert not gate.should_gate(0.05)

    def test_disabled_when_flag_off(self):
        gate, _ = self._gate(require_approval=False)
        assert not gate.should_gate(0.99)

    def test_enqueue_stashes_and_queues(self):
        gate, broker = self._gate(require_approval=True)
        aid = gate.enqueue(SIG)
        assert aid == "fixed-id"
        assert broker.get_pending("fixed-id")["signal"] == SIG
        assert broker.pushed[PENDING_APPROVAL] == [{"approval_id": "fixed-id"}]


# -- handle_action / reaper (transport-free) ---------------------------------
def _payload(action_id, approval_id, user="owen"):
    return {"actions": [{"action_id": action_id, "value": approval_id}],
            "user": {"username": user}}


class TestApprover:
    def _stash(self, broker, aid="a1"):
        broker.stash_pending(aid, {"approval_id": aid, "signal": SIG, "message_ts": "1.0"})

    def test_approve_forwards_to_execution(self):
        broker = FakeBroker(); self._stash(broker)
        client = FakeSlackClient()
        n = Notifier(SlackConfig(bot_token="xoxb-1", app_token="xapp-1"), client=client)
        approver.handle_action(_payload(APPROVE_ACTION, "a1"), broker=broker, notifier=n)
        assert broker.pushed[EXECUTION_SIGNALS] == [SIG]
        assert broker.get_pending("a1") is None  # claimed
        assert client.updates[0]["text"].startswith("APPROVED")

    def test_reject_does_not_forward(self):
        broker = FakeBroker(); self._stash(broker)
        approver.handle_action(_payload(REJECT_ACTION, "a1"), broker=broker, notifier=NULL_NOTIFIER)
        assert broker.pushed[EXECUTION_SIGNALS] == []
        assert broker.get_pending("a1") is None

    def test_double_click_is_idempotent(self):
        broker = FakeBroker(); self._stash(broker)
        approver.handle_action(_payload(APPROVE_ACTION, "a1"), broker=broker, notifier=NULL_NOTIFIER)
        approver.handle_action(_payload(APPROVE_ACTION, "a1"), broker=broker, notifier=NULL_NOTIFIER)
        assert broker.pushed[EXECUTION_SIGNALS] == [SIG]  # only once

    def test_unknown_action_ignored(self):
        broker = FakeBroker(); self._stash(broker)
        approver.handle_action(_payload("something_else", "a1"), broker=broker, notifier=NULL_NOTIFIER)
        assert broker.pushed[EXECUTION_SIGNALS] == []
        assert broker.get_pending("a1") is not None  # untouched

    def test_reap_expired_auto_rejects(self):
        broker = FakeBroker()
        broker.stash_pending("a1", {"approval_id": "a1", "signal": SIG,
                                    "message_ts": "1.0", "deadline": 100.0})
        broker.stash_pending("a2", {"approval_id": "a2", "signal": SIG,
                                    "message_ts": "2.0", "deadline": 500.0})
        n = approver.reap_expired(broker, NULL_NOTIFIER, now=200.0)
        assert n == 1
        assert broker.get_pending("a1") is None      # expired -> claimed
        assert broker.get_pending("a2") is not None   # still pending
        assert broker.pushed[EXECUTION_SIGNALS] == []  # expiry never trades


# -- Digest aggregation ------------------------------------------------------
class TestDigest:
    def test_build_summary_pulls_counts_and_exposure(self):
        db = FakeDB(available=True, one=(2.5, 3, 4, 5))  # pnl, settled, trades, signals
        from sportsball.store import Store
        store = Store(db)
        broker = FakeBroker()
        broker.set_exposure("m1", 0.05)
        s = digest.build_summary(store, broker, now=10_000.0, model_file="/nope/missing.pkl")
        assert s.realized_pnl == 2.5
        assert (s.settled, s.trades, s.signals) == (3, 4, 5)
        assert s.open_exposure == pytest.approx(0.05)
        assert s.model_age == "no model"

    def test_build_summary_unavailable_db_is_zeroed(self):
        from sportsball.store import Store
        s = digest.build_summary(Store(FakeDB(available=False)), FakeBroker(), now=0.0)
        assert s.realized_pnl == 0.0 and s.trades == 0
