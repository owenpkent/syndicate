# A Practical Guide to Sports Betting (for the Sportsball operator)

You built this system but have never placed a bet. This guide connects the
fundamentals to what the code already does and what our own data says. It is
honest: **most bettors lose, and our own analysis found no edge that survives to
today.** Read the last section before you risk a dollar.

---

## 1. The one-sentence mental model

A sportsbook is a market maker. It quotes a price on each outcome, and the price
includes a built-in margin (**the vig / "juice"**) so that if bettors split
evenly, the book profits regardless of who wins. **Betting profitably means
finding prices that are wrong by *more than the vig* — and then getting your bet
down before the price corrects.** That is hard, because the closing price is
usually right.

Everything below is detail on that sentence.

---

## 2. Odds formats (and why we use decimal)

The same price, three ways:

| Format | Example (favourite) | Example (underdog) | Read it as |
|---|---|---|---|
| **Decimal** | 1.61 | 2.45 | total return per 1 staked (incl. stake) |
| **American** | −164 | +145 | −X: stake to win 100; +X: win on a 100 stake |
| **Implied prob** | 1/1.61 = 62.1% | 1/2.45 = 40.8% | the book's quoted chance |

We use **decimal** everywhere (`quant/odds.py`) because the math is clean:
`profit = (decimal − 1) × stake`, and `implied_prob = 1 / decimal`. Note the two
implied probs sum to **102.9%**, not 100% — that extra **2.9%** (typically 3–5%
on an NBA moneyline) is the vig.

---

## 3. Vig, no-vig, and "fair" probability

Add the two implied probs: a typical NBA moneyline sums to ~**102–105%**. The
excess over 100% is the **hold** the book keeps. To recover the market's honest
estimate, **de-vig**: divide each side's implied prob by the total.

```
home 1.61 -> 0.621 |  away 2.45 -> 0.408  | sum 1.029 (2.9% vig)
fair_home = 0.621 / 1.029 = 60.3%   (vs the 62.1% the price implies)
```

This "no-vig fair probability" is the single most useful number in betting. Your
code computes it (`quant/odds.devig...`), the model consumes it as the
`market_logit` feature, and our edge research used the *consensus* no-vig prob as
the benchmark of truth.

---

## 4. Expected value (EV) — the only reason to place a bet

A bet is good only if your estimate of the true probability beats the price:

```
EV per 1 staked = p_true × (decimal − 1) − (1 − p_true)
               = p_true × decimal − 1
```

If `EV > 0`, the price is in your favour. The **Engine** does exactly this: it
models `p_true`, prices EV against the offered line, and only forwards positive-EV
signals. **The hard part is `p_true`.** If your `p_true` is just the market's own
no-vig prob, your EV is negative by the vig — you can't bet your way out of the
juice with the market's own opinion.

---

## 5. The closing line is the boss — CLV is your report card

The **closing line** (the final price right before tip-off) is the sharpest
number in the market — it has absorbed all the money and information. Decades of
evidence: if you can't beat the closing line, you don't have an edge.

**Closing Line Value (CLV)** = did you get a better price than the close?
- Bet home at 2.10, it closes at 1.90 → you have **positive CLV** (you got a
  better price than the sharpest number). Over many bets, positive CLV ⇒ profit.
- CLV is your edge gate because it's significant in ~tens of bets, while raw P&L
  needs thousands. `make clv` / `tools/clv.py` reports it.

**Our result:** the v4 model's CLV is **−1.67%** — it does *not* beat the close.
That's the honest verdict: a good *predictor* (65.9% accuracy) but not a
*market-beater*. This is normal. Beating a liquid market is genuinely hard.

---

## 6. Where edges actually come from (and what we found)

Realistic edges, roughly easiest → hardest, with our findings:

1. **Line-shopping** — different books price the same game differently; always
   take the best available. *Mechanically real but small:* in our data it mostly
   just **erases the vig → ~breakeven**. Necessary, not sufficient.
2. **Beating soft/recreational books** — books like MyBookie/BetUS/Circa are
   slower and looser than FanDuel/BetMGM. Betting them when they're off the sharp
   consensus *was* **+10–15% in 2022–2024** in our data — but it **decayed to
   −19% in 2025**. Edges die.
3. **Specific markets** — sides (moneyline/spread) are the most efficient.
   **Totals (over/under), player props, and second-half markets** are softer. We
   pulled 124k totals quotes and **haven't analysed them yet** — the most
   promising unexplored lever.
