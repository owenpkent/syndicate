"""Slack integration: alerts, the daily digest, and the approval gate.

Layering mirrors the rest of the package — the inner pieces stay pure so the
unit suite can exercise them without a network or a Slack workspace:

* ``blocks``  — pure Block Kit message builders (no ``slack_sdk`` import)
* ``slack``   — the ``Notifier`` (sends; no-op + error-isolating when unconfigured)
* ``gate``    — ``ApprovalGate`` routing logic (no network; Redis only)

The agent that drives Socket Mode lives at ``sportsball.agents.approver``.
"""
from .slack import NULL_NOTIFIER, Notifier, build_notifier

__all__ = ["Notifier", "NULL_NOTIFIER", "build_notifier"]
