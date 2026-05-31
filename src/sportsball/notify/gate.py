"""Approval-gate routing — pure of network, Redis only.

When the gate is enabled and a signal's EV clears the threshold, the Engine
enqueues it here (a pending hash + a work list) instead of pushing straight to
``EXECUTION_SIGNALS``. The ``sportsball-approver`` agent then posts it to Slack
and, on Approve, forwards the original signal unchanged to ``EXECUTION_SIGNALS``.

``uuid4`` is the one impurity; it's injectable so tests get deterministic ids.
"""
from __future__ import annotations

import uuid
from typing import Callable

from ..broker import PENDING_APPROVAL
from ..config import SlackConfig


class ApprovalGate:
    def __init__(self, broker, cfg: SlackConfig,
                 id_factory: Callable[[], str] = lambda: uuid.uuid4().hex):
        self._broker = broker
        self._cfg = cfg
        self._id = id_factory

    def should_gate(self, ev: float) -> bool:
        """Route to human approval only when interactivity is on AND EV is high."""
        return self._cfg.gate_enabled() and ev >= self._cfg.approval_ev_threshold

    def enqueue(self, exec_signal: dict) -> str:
        """Stash the signal under a new approval_id and queue it for posting."""
        approval_id = self._id()
        self._broker.stash_pending(approval_id, {
            "approval_id": approval_id,
            "signal": exec_signal,
            "status": "PENDING",
            "message_ts": None,
        })
        self._broker.push(PENDING_APPROVAL, {"approval_id": approval_id})
        return approval_id
