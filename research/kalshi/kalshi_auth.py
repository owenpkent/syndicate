"""Signed-request auth for the Kalshi trade API (RSA-PSS/SHA256).

The new Kalshi API authenticates each request with three headers: the key ID, a
millisecond timestamp, and an RSA-PSS signature over ``timestamp+METHOD+path`` (path
without query string). Key ID from ``.env`` (KALSHI_API_KEY); private key from the
gitignored ``secrets/kalshi_private_key.pem``. Market-data endpoints are public, but
signing unlocks volume/portfolio and higher limits.
"""
from __future__ import annotations

import base64
import time
from pathlib import Path

import requests
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding

REPO = Path(__file__).resolve().parent.parent.parent
HOST = "https://api.elections.kalshi.com"
_PEM = REPO / "secrets" / "kalshi_private_key.pem"


def _key_id() -> str:
    for line in open(REPO / ".env"):
        if line.startswith("KALSHI_API_KEY="):
            return line.split("=", 1)[1].strip()
    raise RuntimeError("KALSHI_API_KEY not in .env")


def _private_key():
    return serialization.load_pem_private_key(_PEM.read_bytes(), password=None)


def _headers(private_key, key_id: str, method: str, path: str) -> dict:
    ts = str(int(time.time() * 1000))
    sig = private_key.sign(
        (ts + method + path).encode(),
        padding.PSS(mgf=padding.MGF1(hashes.SHA256()), salt_length=padding.PSS.DIGEST_LENGTH),
        hashes.SHA256())
    return {"KALSHI-ACCESS-KEY": key_id, "KALSHI-ACCESS-TIMESTAMP": ts,
            "KALSHI-ACCESS-SIGNATURE": base64.b64encode(sig).decode(),
            "User-Agent": "sportsball-kalshi/0.1"}


class Kalshi:
    def __init__(self):
        self.key_id = _key_id()
        self.private_key = _private_key()

    def get(self, path: str, params: dict | None = None):
        """``path`` like ``/trade-api/v2/markets`` (query is NOT part of the signature)."""
        r = requests.get(HOST + path, params=params,
                         headers=_headers(self.private_key, self.key_id, "GET", path), timeout=25)
        r.raise_for_status()
        return r.json()


if __name__ == "__main__":
    k = Kalshi()
    print("auth OK ->", k.get("/trade-api/v2/portfolio/balance"))
