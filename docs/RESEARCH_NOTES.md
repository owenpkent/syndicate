# Research Notes: What Medallion & the Quant/Betting World Teach a Sports-Trading Pipeline

A literature/industry scan of Renaissance Technologies' **Medallion Fund**, large
quant hedge funds, market-makers/HFT, and professional sports-betting syndicates —
distilled into disciplines a small quantitative sports paper-trading pipeline
(this repo) can actually borrow. Each claim is tagged by confidence
(HIGH/MEDIUM/LOW) and verifiable vs. anecdotal.

> **Honest framing (per [CLAUDE.md](../CLAUDE.md)):** none of this implies
> sportsball *has* an edge. The transferable lessons are the **disciplines**
> (breadth, CLV, fractional Kelly, abstain-by-default, decay-awareness), not the
> returns. Every source converges on the same gate the project already states:
> **CLV vs. a sharp closing line is the test for whether an edge exists** — and
> that's blocked on real closing-odds data, not code.
>
> *Sourcing caveat:* this was compiled from web-search excerpts of the cited
> primary sources (direct page fetches were rate-limited); each headline number
> was corroborated across ≥2 independent results.

---

## 1. The track records (the "is this even possible?" scoreboard)

| Firm / person | Headline number | Conf. |
|---|---|---|
| **Medallion** (RenTech) | ~**66% gross / 39% net** annualized 1988–2018; $100→~$399M; **σ≈31.7%, Sharpe >2**; *negative* market beta; only one losing year (1989, ~−4%) | HIGH |
| **Citadel** (Wellington) | >19%/yr net since 1990; **+38% / ~$16B profit in 2022**; but **−55% in 2008** | HIGH |
| **D.E. Shaw** (Composite) | ~12.5%/yr net since 2001, one down year; >$90B capital | MED-HIGH |
| **Two Sigma** | ~$70B AUM; 2024 flagship +10.9%; **SEC fined it $90M (2025)** for undisclosed model changes | HIGH |
| **AQR** | Value factor **−~12%/yr 2018–2020** (AUM $226B→$164B), then +43.5% in 2022 — factors *mean-reverted* over ~4 yrs | HIGH |
| **Jane Street** | **$20.5B** net trading revenue 2024 (≈2× 2023); ~10% of US equity volume | HIGH |
| **Citadel Securities** | ~**20–25% of US equities**, ~35% of US *retail* flow; record ~$12B revenue 2025 | HIGH |
| **Hudson River Trading** | record $6.4B quarterly revenue; *de-emphasizing* raw latency toward modeling/AI | MED-HIGH |
| **Bill Benter** | ~**US$1B** career on HK horse racing; multinomial-logit model | HIGH (figure widely reported) |
| **Ed Thorp** (Princeton/Newport) | **15.8%/yr at 4.3% σ** vs. market 10.1% at 17.3% σ | HIGH |
| **Tony Bloom / Starlizard**, **Benham / Smartodds** | football syndicates; scale figures (£100M/yr won, £600M/yr staked) | LOW-MED (secretive / litigation-sourced) |
| **Susquehanna (SIG)** | options market-maker; poker/EV culture; now a sports/prediction-market desk | HIGH |

---

## 2. The seven ideas that recur everywhere

1. **Edge = a tiny per-bet margin × enormous, _independent_ breadth.** Medallion is
   reputedly ~50.75% per bet over ~100k bets/yr (anecdotal, Zuckerman); HFT races
   are worth ~half a tick but total ~$5B/yr; Thorp ran many uncorrelated hedged
   wagers. Formalized by **Grinold's Fundamental Law: IR = IC × √Breadth** — win by
   raising skill *or* adding uncorrelated bets; portfolio vol falls as σ/√N **only
   if bets are independent**. *(HIGH)*
2. **Ensemble many weak signals; don't hunt one oracle.** Two Sigma / D.E. Shaw
   blend hundreds of modest signals; RenTech mixes trend + mean-reversion. Lift
   comes from *combining decorrelated* features, not a stronger single one. *(HIGH)*
3. **Risk limits are a _source_ of return, not just a safety net.** Pod shops
   hard-code drawdown kills (Millennium: −5% halves a pod, −7.5% terminates it);
   multi-managers lost <1% in the Mar-2020 crash vs. −7.3% for hedge funds broadly.
   Citadel still went −55% in 2008 when leverage met illiquidity. *(HIGH)*
