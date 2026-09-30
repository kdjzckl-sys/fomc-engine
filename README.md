# FOMC — a reaction-function engine

An ordered-logit model of the Federal Reserve's reaction function, fitted on 36 years
of decisions and scored walk-forward against the baselines it has to beat.

Everything is derived from primary sources at run time — FRED/ALFRED for the macro data,
federalreserve.gov for the meeting calendar, and a reconstructed fed funds futures strip
for what the market had already priced. **Nothing is hand-typed**, so nothing rots quietly
and nothing is remembered wrong.

```bash
cp .env.example .env          # free FRED key: fred.stlouisfed.org/docs/api/api_key.html
cd engine && python cli.py all    # fetch → build → train → backtest → predict
```

No dependencies. The ordered logit is ~200 lines of pure Python; the xlsx parser is
`zipfile` + `ElementTree`. `python test_fomc.py` runs the suite.

---

## The scorecard

Walk-forward, 233 scheduled meetings, 1997-08-19 → 2026-09-16. Every number below is
out-of-sample: to predict meeting *i* the model sees meetings `0..i-1` and nothing else —
scaler, imputation medians, cutpoints and coefficients all refit at each step.

| | model | always-hold | repeat-last | curve proxy | fed funds futures |
|---|---|---|---|---|---|
| direction, all meetings | 84.6% | 68.7% | 79.0% | 66.1% | **90.8%** |
| meetings it can score | 233 | 233 | 233 | 233 | **196** |
| 5-class, all meetings | **82.4%** | — | — | — | — |
| log loss | **0.494** | — | — | — | — |
| mean abs. error | **6.76 bp** | — | — | — | — |

**Read the baselines first.** The Fed holds ~71% of scheduled meetings, so an accuracy
number without them means nothing at all. That is why they sit in the same table.

**Two of those columns are both called "the market" and they are not the same thing.**
The *curve proxy* is the sign of `DGS1 − EFFR`, a yield spread standing in for market
expectations; it covers every meeting. *Fed funds futures* is the CME ZQ strip run
through the FedWatch arithmetic — what the market had genuinely priced — and it is
**null on the 37 meetings the strip cannot reach**, rather than quietly falling back to
the spread. That is why its column says 196 and the others say 233.

### The number that actually matters

All-meeting accuracy is dominated by holds. On the 73 meetings where the Committee
*moved*, the picture inverts — and it is the honest way to read this model:

| | model | curve proxy | fed funds futures |
|---|---|---|---|
| direction correct **on moves** | 65.8% | **91.8%** | 83.1% |
| cries "move" **on holds** (false alarm) | 6.9% | 45.6% | **5.3%** |
| scored on | 233 (73 / 160) | 233 (73 / 160) | 196 (65 / 131) |

**Read the two rows together or not at all.** The curve's 91.8% is the number that looks
like a verdict and is not one: it is bought by signalling a move at **45.6% of all
holds**, nearly one in two. The model trades recall for precision, deliberately and
heavily. The engine publishes recall and its false-alarm rate in the same record for
exactly this reason, `cli.py backtest` prints them as one table, and the dashboard reads
that table rather than deriving its own.

**The real futures strip is not bought that way, and it beats the model on both axes at
once.** On the 196 meetings it reaches, every caller rescored on those same rows:

| on the 196 meetings the strip reaches | direction, all | on moves | false alarm on holds |
|---|---|---|---|
| model | 84.7% | 64.6% | 5.3% |
| always say hold | 66.8% | 0.0% | 0.0% |
| repeat the last action | 81.1% | 66.2% | 11.5% |
| curve proxy | 66.3% | 90.8% | 45.8% |
| **fed funds futures** | **90.8%** | **83.1%** | **5.3%** |

That is the honest headline of this repo and it is not a flattering one: an ordered logit
on 57 macro columns does not beat what the market had already priced, on the meetings
that matter, at equal false-alarm cost. It is not close — 18.5 points of recall. Nothing
was tuned to produce that number: a move counts as "priced" past **±12.5bp**, half of the
smallest increment the Committee uses, chosen before the number was computed and not
searched over. Tightening the band to ±10bp makes the futures column *better* (90.8% on
moves at 6.9% false alarms), not worse, so the conclusion does not hang on where the band
sits.

What the model is still for: it is a *distribution* with an exact attribution behind it,
fitted on the reaction function rather than on the price. The futures strip tells you
what is priced and nothing about why. Read the model as a structured argument with the
market, not as an edge over it.

### Who carries the headline: the market handoff

