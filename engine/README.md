# FOMC — the Fed reaction-function engine

**Owner:** `career` OS (the S&T lane). **Class C** — deterministic, no model calls, no spend.
**Front door:** `/fed`. **Dashboard:** `jarvis-ui` → `/fed`.

A learning layer on 36 years of FOMC decisions, and a predictor that reads the
next meeting off it. Everything is derived from primary sources at run time —
FRED/ALFRED for the macro data, federalreserve.gov for the meeting calendar, and
the CME ZQ fed funds futures strip for what the market had actually priced.
Nothing is hand-typed, so nothing rots silently and nothing is remembered wrong.

---

## What it does

```
python cli.py all          # fetch -> build -> train -> backtest -> predict
python cli.py predict      # the next scheduled meeting
python cli.py backtest     # walk-forward validation (the only honest score)
python cli.py tune         # does macro beat what the market already priced?
python cli.py coefficients # the fitted reaction function, signed
python cli.py provenance   # where genuine vintage data starts, per series

python cli.py backtest --no-futures   # the same run without the futures block
```

`--no-futures` drops the fed funds futures columns back to the Treasury-spread
proxy the engine used before them, and writes to `*.noff` artefacts so it can
never overwrite the published ones. The claim that futures help is checkable in
one command rather than asserted in this file.

---

## The three things that make it honest

Most Fed-prediction accuracy numbers are inflated by one of three leaks. All
three are closed here, and closing them *lowers* the headline number — that is
the point.

**1. Point-in-time data.** Macro data is revised. Backtesting on today's revised
CPI hands a 1994 meeting numbers that did not exist until 1996. Every revisable
series is pulled through ALFRED's initial-release record, stamped with its real
publication date, and every lookup filters on `published <= meeting date`
(`fred.py`). Where ALFRED's vintage record does not reach back far enough — it
starts around 1997 for CPI, 2000 for core PCE, 2014 for Case-Shiller — the older
segment falls back to revised values stamped with that series' own *measured*
median publication lag. Timing stays honest; early values are slightly cleaner
than the Committee's were. `cli.py provenance` prints the split date per series,
and the same command prints where the futures strip stops and its proxy starts.

**2. Walk-forward validation.** To predict meeting *i*, the model may use
meetings `0..i-1` and nothing else — scaler, imputation medians, cutpoints and
coefficients are all refit at each step (`backtest.py`). No single fit scored on
a held-out tail, no full-sample standardisation leaking the future in sideways.

**3. Baselines stated before the result.** The Fed holds ~71% of scheduled
meetings, so any headline accuracy has to be read against *always say hold*,
against *repeat the last action*, and above all against *what the market had
already priced*. The backtest prints all three next to the model.

---

## Where the labels come from

FRED carries the policy target as a daily series — `DFEDTAR` (single target,
1982–2008) spliced to `DFEDTARU` (upper bound of the range, 2008–). Every change
in that series is a policy action, dated to the day it took effect. Join those
changes to the scraped meeting calendar and the full labelled record falls out
with no hand entry (`dataset.py`).

Classes are **ordered**, because the Committee's choice set is:

| class | move |
|---|---|
| `cut50+` | ≤ −50bp |
| `cut25` | −25bp |
| `hold` | no change |
| `hike25` | +25bp |
| `hike50+` | ≥ +50bp |

360 meetings labelled, 1990-02 → 2026-09. 293 scheduled, 67 unscheduled
(the 1998, 2001, 2008 and 2020 intermeeting actions). **Unscheduled meetings are
excluded from training by default** — an emergency cut is a different
data-generating process and mixing it in teaches nothing about the next
scheduled decision. `--with-unscheduled` includes them.

---

## The model

**Ordered logistic regression (proportional odds)**, pure Python, no
dependencies (`model.py`).

`P(y ≤ k) = σ(θ_k − x·β)`, with four ordered cutpoints. Every meeting sits on one
latent "policy pressure" axis, sliced into 25bp increments — which is the actual
shape of the thing being modelled. A multinomial classifier throws the ordering
away; a boosted tree on 293 observations describes the past and tells you nothing.

What this buys, beyond a point or two of accuracy:

- **One signed coefficient per feature.** The fitted model reads as a reaction
  function you can check against what a desk believes, and every prediction
  decomposes exactly into `β_j · z_j` per feature — the attribution in
  `cli.py predict` is arithmetic, not an approximation.
- **Calibrated probabilities.** The output is a distribution. "68% hold /
  27% cut25" is the usable answer; `argmax` is not.