4. **Execution / line-shopping is worth as much as prediction.** Market-makers' whole
   business is capturing the bid-ask spread; takers pay ~⅓ of the effective spread
   just for being slower (QJE HFT study). In betting the **vig is the spread**. *(HIGH)*
5. **Closing Line Value is _the_ edge metric.** The no-vig closing line is the
   market's most efficient estimate; **beating it ⇒ +EV**, and positive-CLV bettors
   are almost universally profitable *regardless of short-term variance*. CLV proves
   edge in ~tens of bets vs. thousands for P&L. *(HIGH)*
6. **Fractional Kelly is the professional default.** f\* = edge/odds maximizes
   log-growth, but full Kelly has a ~1-in-3 chance of a ≥50% drawdown. **Half-Kelly
   ≈ 75% of the growth at ~half the volatility** (MacLean-Ziemba-Blazenko 1992);
   quarter-Kelly when estimates are noisy — because you bet your *estimated* edge and
   overestimation lands you on the losing side of the growth curve. *(HIGH)*
7. **Edges decay; backtests lie.** McLean-Pontiff: ~58% of published-anomaly alpha
   disappears post-publication; crowding pushes per-agent edge toward K/N; López de
   Prado's **Deflated Sharpe Ratio** corrects for the fact that testing many
   strategies manufactures false discoveries. *(HIGH)*

---

## 3. The single most important insight for this repo

**Benter's breakthrough: feed the market price _into_ the model as a feature, not
just use it as the EV benchmark.** When Hong Kong published win odds (~1990), Bill
Benter added the public's implied probability as an input variable to his own logit
model and called it the most profitable thing he ever did. Smartodds (Dixon-Coles)
and every serious syndicate do versions of this. *(HIGH — verifiable from Benter's
own 1994 paper.)*

Sportsball today models `P_true` purely from Elo/features, then compares to the
market for EV. The evidence says a **blend of (model probability, market-implied
probability)** — benchmarked against a *sharp* closing line, not a soft opener — is
the highest-evidence accuracy upgrade available, and it is exactly the roadmap's
"train against CLV" item.

---

## 4. Concrete changes for sportsball (mapped to the code)

1. **Add market-implied probability as a model feature** (Benter). Feed the
   producer's no-vig implied prob into `quant/features.py` as a new feature, keeping
   `quant/` I/O-free (the value rides on the signal). Highest-evidence model change.
2. **Promote CLV to the headline KPI.** `tools/clv.py` exists but is secondary — make
   "did we beat the no-vig close?" the gate that decides whether the model has edge,
   *before* trusting backtest ROI. Pairs with the `ingest-odds` path.
3. **Benchmark calibration against the sharp closing line,** not the opener — extend
   the temperature-calibration work in `evaluate` / `measure-features`.
4. **Keep ¼–½ Kelly; shrink it when calibration `T` is high.** Repo already uses
   `kelly_multiplier=0.25`; literature endorses this. Tie the fraction to edge
   uncertainty explicitly.
5. **Enforce _independence_ in breadth.** IR = IC×√Breadth counts only uncorrelated
   bets — many legs on one game/slate aren't independent. Weight the
   `PortfolioRiskManager` correlation penalty by same-game/same-day correlation.
6. **Treat decay as a given + guard the backtest.** Keep the retrain loop; add a
   Deflated-Sharpe / multiple-testing adjustment to `eval-duckdb` / `backtest-sim`
   so a model picked from many candidates isn't a false discovery.
7. **Hard, automatic exposure limits** (pod-shop lesson): per-event and aggregate
   caps enforced non-discretionarily — the Sniper exposure + Settlement reaper
   already model this primitive.

---

## 5. Why Medallion specifically is inimitable

Closed to outsiders since 1993; **employee-only** capital; capacity-capped at
~$10–15B with profits distributed (not compounded); leveraged via **basket options**
(the ~$7B IRS settlement); built by scientists (Simons, Laufer, Mercer, Brown) not
financiers; extreme secrecy (NDAs, no conferences). The clincher: RenTech's *own*
outside fund **RIEF fell ~20% in 2020 while Medallion rose ~76%** — the edge
**doesn't scale and doesn't transfer**. A small operator's advantage is its
*smallness* (it can exploit micro-inefficiencies that vanish with size), which is
precisely why the disciplines above — not the returns — are what's portable.

---

## Sources