The scorecard reads everything the evening before a meeting. The dashboard forecasts
weeks out. So `cli.py horizon` re-reads every scored meeting N days before it opens
(macro data and futures strip alike, point-in-time), asks the walk-forward model about
it from there, and sets that against what the strip priced the same day. A meeting is
scored at N days only if it was already the next meeting then.

| days out | meetings | model | futures | on moves: model / futures | false alarms: model / futures |
|---|---|---|---|---|---|
| 1 | 196 | 84.7% | **90.8%** | 64.6% / 83.1% | 5.3% / 5.3% |
| 7 | 187 | 84.0% | **95.7%** | 62.3% / 95.1% | 5.6% / 4.0% |
| 14 | 164 | 86.6% | 88.4% | 62.0% / 86.0% | 2.6% / 10.5% |
| 21 | 132 | 84.8% | 87.1% | 57.9% / 81.6% | 4.3% / 10.6% |
| 28 | 130 | 85.4% | 86.2% | 55.6% / 91.7% | 3.2% / 16.0% |
| 42 | 67 | 83.6% | 83.6% | 50.0% / 77.8% | 4.1% / 14.3% |

The 1-day row reproduces the scorecard exactly, which is the check that the harness is
the backtest and not a cousin of it. The rule was written before the table: the market
takes the headline at horizons where, on the same meetings, it called direction more
often than the model, out to the last horizon where that holds without a break. That is
**28 days** (`handoff_days` in `engine/models/horizon.json`), and nothing else in the
repo hard-codes it. `predict --json` carries the market read (FedWatch arithmetic on the
strip, same ±12.5bp band as the scorecard), the model read, and a `handoff` block with
`headline_source` and a one-line reason. The dashboard shows both side by side and
flips the headline. With no strip reading for the meeting, the headline stays on the
model and says the feed is missing.

**Read the table, not just the flag.** The market's edge is decisive inside a week.
From 14 to 28 days out it is one to three meetings, bought with three to five times the
model's false alarms on holds. The option-implied `ff_p_cut` / `ff_p_hike` ride along
but are not this meeting's odds; they cover the nearest SOFR-option window. The horizon
run takes ~8 minutes and is not in the daily refresh: rerun it after `cli.py all` when
the sample or the model changes.

---

## The three things that make it honest

Most published Fed-prediction accuracy is inflated by one of three leaks. All three are
closed here, and closing them *lowers* the headline number. That is the point.

**Point-in-time data.** Macro data is revised. Backtesting on today's revised CPI hands a
1998 meeting numbers that did not exist until 2000. Every revisable series is pulled
through ALFRED's initial-release record, stamped with its real publication date, and
every lookup filters on `published <= meeting date`. `cli.py provenance` prints, per
series, where genuine vintage data starts and where the fallback takes over.

**Walk-forward validation.** No single fit scored on a held-out tail, no full-sample
standardisation leaking the future in sideways.

**Baselines stated before the result** — including what the market had already priced,
which is the only one that is hard to beat.

---

## Fed funds futures

The market's own forecast is the strongest single feature, and CME's historical archive
is paywalled. It is nonetheless reconstructable from free sources:

| source | coverage | licence |
|---|---|---|
| Nakamura–Steinsson replication archive | 1989-03 → 2015-02, full 7-contract strip | CC0 |
| Yahoo Finance `ZQ=F` | 2000-09 → today, front contract | free, no key |
| Atlanta Fed Market Probability Tracker | 2023-03 → today, option-implied P(cut)/P(hike) | personal/educational |

The splice is **measured, not assumed**: over 3,590 overlapping days the two front-contract
series differ by a mean of +0.15 bp (sd 1.57 bp), and `test_fomc.py` re-measures it on
every run.

Wiring it beat the yield-spread proxy it replaced on every metric — 5-class accuracy
79.4% → **82.4%**, log loss 0.527 → **0.494**, false alarms 8.8% → **6.9%** — so it ships
on by default. `--no-futures` rebuilds the old feature set into `*.noff` artefacts, which
can never overwrite the published ones, so the claim stays falsifiable in one command.

**Deliberately not used:** FRBSF USMPD, Bauer–Swanson and Gürkaynak–Sack–Swanson. They are
free and excellent, and they publish the *change* in the futures rate across a window
bracketing the announcement — measured after the decision is known. That would be the
worst look-ahead leak available to this project.

---

## Beyond the macro panel: news, Fed text, shocks

Three more blocks are built, point-in-time, tested for leaks — and **off by default**,
because the walk-forward record says they do not earn it yet.

