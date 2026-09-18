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

| | model | always-hold | repeat-last | market-implied |
|---|---|---|---|---|
| direction, all meetings | **84.6%** | 68.7% | 79.0% | 66.1% |
| 5-class, all meetings | **82.4%** | — | — | — |
| log loss | **0.494** | — | — | — |
| mean abs. error | **6.76 bp** | — | — | — |

**Read the baselines first.** The Fed holds ~71% of scheduled meetings, so an accuracy
number without them means nothing at all. That is why they sit in the same table.

### The number that actually matters

All-meeting accuracy is dominated by holds. On the 73 meetings where the Committee
*moved*, the picture inverts — and it is the honest way to read this model:

| on the 73 moves / 160 holds | model | market-implied curve |
|---|---|---|
| direction correct **on moves** | 65.8% | **91.8%** |
| cries "move" **on holds** (false alarm) | **6.9%** | 45.6% |

The curve looks brilliant on moves because it is scored on a subset chosen by the
outcome. It buys that 91.8% by signalling a move at **45.6% of all holds** — nearly one
in two. The model trades recall for precision, deliberately and heavily. Neither column
is the whole truth, which is exactly why `cli.py backtest` prints both and the dashboard
cannot render one without the other.

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

## What it cannot do

- **It has no text.** Statement language, the dot plot, speeches and the press conference
  are often the whole story at an ambiguous meeting. None of it is in the feature set.
- **It cannot see a shock.** March 2020 is in the training data as a label; nothing in the
  features would have called it on March 1st.
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
  features.py    the reaction function as point-in-time columns
  model.py       ordered logit (proportional odds)
  backtest.py    walk-forward validation and baselines
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