Guards against overfitting 53 features on 293 meetings: L2 on all coefficients
(cutpoints unpenalised), expanding-window standardisation, a recency half-life
so the Volcker era informs shape without outvoting the last decade, and the fact
that every reported number is walk-forward.

---

## The features

57 columns in six blocks (`features.py`), all computed as of the morning the
meeting opens.

| block | what it carries |
|---|---|
| **policy** | level, ex-ante real rate, balanced-approach Taylor gap, 12m cumulative change, months since last move, direction and length of the current run, ZLB flag |
| **inflation** | core PCE (level, gap to 2%, 3m annualised, acceleration), core CPI, headline CPI, 5y breakeven, 10y TIPS real |
| **labour** | unemployment, gap to CBO natural rate, 6m change, **Sahm gap**, 3m payroll pace and its change, claims YoY, vacancies-per-unemployed, participation |
| **growth** | real GDP QoQ, industrial production, real retail sales, consumer sentiment and its change |
| **housing** | starts (YoY, 3m annualised), permits and permits-minus-starts, new-home sales, **months' supply**, Case-Shiller, mortgage spread to 10y, 6m change in the mortgage rate |
| **markets** | 3m/1y/2y Treasury minus effective funds, 10y−2y and 10y−3m, intermeeting 2y move, Baa credit spread and its 3m change, VIX, Chicago Fed NFCI, equity 3m return and 1y drawdown, oil YoY, **the fed funds futures expected move in bp**, and **option-implied P(cut) / P(hike)** |

**On housing specifically** (it was the block the request singled out): housing is
the transmission mechanism, not a coincident indicator. Policy reaches households
through the 30-year mortgage; builders respond in permits; permits lead starts
lead sales lead prices. It turns 2–4 quarters before the cycle does, which is why
permits and months' supply earn columns rather than being folded into "growth".

---

## Fed funds futures

This file used to say, in this spot, that futures were the engine's biggest
weakness and that `DGS1 − EFFR` / `DGS3MO − EFFR` stood in for them because
there was no free historical API. That turned out to be half wrong. CME's own
archive is genuinely paywalled, but the strip is reconstructable from two free
sources, and it is now in the feature set (`futures.py`).

### What exists, and what does not

Every candidate below was fetched on 2026-09-18. Nothing here is inferred from
what a source "probably" offers. The three sources marked **used** were each
downloaded and parsed directly while writing `futures.py`; the rejections were
confirmed by fetching the endpoint and reading what came back, not by reasoning
about what a vendor is likely to allow.

| source | reaches | cadence | verdict |
|---|---|---|---|
| **Nakamura–Steinsson replication archive** (Harvard Dataverse, **CC0**) | **1989-03-27 → 2015-02-11** | frozen since 2018 | **used.** The full **7-contract** ZQ strip, daily, 9,453 rows. The backbone. |
| **Yahoo Finance `ZQ=F`** | **2000-09-01 → today** | live, daily | **used.** Front contract only. No key, no cookie. Listed per-contract symbols (`ZQZ26.CBT`) give the *live* forecast a forward strip. |
| **Atlanta Fed Market Probability Tracker** | **2023-03-29 → today** | live, daily | **used** for `ff_p_cut` / `ff_p_hike`. Option-implied, so it carries the market's *uncertainty*, not just its mean. SOFR options, which is why it starts in 2023. Licensed for personal and educational use. |
| CME Group settlements | — | — | **no.** 403 on the settlement page, the JSON API *and* robots.txt, with the body naming automated access as prohibited. No free historical archive exists. Not worked around. |
| Stooq `ZQ.F` | deep, but unreadable | live | **no.** Every request returns HTTP 200 carrying a JavaScript proof-of-work challenge instead of data, and the bulk CSV endpoint answers "Access denied" even from a browser session that has just rendered the chart. The history is real and it is the longest of any candidate — it is simply not reachable by a headless client, and defeating the challenge is the kind of scraping this repo does not do. |
| FRED / ALFRED | — | — | **no futures- or expectation-derived series exists.** Searched. The hits are repo operations, the effective rate, the target range, and the SEP dot plot — which is the Committee's own projection, not the market's. |
| Cleveland Fed "Fed Funds Rate Predictions" | — | — | **gone.** 404, and absent from the live indicator index. Their Simple Monetary Policy Rules file is real but rule-based, not market-implied. |
| Nasdaq Data Link / Quandl, investing.com, Barchart, Databento | — | — | **no.** Bot-walled, ToS-prohibited, key-gated, or deprecated. |
| FRBSF USMPD · Bauer–Swanson · Gürkaynak–Sack–Swanson | 1988/1994 → today | live / frozen | **free, excellent, and deliberately refused.** They publish the *change* in the futures rate across a window bracketing the announcement — measured after the decision is known. Wiring that in would be the worst look-ahead leak available to this repo. |