| block | columns | source | switch |
|---|---|---|---|
| news | tone level, 90-day change, change since last meeting; policy uncertainty level + change | SF Fed Daily News Sentiment (1980–), Baker-Bloom-Davis EPU (`USEPUINDXD`, 1985–) | `--with-news` |
| text | dot-plot revisions, dots minus the target, risk-tilt change, redline size, net dissent direction | the **prior** statement and SEP only (`engine/text/`) | `--with-text` |
| shock | 5d/90d VIX, 1-month equity and oil moves, 1-month Baa jump, 4-week NFCI change, EPU spike, news-tone collapse, count of tripped gauges | the series above | `--with-shock` |

News sentiment is stamped public 10 days after the day it describes (the index is weekly
with a ~3-day lag, so this is the worst case). The SF Fed re-estimates its history on each
update, so the historical values are today's vintage — the same compromise as the
non-vintage market series. The text block uses exactly the six columns `engine/text/README.md`
measured as surviving the persistence control; the hawk/dove lexicon is left out because it
mostly re-reads the last decision.

**What they bought**, 233 meetings, walk-forward, measured 2026-09-30:

| | 5-class | direction | log loss | direction on moves | false alarms |
|---|---|---|---|---|---|
| **published** (macro + futures), L2 2 | 82.4% | 84.5% | **0.491** | 64.4% | 6.3% |
| + news + shock, L2 2 | **84.1%** | **87.1%** | 0.517 | 68.5% | **4.4%** |
| + news + shock, L2 4 | 82.8% | 85.8% | 0.498 | 67.1% | 5.6% |
| + news + shock + text, L2 2 | 83.3% | 85.8% | 0.527 | 68.5% | 6.3% |
| + news + shock + text, L2 4 | 83.3% | 85.8% | 0.502 | 67.1% | 5.6% |

More meetings called right — up to six on direction — and a third fewer false alarms. But
**every configuration makes the probabilities worse**, and no regularisation strength tested
(2/4/8/16) closes that gap: tightening L2 recovers log loss only by giving the accuracy back.
The engine's output is a probability, and six meetings in 233 is inside the noise. Futures
shipped because they improved every metric at once; these do not, so they stay switchable
rather than published. Text adds nothing on top of news + shock — the futures strip already
prices what the prior statement said.

Reproduce any row: `python cli.py all --with-news --with-shock --l2=4`. Every non-published
configuration writes to its own tagged artefacts (`backtest.withnews-withshock.json`) and
cannot overwrite the scorecard.

---

## What it cannot do

- **It cannot read the meeting's own text.** Statement language, the press conference and
  speeches are often the whole story at an ambiguous meeting. The prior statement and dots
  are available as a block (above) and measured; the live meeting's words are not, by
  construction.
- **It still cannot see a shock coming.** The shock block reads stress once it is in the
  tape; nothing would have called March 2020 on March 1st.
- **The futures column is degenerate at the zero bound.** ZQ settles to the effective rate,
  which had already collapsed to ~14 bp by December 2008 — so the strip priced no cut into
  a 75 bp cut.
- **It is weakest exactly where it matters most** — at turning points, where the market is
  also wrong and where there is least precedent.
- **Regime change breaks it.** A reaction function fitted through 2026 assumes the 2027
  Committee reacts like the 2020s one.

---

## Layout

```
engine/          the model. Python, no dependencies
  fred.py        FRED/ALFRED client: vintages, splicing, point-in-time lookups
  futures.py     the reconstructed ZQ strip + option-implied probabilities
  news.py        news sentiment + news-based policy uncertainty
  text/          statements and dot plots, strictly point-in-time
  features.py    the reaction function as point-in-time columns
  model.py       ordered logit (proportional odds)
  backtest.py    walk-forward validation and baselines
  horizon.py     model vs futures by days-to-meeting; sets the market handoff
  cli.py         front door
web/             Next.js dashboard — calibration, baselines, attribution
```

`engine/README.md` is the long-form documentation: the label derivation, the feature
blocks, the guards against overfitting, and how to read the output.

The dashboard shells out to the CLI rather than porting the model to TypeScript, so the
web view and the command line can never disagree about what the Fed is going to do.

```bash
cd web && npm install && npm run build && npm start   # → localhost:3050
```

**Staying current.** The deployed dashboard reads `web/data/snapshot.json`, because a
serverless host has no Python and no FRED key. `.github/workflows/refresh.yml` regenerates
it every weekday at 22:30 UTC (after the close and the day's prints) and on Sundays (the
SF Fed's weekly sentiment update): full fetch → build → train → backtest → tests →
snapshot → commit. The Vercel project is git-connected with root `web/`, so that commit
is the deploy. `generated_at` is rendered on the page — a stalled job shows as a stale date,
never as a live forecast. Needs one repo secret, `FRED_API_KEY`.
