# research/kalshi — Kalshi API access

Authenticated access to the [Kalshi](https://kalshi.com) trade API (regulated US
prediction market). **Auth works**; the data access for *research* does not (see below).

## Setup
- Key ID in `.env`: `KALSHI_API_KEY=<uuid>` (gitignored).
- RSA private key at `secrets/kalshi_private_key.pem` (gitignored, mode 600).
- `pip install cryptography` (in the venv).

```python
from kalshi_auth import Kalshi
k = Kalshi()
k.get("/trade-api/v2/portfolio/balance")        # signed request
```

`kalshi_auth.py` signs each request with RSA-PSS/SHA256 over `timestamp+METHOD+path`
(three `KALSHI-ACCESS-*` headers), per the new Kalshi auth scheme.

## Honest status / limitation
- Auth verified against `/portfolio/balance`. Account balance is **$0** — data/research
  only, not funded for live trading.
- **The `/markets` list endpoint returns volume / open_interest / liquidity / last_price
  as null** for both settled and open markets, and the feed is dominated by dormant
  auto-generated markets. So bulk liquid-market discovery — for historical calibration
  or a forward collector — is impractical here without hand-curating specific liquid
  series tickers and per-market calls. Given the broader survey (every liquid market is
  efficient), this wasn't pursued. The auth is kept as reusable infrastructure.
