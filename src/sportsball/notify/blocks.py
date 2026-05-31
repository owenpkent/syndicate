"""Pure Slack Block Kit builders.

Every function here returns a JSON-serializable ``list[dict]`` (Slack "blocks")
and performs **no** I/O — no ``slack_sdk`` import, no network. That keeps message
formatting fully unit-testable and decoupled from how it's delivered. The
:class:`~sportsball.notify.slack.Notifier` is responsible for sending them.

Action ids on the approval buttons are the contract with the approver agent:
``APPROVE_ACTION`` / ``REJECT_ACTION`` carry the ``approval_id`` in their value.
"""
from __future__ import annotations

APPROVE_ACTION = "sportsball_approve"
REJECT_ACTION = "sportsball_reject"


def _section(text: str) -> dict:
    return {"type": "section", "text": {"type": "mrkdwn", "text": text}}


def _context(text: str) -> dict:
    return {"type": "context", "elements": [{"type": "mrkdwn", "text": text}]}


def _matchup(sig: dict) -> str:
    away, home = sig.get("away_team", "?"), sig.get("home_team", "?")
    return f"{away} @ {home}"


def fill_blocks(sig: dict, executed_odds: float) -> list[dict]:
    """A paper fill confirmation (Sniper)."""
    side = sig.get("side", "?")
    return [
        _section(f":dart: *Paper fill* — `{sig.get('market_id', '?')}`"),
        _section(
            f"*{_matchup(sig)}*\n"
            f"Side: *{side}*  |  Odds: *{sig.get('odds', '?')}* → filled *{executed_odds}*\n"
            f"Stake: *{float(sig.get('fraction', 0)):.4f}*  |  EV: *{float(sig.get('ev', 0)):.4f}*"
        ),
        _context(f"source: {sig.get('source', 'n/a')}  ·  event: {sig.get('event_id', 'n/a')}"),
    ]


def settlement_blocks(*, event_id: str, side: str, status: str, pnl: float,
                      home_score: int, away_score: int) -> list[dict]:
    """A graded trade outcome (Settlement)."""
    icon = ":large_green_circle:" if status == "WIN" else ":red_circle:"
    sign = "+" if pnl >= 0 else ""
    return [
        _section(f"{icon} *Settled {status}* — `{event_id}`"),
        _section(
            f"Side: *{side}*  |  Final: *{home_score}–{away_score}*\n"
            f"PnL: *{sign}{pnl:.4f}* (stake-fraction units)"
        ),
    ]


def health_blocks(healthy: bool, lines: list[str]) -> list[dict]:
    """A health report; only sent on degradation in practice."""
    header = ":white_check_mark: *Sportsball healthy*" if healthy else ":rotating_light: *Sportsball DEGRADED*"
    detail = "\n".join(lines[:40]) or "(no detail)"
    return [_section(header), _section(f"```{detail}```")]


def digest_blocks(summary: dict) -> list[dict]:
    """The scheduled performance digest. ``summary`` is DigestSummary.as_dict()."""
    pnl = float(summary.get("realized_pnl", 0.0))
    sign = "+" if pnl >= 0 else ""
    return [
        _section(":bar_chart: *Daily digest* — last 24h"),
        _section(
            f"Realized PnL: *{sign}{pnl:.4f}*\n"
            f"Trades: *{summary.get('trades', 0)}*  (settled *{summary.get('settled', 0)}*)  |  "
            f"Signals: *{summary.get('signals', 0)}*\n"
            f"Open exposure: *{float(summary.get('open_exposure', 0.0)):.4f}*"
        ),
        _context(f"model: {summary.get('model_age', 'unknown')}"),
    ]


def approval_blocks(approval_id: str, sig: dict) -> list[dict]:
    """An actionable suggestion with Approve / Reject buttons."""
    return [
        _section(
            f":mag: *Trade suggestion* — `{sig.get('market_id', '?')}`\n"
            f"*{_matchup(sig)}*  ·  Side: *{sig.get('side', '?')}*\n"
            f"EV: *{float(sig.get('ev', 0)):.4f}*  |  Odds: *{sig.get('odds', '?')}*  |  "
            f"Size: *{float(sig.get('fraction', 0)):.4f}*"
        ),
        {
            "type": "actions",
            "block_id": f"approval:{approval_id}",
            "elements": [
                {
                    "type": "button", "style": "primary",
                    "text": {"type": "plain_text", "text": "Approve"},
                    "action_id": APPROVE_ACTION, "value": approval_id,
                },
                {
                    "type": "button", "style": "danger",
                    "text": {"type": "plain_text", "text": "Reject"},
                    "action_id": REJECT_ACTION, "value": approval_id,
                },
            ],
        },
    ]


def approval_resolved_blocks(sig: dict, decision: str, who: str | None = None) -> list[dict]:
    """Replacement blocks for a suggestion once decided (removes the buttons)."""
    icons = {"APPROVED": ":white_check_mark:", "REJECTED": ":no_entry:", "EXPIRED": ":hourglass:"}
    icon = icons.get(decision, ":grey_question:")
    by = f" by *{who}*" if who else ""
    return [
        _section(
            f"{icon} *{decision}*{by} — `{sig.get('market_id', '?')}`\n"
            f"*{_matchup(sig)}*  ·  Side: *{sig.get('side', '?')}*  ·  EV: *{float(sig.get('ev', 0)):.4f}*"
        ),
    ]