**The splice is validated, not assumed.** Yahoo's front contract and the
Nakamura–Steinsson front contract overlap on **3,590 trading days
(2000-09-01 → 2015-02-11)**: mean difference **+0.15bp**, sd **1.57bp**, and
**0.5%** of days differ by more than 5bp. `test_fomc.py` re-measures this every
run and fails if it drifts, rather than trusting a number written here.

### How the expected move is computed

The CME FedWatch calculation — arithmetic on a settlement price, not a model. A
ZQ contract settles to the *average* effective funds rate over its delivery
month, so for a decision taking effect on day `e` of a month with `n` days:

```
R_month = (e-1)/n · r_before  +  (n-e+1)/n · r_after
```

solved for `r_after`. A policy change takes effect the day *after* the
announcement, which was checked against `DFEDTARU` rather than assumed.
`r_before` is a **10-day median** of the effective funds rate, not a mean: the
effective rate spikes on the last day of a month and collapses over the turn of
the year, and before this was a median those settlement artefacts produced
phantom priced moves — the January 2001 intermeeting *cut* read as +61bp of
priced *hike*.

### Where it still falls back to the proxy

**37 of 293 scheduled meetings**, all after February 2015. Two causes, both
structural: the meeting lands so late in the month that the FedWatch weighting
would amplify noise tenfold, or it crosses into the following month — and in
both cases the next month's contract is needed. Expired ZQ contracts are purged
from Yahoo, so a *historical* forward strip cannot be rebuilt; the
Nakamura–Steinsson strip supplied it until it ends in 2015. Those 37 rows use
`100 · (DGS3MO − EFFR)`, exactly as before.

The proxy also covers anything before 1989-03-27, but that is dead code for this
dataset: the labelled history starts at the February 1990 meeting, so the strip
already reaches further back than the engine does. Those 37 late-month meetings
are the whole of the real gap, and they sit in the most recent decade rather
than in the distant past, which is the worse place for a gap to be.
`cli.py provenance` prints the split next to the ALFRED vintage splits.

`ff_p_cut` and `ff_p_hike` have **no proxy at all** and are simply null before
2023-03-29. A Treasury-minus-funds spread is not a probability, and a
probability derived from the futures mean under a two-outcome assumption would
only be a monotone restatement of the expected move — no new information,
dressed up as new information. The imputation layer handles the gap.

### Does it actually help?

Walk-forward, 233 scheduled meetings, 1997-08-19 → 2026-09-16. Both columns are
the *same rows on the same day's data* — which is why all three baselines are
identical, and that identity is the check that the comparison is fair.

| | proxy only (`--no-futures`) | **shipped (futures)** |
|---|---|---|
| accuracy, 5-class | 79.40% | **82.40%** |
| accuracy, direction | 82.83% | **84.55%** |
| log loss | 0.527 | **0.494** |
| Brier | 0.284 | **0.263** |
| mean abs error | 7.25 bp | **6.76 bp** |
| move accuracy, 5-class | 53.42% | **58.90%** |
| move accuracy, direction | 64.38% | **65.75%** |
| false alarms on holds | 8.75% | **6.88%** |
| *baseline: always hold* | *68.67%* | *68.67%* |
| *baseline: market direction* | *66.09%* | *66.09%* |
| *baseline: repeat last action* | *78.97%* | *78.97%* |

**Verdict: it earns its place, so it ships on by default.** Every headline
metric improves, and the gain is concentrated where it should be — on the hard
subset. Exact-size accuracy on meetings where the Fed actually moved rises 5.5
points, and false alarms fall by a fifth.

`cli.py coefficients` now puts `ff_exp_move_bp` at the top of the fitted
reaction function at **+1.413**, nearly double the next feature. That is the
prediction the old version of this file made about what a real futures series
would do, and it is worth noting that it came out that way rather than quietly
letting it pass.

It is not uniform across time, and the direction of that is worth stating
because it is the opposite of what a "more data is better" story would predict.
Correlation of the priced move with the actual decision, by era:

| era | futures | `mkt_3m` proxy | `mkt_1y` proxy |
|---|---|---|---|
| 1990–1999 (n=80) | **0.324** | 0.544 | 0.635 |
| 2000–2015 (n=120) | **0.788** | 0.696 | 0.631 |
| 2015–today (n=56) | **0.979** | 0.864 | 0.881 |

