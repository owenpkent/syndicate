"""Approver Agent — the human-in-the-loop gate.

When the approval gate is enabled, the Engine stashes high-EV signals instead of
firing them. This agent turns those into actionable Slack messages and forwards
only the approved ones to the Sniper. It runs three cooperating concerns that
share state **only through Redis** (the pending hash), so no in-process lock is
needed:

1. **Poster loop** — drains the ``pending_approval`` work list, posts an
   Approve/Reject message, and records the message ts + deadline.
2. **Socket Mode handler** — receives button clicks over an outbound WebSocket
   (no public endpoint) and resolves the decision via :func:`handle_action`.
3. **TTL reaper** — auto-rejects (EXPIRED) suggestions nobody acted on, so a
   missed click never silently trades and never leaks a pending row.

Idempotency for double-clicks / poster-vs-reaper races is handled by
``broker.pop_pending`` (only one caller's HDEL wins).
"""
from __future__ import annotations

import threading
import time

from ..broker import Broker, EXECUTION_SIGNALS, PENDING_APPROVAL
from ..config import Settings, load_settings
from ..logging_conf import get_logger
from ..notify import Notifier, build_notifier
from ..notify.blocks import APPROVE_ACTION, REJECT_ACTION

log = get_logger("approver")

INFLIGHT = "pending_approval:inflight"


def handle_action(payload: dict, *, broker, notifier: Notifier) -> None:
    """Resolve one Slack ``block_actions`` payload. Transport-agnostic (testable).

    Pops the pending record (idempotent); on Approve forwards the original
    signal to ``EXECUTION_SIGNALS``; either way edits the Slack message to show
    the outcome. A no-longer-pending id (double click, already expired) no-ops.
    """
    actions = payload.get("actions") or []
    if not actions:
        return
    action = actions[0]
    action_id = action.get("action_id")
    approval_id = action.get("value")
    if action_id not in (APPROVE_ACTION, REJECT_ACTION) or not approval_id:
        return

    record = broker.pop_pending(approval_id)
    if record is None:
        log.info("Approval %s already resolved; ignoring.", approval_id)
        return

    sig = record.get("signal", {})
    ts = record.get("message_ts")
    who = (payload.get("user") or {}).get("username") or (payload.get("user") or {}).get("name")

    if action_id == APPROVE_ACTION:
        broker.push(EXECUTION_SIGNALS, sig)
        log.info("[APPROVED] %s by %s -> execution", sig.get("market_id"), who)
        notifier.resolve_approval(ts, sig, "APPROVED", who)
    else:
        log.info("[REJECTED] %s by %s", sig.get("market_id"), who)
        notifier.resolve_approval(ts, sig, "REJECTED", who)


def post_loop(broker: Broker, notifier: Notifier, ttl_secs: int) -> None:
    """Post each freshly-queued suggestion and record its ts + deadline."""
    for raw, item in broker.reliable_consume(PENDING_APPROVAL, INFLIGHT):
        try:
            approval_id = item.get("approval_id")
            record = broker.get_pending(approval_id)
            if record is None:
                continue  # already resolved before we could post
            ts = notifier.post_approval(approval_id, record["signal"])
            record["message_ts"] = ts
            record["deadline"] = time.time() + ttl_secs
            broker.stash_pending(approval_id, record)
            log.info("Posted suggestion %s (ts=%s)", approval_id, ts)
        except Exception as exc:  # noqa: BLE001 - never let one bad item kill the loop
            log.error("Approver post error: %s", exc)
        finally:
            broker.ack(INFLIGHT, raw)


def reap_expired(broker: Broker, notifier: Notifier, now: float) -> int:
    """Auto-reject suggestions past their deadline. Returns the count expired."""
    expired = 0
    for record in broker.all_pending():
        deadline = record.get("deadline")
        if deadline is None or now < deadline:
            continue
        claimed = broker.pop_pending(record["approval_id"])
        if claimed is None:
            continue  # someone else resolved it first
        sig = claimed.get("signal", {})
        log.info("[EXPIRED] %s auto-rejected", sig.get("market_id"))
        notifier.resolve_approval(claimed.get("message_ts"), sig, "EXPIRED")
        expired += 1
    return expired


def _reaper_thread(broker: Broker, notifier: Notifier, interval: int) -> None:
    while True:
        time.sleep(interval)
        try:
            reap_expired(broker, notifier, time.time())
        except Exception as exc:  # noqa: BLE001
            log.error("Reaper error: %s", exc)


def run(settings: Settings) -> None:
    if not settings.slack.has_socket_mode():
        log.error("Approver needs SLACK_BOT_TOKEN + SLACK_APP_TOKEN (Socket Mode). Exiting.")
        return

    from slack_sdk import WebClient
    from slack_sdk.socket_mode import SocketModeClient
    from slack_sdk.socket_mode.request import SocketModeRequest
    from slack_sdk.socket_mode.response import SocketModeResponse

    broker = Broker(settings.redis)
    notifier = build_notifier(settings)
    ttl = settings.slack.approval_ttl_secs

    socket_client = SocketModeClient(
        app_token=settings.slack.app_token,
        web_client=WebClient(token=settings.slack.bot_token),
    )

    def _on_request(client: SocketModeClient, req: SocketModeRequest) -> None:
        client.send_socket_mode_response(SocketModeResponse(envelope_id=req.envelope_id))
        if req.type == "interactive":
            try:
                handle_action(req.payload, broker=broker, notifier=notifier)
            except Exception as exc:  # noqa: BLE001
                log.error("Approver action error: %s", exc)

    socket_client.socket_mode_request_listeners.append(_on_request)
    socket_client.connect()
    log.info("Approver connected (Socket Mode); TTL=%ds", ttl)

    threading.Thread(target=_reaper_thread, args=(broker, notifier, min(ttl, 60)),
                     daemon=True).start()
    post_loop(broker, notifier, ttl)


def main() -> None:
    log.info("Approver Agent (the gatekeeper) starting...")
    run(load_settings())


if __name__ == "__main__":
    main()