4. **Information speed** — reacting to **injury / lineup news** before the line
   moves. Genuinely exploitable but operationally demanding.
5. **Genuine predictive modelling** — beating the close with a model. The holy
   grail, and the thing our model does *not* currently do.

The pattern: edges exist, they live in the **soft corners of the market**, and
**they decay** — so a live edge needs continuous monitoring, not a frozen
backtest.

---

## 7. Bankroll & staking — how you survive long enough to win

Even with an edge, bet too big and variance ruins you. Two rules:

- **Bankroll:** money you can lose entirely without affecting your life. Bets are
  a % of *it*, not of your net worth.
- **Kelly criterion:** the mathematically optimal stake given your edge:
  `f* = edge / odds = (p_true × decimal − 1) / (decimal − 1)`. Full Kelly is too
  swingy in practice, so everyone bets a **fraction** (¼–½ Kelly). Your system
  uses **fractional Kelly** and *shrinks it further by model uncertainty*
  (`strategy.uncertainty_scaling`) — a conservative, correct instinct.
- A practical floor: never stake more than **1–2%** of bankroll on one bet, edge
  or not.

---

## 8. The operational reality nobody mentions

- **Sharp vs soft books.** Pinnacle/Circa *welcome* winners and move on your
  action (low margin, high limits). FanDuel/DraftKings/etc. **limit or ban**
  winning accounts fast — sometimes to pennies. A real edge program is mostly a
  logistics problem: many accounts, getting bets down at soft books before they
  move, line-shopping across all of them.
- **Limits.** That juicy soft-book price often comes with a **small max bet** —
  one reason our "best price" backtest overstates the edge (you can't always get
  filled at the outlier quote).
- **Closing line, not opening.** You want to bet *into* soft numbers early and
  let the market move toward you (positive CLV), or grab a stale line late.
- **Records.** Track every bet (price taken, closing price, result) so you can
  compute your own CLV. CLV tells you if you're good *long before* the money does.

---

## 9. How your system maps to all this

| Concept | In Sportsball |
|---|---|
| De-vig / fair prob | `quant/odds.py`, the `market_logit` feature |
| EV pricing | the **Engine** (`agents/engine`) |
| Fractional Kelly + uncertainty shrink | `strategy` config, Engine sizing |
| Closing Line Value | `tools/clv.py` / `make clv` |
| Line-shopping data | DuckDB `odds_quotes` (per-book h2h + totals) |
| Paper trading (no real money) | `EXECUTION_MODE=PAPER` — **stays paper** |
| Edge findings | `docs/ROADMAP.md`, the memory `edge-research-findings` |

The system is, in effect, a **disciplined practice of everything in this guide** —
which is exactly why it's valuable even though it hasn't found a live edge: it
*measures* honestly instead of guessing.

---

## 10. If you actually want to start (a sane first path)

You have the rare advantage of an analytical temperament and tooling. A measured
on-ramp:

1. **Paper-trade first.** Keep `EXECUTION_MODE=PAPER`. Log model picks *and* the
   closing line for a full season. **Only your CLV matters** at this stage — if
   it's not positive on paper, real money won't help.
2. **Open 2–3 legal books** in your jurisdiction (plus a sharp reference like
   Pinnacle/Circa for the "true" line if available). Fund only a bankroll you can
   lose.
3. **Line-shop every bet** across them — that alone is worth ~the vig.
4. **Start with totals**, where the market is softer and we haven't even looked
   yet — and bet **tiny** (≤1% bankroll) while you learn the mechanics.
5. **Re-derive your edge live.** Backtests decay; wire the free daily capture to
   watch which books are currently soft before trusting any signal.

---

## 11. Honest warnings (read this twice)

- **The base rate is losing.** The vast majority of sports bettors lose money.
  The market is efficient and the book has the vig and can limit you.
- **Our own system shows no edge that survives to today.** Do not mistake a
  pretty backtest for a live edge — ours looked great in 2022–2024 and is
  negative now.
- **Variance is brutal.** A real +3% edge still has long losing streaks. Without
  bankroll discipline, variance — not lack of edge — busts most people.
- **It can become a problem.** If betting stops being a coldly analytical
  exercise, stop. Set deposit limits; never chase losses; never bet money that
  matters. US help: **1-800-GAMBLER**.
- **This is not financial advice.** It's a primer on how the machinery works.

The most profitable move available to you today is the one you've already made:
**measure honestly, in paper mode, until the numbers earn the right to risk real
money.**
