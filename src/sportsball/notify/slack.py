"""The Slack ``Notifier`` — the only place that talks to Slack's network.

Design rules (all load-bearing):

* **No-op when unconfigured.** With no Slack env vars, ``enabled`` is False and
  every ``notify_*`` returns immediately. The pipeline runs exactly as before.
* **Never raises into a caller.** All network calls are wrapped; failures are
  logged (error string only, never the token) and swallowed. A Slack outage
  must never break the Sniper, Settlement, or Engine.
* **Lazy import.** ``slack_sdk`` is imported inside the client builder, so an
  unconfigured run (and the whole test suite) needs neither the package nor a
  connection.

Message *formatting* lives in :mod:`sportsball.notify.blocks` (pure); this
module only decides whether and how to send.
"""
from __future__ import annotations

from typing import Optional

from ..config import SlackConfig
from ..logging_conf import get_logger
from . import blocks

log = get_logger("notify")


class Notifier:
    """Sends Slack messages, or does nothing if Slack isn't configured.

    ``client`` may be injected (a real ``slack_sdk.WebClient`` or a fake) for
    testing; otherwise it is built lazily from ``cfg.bot_token`` on first use.
    """

    def __init__(self, cfg: Optional[SlackConfig], client=None) -> None:
        self._cfg = cfg
        self._client = client
        self._client_built = client is not None

    # -- capability flags ----------------------------------------------------
    @property
    def enabled(self) -> bool:
        return self._cfg is not None and self._cfg.has_alerts()

    @property
    def interactive(self) -> bool:
        """Can we post/update interactive messages (needs a bot token)?"""
        return self._cfg is not None and self._cfg.has_socket_mode()

    def _get_client(self):
        if not self._client_built:
            self._client_built = True
            token = self._cfg.bot_token if self._cfg else None
            if token:
                try:
                    from slack_sdk import WebClient  # lazy: optional at runtime
                    self._client = WebClient(token=token)
                except Exception as e:  # noqa: BLE001
                    log.warning("Slack client init failed: %s", e)
                    self._client = None
        return self._client

    # -- low-level send ------------------------------------------------------
    def _post(self, block_list: list[dict], text: str) -> Optional[str]:
        """Post a message; returns the Slack message ts, or None. Never raises."""
        if not self.enabled:
            return None
        client = self._get_client()
        try:
            if client is not None:
                resp = client.chat_postMessage(
                    channel=self._cfg.channel, blocks=block_list, text=text)
                return resp.get("ts")
            if self._cfg.webhook_url:
                import requests  # already a core dependency
                requests.post(self._cfg.webhook_url,
                              json={"blocks": block_list, "text": text}, timeout=10)
        except Exception as e:  # noqa: BLE001 - isolate Slack failures from agents
            log.warning("Slack send failed: %s", e)
        return None

    def update_message(self, ts: str, block_list: list[dict], text: str) -> None:
        """Edit a previously-posted message (Approve/Reject resolution)."""
        if not self.interactive or not ts:
            return
        client = self._get_client()
        if client is None:
            return
        try:
            client.chat_update(channel=self._cfg.channel, ts=ts,
                               blocks=block_list, text=text)
        except Exception as e:  # noqa: BLE001
            log.warning("Slack update failed: %s", e)

    # -- public, semantic API ------------------------------------------------
    def notify_fill(self, sig: dict, executed_odds: float) -> None:
        self._post(blocks.fill_blocks(sig, executed_odds),
                   f"Paper fill {sig.get('market_id', '')}")

    def notify_settlement(self, *, event_id: str, side: str, status: str, pnl: float,
                          home_score: int, away_score: int) -> None:
        self._post(
            blocks.settlement_blocks(event_id=event_id, side=side, status=status,
                                     pnl=pnl, home_score=home_score, away_score=away_score),
            f"Settled {status} {event_id} pnl={pnl:.4f}")

    def notify_health(self, healthy: bool, lines: list[str]) -> None:
        self._post(blocks.health_blocks(healthy, lines),
                   "Sportsball health: " + ("OK" if healthy else "DEGRADED"))

    def notify_digest(self, summary: dict) -> None:
        self._post(blocks.digest_blocks(summary), "Sportsball daily digest")

    def post_approval(self, approval_id: str, sig: dict) -> Optional[str]:
        """Post an interactive suggestion; returns its message ts (for later edit)."""
        if not self.interactive:
            return None
        return self._post(blocks.approval_blocks(approval_id, sig),
                          f"Trade suggestion {sig.get('market_id', '')}")

    def resolve_approval(self, ts: str, sig: dict, decision: str,
                         who: str | None = None) -> None:
        self.update_message(ts, blocks.approval_resolved_blocks(sig, decision, who),
                            f"{decision} {sig.get('market_id', '')}")


def build_notifier(settings) -> Notifier:
    """Construct a :class:`Notifier` from a :class:`~sportsball.config.Settings`."""
    return Notifier(settings.slack)


# A shared, always-disabled notifier for default arguments in agent hot paths.
NULL_NOTIFIER = Notifier(None)
