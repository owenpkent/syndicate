"""Canonical event identity — the bridge that lets different venues agree.

The Rundown, nba_api, and Polymarket all refer to the same game with different
ids and team-name spellings. To settle and (especially) to detect *cross-venue*
arbitrage, signals from different sources must collapse onto one ``event_id``.

We derive a deterministic, venue-independent id from ``(sport, date, away, home)``
by normalizing each team to a canonical mascot token. This is **best-effort**:
within a single source it's exact; across sources it depends on the alias table
below covering the spellings each venue uses. Unmapped multi-word names fall
back to their last token, which is correct for most US-sports mascots but not
all — extend ALIASES / TWO_WORD_MASCOTS as gaps appear.
"""
from __future__ import annotations

import re
from datetime import date, datetime

# Mascots that are two words (so "last token" would truncate them).
TWO_WORD_MASCOTS = {
    "trail blazers", "red sox", "white sox", "blue jays", "maple leafs",
    "golden knights", "red wings",
}

# Explicit spelling fixes where normalization can't recover the canonical token.
ALIASES = {
    "la clippers": "clippers", "los angeles clippers": "clippers",
    "la lakers": "lakers", "los angeles lakers": "lakers",
}


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]", "", text.lower())


def normalize_team(name: str) -> str:
    """Reduce a team name to a canonical token (usually its mascot).

    'Los Angeles Lakers' -> 'lakers'; 'Portland Trail Blazers' -> 'trailblazers';
    'Lakers' -> 'lakers'. Lossy on purpose so different venues collapse together.
    """
    cleaned = re.sub(r"\s+", " ", (name or "").strip().lower())
    if cleaned in ALIASES:
        return ALIASES[cleaned]
    for mascot in TWO_WORD_MASCOTS:
        if cleaned.endswith(mascot):
            return _slug(mascot)
    tokens = cleaned.split()
    return _slug(tokens[-1]) if tokens else ""


def _date_str(d) -> str:
    if isinstance(d, (datetime, date)):
        return d.strftime("%Y%m%d")
    # Accept "YYYY-MM-DD" or "YYYY-MM-DDTHH:MM:SSZ" style strings.
    return re.sub(r"[^0-9]", "", str(d))[:8]


def canonical_event_id(sport: str, when, away_team: str, home_team: str) -> str:
    """Deterministic id shared by any venue describing the same game.

    Contains no ``-`` so it is safe as the EVENTID segment of a
    ``SOURCE-EVENTID-PARTICIPANT`` market_id. Example:
    ``nba_20240115_lakers_at_celtics``.
    """
    return f"{_slug(sport)}_{_date_str(when)}_{normalize_team(away_team)}_at_{normalize_team(home_team)}"