In the 1990s the futures column is **worse than the proxy it replaces** — ZQ was
newly listed and thin, and the Fed did not announce decisions at all until 1994.
The full-history number is positive anyway because the model's 12-year recency
half-life means the 1990s barely vote, and because the ordered logit can lean on
whichever column is informative. It is still a real weakness and it is why the
older segment should not be read as evidence of anything.

The same comparison on narrower windows, so the improvement cannot be a
full-sample artefact:

| window | n | 5-class: proxy → futures | direction: proxy → futures | log loss |
|---|---|---|---|---|
| since 2000 | 213 | 79.34% → **83.10%** | 83.10% → **85.45%** | 0.525 → **0.487** |
| since 2015 | 93 | 76.34% → **80.65%** | 77.42% → **80.65%** | 0.499 → **0.423** |
| since 2023 | 28 | 64.29% → **71.43%** | 67.86% → **71.43%** | 0.620 → **0.519** |

Reproduce any row with `python cmp_window.py <start>` and the same under
`FOMC_FUTURES=0`. The 28-meeting window is far too thin to carry a conclusion on
its own and is shown because it is the only window where the option-implied
probability columns exist at all.

---

## How to read the output

`cli.py predict` gives a distribution, an expected move in basis points, the
implied target, and the features pushing each way. Read the **probability**, not
the argmax. The model's job is to tell you 30% when it is 30%, and the
calibration table in the backtest is where you check whether it does.

`cli.py path` runs the next N meetings — but every meeting past the first is
scored on **today's** data. It is "what would the Fed do at that meeting if
nothing changed", a read on how much pressure is already in the data. It is not
a rate-path forecast and must not be quoted as one.

`cli.py watch` is the opposite object: no model at all, the **market's** path.
Each upcoming meeting is read off its own ZQ contract through the FedWatch
arithmetic, split across the two bracketing 25bp outcomes, and chained into a
target-range distribution per meeting, with the Atlanta Fed's option-implied
read beside it as a cross-check on the tails. Row one equals the scored
market read exactly. Method and caveats: `watch.py`'s docstring.

---

## What it cannot do

- **It has no text.** Statement language, dot plots, speeches and the Chair's
  press conference are often the whole story at a meeting where the data is
  ambiguous. None of it is in here.
- **It cannot see a shock.** March 2020 is in the training data as a label;
  nothing in the feature set would have called it on March 1st.
- **It is weakest exactly where it matters most** — at turning points, where
  the market is also wrong and where there is the least historical precedent.
  The futures block makes this *worse*, not better: futures are most accurate
  when a decision is already telegraphed, and telegraphed decisions are the ones
  that were never hard.
- **The futures column is degenerate at the zero bound.** ZQ settles to the
  *effective* funds rate, and in late 2008 the effective rate had already
  collapsed to ~14bp against a 1.00% target — so the strip priced no cut into
  the December 2008 meeting that cut 75bp. That is not a bug in the arithmetic,
  it is what the instrument could express. The `zlb` flag is in the feature set
  for this reason, and it is not a full answer.
- **Regime change breaks it.** A reaction function fitted through 2026 assumes
  the 2027 Committee reacts like the 2020s one. A new Chair, a framework review,
  or a change in the inflation target invalidates it, and the half-life only
  softens that, it does not solve it.

---

## Files

```
fred.py          FRED/ALFRED client: vintages, splicing, point-in-time lookups
futures.py       CME ZQ strip + option-implied probabilities, spliced to the proxy
calendar_fed.py  meeting calendar scraped from federalreserve.gov
dataset.py       decisions derived from the target series, labelled
features.py      the reaction function as 57 point-in-time columns
matrix.py        design matrix + expanding-window imputation and scaling
model.py         ordered logit, pure Python
backtest.py      walk-forward validation and baselines
tune.py          hyperparameter sweep + feature-block ablation
predict.py       train the production model; forecast with attribution
watch.py         CME watch: the futures-priced path across the next 8 meetings
cli.py           front door
cmp_window.py    score a narrowed window without publishing it (comparison only)
data/            cached series, calendar, labelled decisions, design matrix
models/          current.json (trained), backtest.json, tuning.json
                 *.noff.json -- the same artefacts built with --no-futures
```

Data and model artefacts rebuild from scratch with `python cli.py all`. The only
secret is `FRED_API_KEY`, already in `jarvis-ui/apps/web/.env.local`.