**Renaissance / Medallion**
- Cornell, "Medallion Fund: The Ultimate Counterexample?" — https://papers.ssrn.com/sol3/papers.cfm?abstract_id=3504766
- Renaissance Technologies — https://en.wikipedia.org/wiki/Renaissance_Technologies
- Growth of $100 in Medallion (Visual Capitalist) — https://www.visualcapitalist.com/growth-of-100-invested-in-jim-simons-medallion-fund/
- "Medallion surged 76% in 2020, outside funds tanked" (Institutional Investor) — https://www.institutionalinvestor.com/article/2bswms7wco7as686o8ikg/portfolio/renaissances-medallion-fund-surged-76-in-2020-but-funds-open-to-outsiders-tanked
- US Senate basket-options report — https://www.govinfo.gov/content/pkg/CHRG-113shrg89882/html/CHRG-113shrg89882.htm
- Renaissance ~$7B IRS settlement (CNBC) — https://www.cnbc.com/2021/09/03/renaissance-executives-agree-to-pay-7-billion-to-settle-tax-dispute.html
- Zuckerman, "The Man Who Solved the Market" (notes) — https://novelinvestor.com/notes/the-man-who-solved-the-market-by-gregory-zuckerman/

**Large quant funds**
- Two Sigma — https://en.wikipedia.org/wiki/Two_Sigma ; SEC $90M fine — https://www.globaltrading.net/two-sigma-fined-90m-by-sec-over-trading-model-scandal/
- D.E. Shaw — https://www.deshaw.com/what-we-do/investment-management
- Citadel — https://en.wikipedia.org/wiki/Citadel_LLC ; pod model — https://www.efinancialcareers.com/news/2023/10/citadel-millennium-hedge-funds
- AQR, "The Long Run Is Lying to You" — https://www.aqr.com/-/media/AQR/Documents/Perspectives/The-Long-Run-Is-Lying-to-You.pdf
- McLean & Pontiff, anomaly decay — https://www.fmg.ac.uk/sites/default/files/2020-08/Jeffrey-Pontiff.pdf

**Market makers / HFT**
- Jane Street record revenue — https://mlq.ai/news/jane-street-reports-record-205-billion-net-trading-revenue-for-2024-nearly-doubling-previous-year/
- Citadel Securities — https://en.wikipedia.org/wiki/Citadel_Securities
- Hudson River Trading record quarter — https://www.hedgeweek.com/hudson-river-trading-reports-record-q1-revenue/
- Aquilina, Budish & O'Neill, "Quantifying the HFT Arms Race" (QJE) — https://academic.oup.com/qje/article/137/1/493/6368348

**Sports-betting syndicates**
- Bill Benter — https://en.wikipedia.org/wiki/Bill_Benter ; 1994 paper — https://gwern.net/doc/statistics/decision/1994-benter.pdf ; annotated — https://actamachina.com/posts/annotated-benter-paper
- Tony Bloom / Starlizard — https://en.wikipedia.org/wiki/Tony_Bloom
- Matthew Benham / Smartodds — https://en.wikipedia.org/wiki/Matthew_Benham
- Susquehanna International Group — https://en.wikipedia.org/wiki/Susquehanna_International_Group
- CLV demystified (Buchdahl/Pinnacle) — https://www.pinnacleoddsdropper.com/blog/closing-line-value--clv-demystified-by-expert-joseph-buchdahl
- Why Pinnacle beats recreational books — https://bet2invest.com/blog/Why-Pinnacle.com-(Sharp-Bookmaker)-is-Better-than-Bet365.com-(Recreational-Bookmaker)-in-the-Long-Run

**Cross-cutting technique**
- Kelly criterion — https://en.wikipedia.org/wiki/Kelly_criterion
- Thorp, "The Kelly Criterion in Blackjack, Sports Betting, and the Stock Market" — https://gwern.net/doc/statistics/decision/2006-thorp.pdf
- Edward O. Thorp — https://en.wikipedia.org/wiki/Edward_O._Thorp
- Grinold's Fundamental Law of Active Management — https://blankcapitalresearch.com/learn/grinold-fundamental-law-active-management
- Law of large numbers & casino earnings (Alpha Architect) — https://alphaarchitect.com/2014/01/the-law-of-large-numbers-and-casino-earnings/
- Closing Line Value (VSiN) — https://vsin.com/how-to-bet/the-importance-of-closing-line-value/
- Bailey & López de Prado, Deflated Sharpe Ratio — https://papers.ssrn.com/sol3/papers.cfm?abstract_id=2460551
