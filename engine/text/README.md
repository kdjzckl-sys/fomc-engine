# engine/text — statement and SEP text, point-in-time

**Owner:** `career` OS. **Class C** — deterministic, no model calls, no new
dependency, no new key. **Front door:** `python cli.py` inside this directory.

`engine/README.md` lists "it has no text" as the model's first limitation. This
package is the data layer that closes it: every FOMC statement and every dot
plot the Board publishes, fetched at run time, cached, stamped with a real
publication date, and exposed through the same point-in-time lookups `fred.py`
uses — with one character changed, which is the whole story.

**Nothing here is wired into the design matrix.** `features.py`, `matrix.py`,
`backtest.py` and `cli.py` are untouched. The columns are computed, their
coverage is measured, and their worth is reported against a yardstick already in
the repo. What to do with them is a separate decision, and the last section says
what this file would recommend.

```bash
python cli.py index       # rebuild the document index from federalreserve.gov
python cli.py fetch       # pull every statement and SEP into the cache
python cli.py coverage    # what each column covers, by era
python cli.py signal      # what each column is worth, and the yardstick
python cli.py leak        # what using a meeting's OWN statement would score
python cli.py features    # the derived rows for the 233 backtest meetings
python cli.py show 2024-09-18
python cli.py test
```

---

## The leak this exists to avoid

A meeting's statement is published **at that meeting**, at 2:00 p.m. on the day
the decision lands, in the same release as the decision. So is its SEP — the
Board's projections FAQ: the charts and tables "are released shortly after the
conclusion of the meeting." Using either to predict its own meeting is not a
subtle look-ahead bias; the statement contains the sentence *"the Committee
decided to raise the target range"*.

That is why every lookup here is **strict**, where `fred.py`'s is not:

| | rule | why |
|---|---|---|
| `fred.as_of(obs, when)` | `pub <= when` | a CPI print that morning **is** on the Committee's desk |
| `statements.latest_before(when)` | `pub < when` | the 2 p.m. statement **is not** |
| `sep.latest_before(when)` | `pub < when` | same release, same instant |

**The size of the hole, measured rather than asserted.** Three regexes looking
for *"decided to raise"* / *"decided to lower"* in a statement:

| classifier | direction accuracy, 233 meetings |
|---|---|
| three regexes on the meeting's **own** statement | **87.7%** (n=219) |
| the published ordered-logit model | 84.55% |
| the same three regexes on the **prior** statement | 71.2% |
| always-hold baseline | 68.67% |

Three regexes on a document published simultaneously with the decision beat the
entire model. That is what a text leak buys, and it is why `test_text.py`
asserts strictness on every meeting in the record rather than on a sample.
`python cli.py leak` reproduces the table.

Minutes are the one attached document that is genuinely new information between
meetings, and only because they print late. The Board writes the release date
next to the link — *"(Released February 19, 2025)"* — so it is **parsed**, not
assumed to be meeting + 21 days. All 268 of them carry a real one, every one is
strictly after its meeting, and the median lag is 21 days.

---

## What is actually retrievable

Every URL below was fetched on **2026-09-18**. Nothing is inferred from what the
site "probably" serves.

`federalreserve.gov/robots.txt` returns **404** — checked, not assumed. There is
no exclusion to respect, so the politeness is self-imposed: a descriptive
User-Agent, a serial fetcher with a 0.3 s delay, and a cache with no expiry on
archived documents, so the whole corpus is downloaded exactly once.

### Statements

**246 statements, 1994-02-04 → 2026-09-16.** Ten of them are intermeeting
liquidity or facility releases rather than policy statements (August 2007, March
2020 and similar); they are kept, tagged `kind="other"`, and excluded from the
prior-statement lookup by default, because mixing a 77-word liquidity notice
into a length feature reads as the Committee suddenly going quiet.

The Board files them under **four** URL schemes and the archive index changes
layout twice, so `index.py` harvests whatever the Board links rather than
guessing a scheme per year:

| era | scheme |
|---|---|
| 1994–1998 | `/fomc/{YYYYMMDD}DEFAULT.htm` |
| 1999–2005 | `/boarddocs/press/{general,monetary}/{YYYY}/{YYYYMMDD}/[default.htm]` |
| 2006–2015 | `/newsevents/press/monetary/{YYYYMMDD}a.htm` |
| 2016– | `/newsevents/pressreleases/monetary{YYYYMMDD}a.htm` |

The index pages are `fomccalendars.htm` (the rolling five years plus the future)
and `fomchistorical{YYYY}.htm` (one page per year). The historical pages stop
five years short of today — 2021 onward returns 404, which is the transcript
embargo, not a fetch failure.

**The part that matters more than the span.** The FOMC did not announce
decisions at all before February 1994, and from 1994 to 1999 it issued a
statement **only when it changed policy**:

| year | scheduled meetings with their own statement |
|---|---|
| 1990–1993 | 0 / 8 each — the Committee did not announce |
| 1994 | 5 / 8 |
| 1995 | 2 / 8 |
| 1996 | 1 / 8 |
| 1997 | 1 / 8 |
| 1998 | 2 / 8 |
| 1999 | 6 / 8 |
| 2000 onward | **8 / 8, every year** |

228 of 293 scheduled meetings have a statement of their own. So "the previous
statement" is not the same object across the sample: in 1996 it can be from
fourteen months earlier. Across the 233-meeting backtest window a prior
statement always exists, but it is **the immediately preceding meeting's** only
211 times:

| era | prior statement is the previous meeting |
|---|---|
| 1997–2007 | 65 / 84 |
| 2008–2015 | 62 / 64 |
| 2016–today | 84 / 85 |

Median age of the prior statement is 43 days; the maximum is **553**. The three
misses after 2008 are correct behaviour, not gaps — the most recent statement
before the April 2020 meeting genuinely was the March emergency one.

### The SEP and the dot plot

**The dot plot does not reach 1990, and it does not even reach the start of the
SEP.** Three facts, each measured:

* The SEP itself begins **October 2007**, quarterly.
* The 2007–2011 SEPs exist only as `FOMC{date}SEPcompilation.pdf`, a scan — and,
  decisively, **they contain no interest-rate projections at all.** The Board's
  own FAQ: *"Since January 2012, the economic projections have also included
  information about policymakers' projections of the appropriate level of the
  target federal funds rate."* There is no dot plot in them to parse.
* The machine-readable table `fomcprojtabl{YYYYMMDD}.htm` therefore starts at
  **2012-01-25** and runs to today: **57 SEPs**.

The rolling calendar links that table; the historical year pages link only the
PDF. So `index.py` probes the documented URL at all 145 meeting dates back to
2007 and records the 404s — the "nothing before 2012" claim is a measurement,
not a reading of the FAQ.

What is parsed is the dot **distribution**, not a summary line: the Board
publishes "Midpoint of target range or target level (Percent)" against "Number
of participants" per horizon year, which is the individual dots in count form.
Medians and dispersion are computed from the participants. The test suite checks
**195 computed medians against the Board's own published median row** and they
agree to within one rounding step; the only disagreements are exact `.25` ties
where the Board rounds half-to-even and this code rounds half-up.

---

## Coverage against the 233-meeting backtest

`1997-08-19 → 2026-09-16`, the same window as the published scorecard.

| column | covered | 1997–2007 | 2008–2015 | 2016–today |
|---|---|---|---|---|
| `stmt_words`, `stmt_words_chg`, `stmt_age_days` | 233/233 | 84/84 | 64/64 | 85/85 |
| `stmt_diff_*` (the redline, 4 columns) | 233/233 | 84/84 | 64/64 | 85/85 |
| `stmt_hawk_net_per_1k` | 233/233 | 84/84 | 64/64 | 85/85 |
| `stmt_tone` | 231/233 | 83/84 | 63/64 | 85/85 |
| `stmt_tone_chg` | 229/233 | 82/84 | 62/64 | 85/85 |
| `stmt_dissent_*` (4 columns) | **192/233** | 45/84 | 63/64 | 84/85 |
| `stmt_risk_tilt` | **153/233** | 57/84 | 43/64 | 53/85 |
| `stmt_risk_tilt_chg` | **144/233** | 54/84 | 40/64 | 50/85 |
| `sep_*` (8 columns) | **116/233** | **0/84** | 31/64 | 85/85 |

Three of those are structural and no amount of care moves them:

* **Dissents enter the statement in March 2002.** Before that the roll call was
  published in the minutes, three weeks late. The column is `None` for those
  meetings, never `0` — encoding it as zero would teach the model that the
  Greenspan Committee was unanimous for eight years.
* **The balance-of-risks sentence is a regime, not a constant.** It was formal
  and explicit from February 2000 to roughly 2007 ("the risks are weighted
  mainly toward conditions that may generate heightened inflation pressures"),
  survived in weaker forms through 2019, and since 2020 most statements carry no
  classifiable tilt at all. 153 of 233 is what an honest phrase classifier gets;
  anything higher would be inventing a tilt where the Committee wrote none.
* **The dots cover half the sample and none of the first decade.**

---

## What the columns are worth

Correlation with the actual decision in basis points, same 233 rows.

`r_prev` is the correlation with the **previous** decision. It is the column
that decides whether any of this is new information: the Fed moves in runs,
*repeat the last action* is already a 78.97% baseline in this repo, and the
previous statement is, among other things, a description of the previous
decision. `r_partial` is what survives once that persistence is removed — and
the engine already carries `last_dir` and `run_length`, so **only `r_partial`
is a candidate for adding anything.**

| column | n | r | r on moves | r_prev | **r_partial** |
|---|---|---|---|---|---|
| `sep_dot_y0_chg` | 114 | 0.621 | 0.699 | 0.605 | **0.370** |
| `sep_dot_minus_mid` | 116 | 0.466 | 0.779 | 0.321 | **0.357** |
| `sep_dot_y1_chg` | 114 | 0.582 | 0.741 | 0.540 | **0.356** |
| `stmt_risk_tilt_chg` | 144 | 0.283 | 0.425 | 0.103 | **0.288** |
| `stmt_hawk_net_per_1k` | 233 | 0.529 | 0.784 | 0.612 | 0.209 |
| `stmt_diff_changed_frac` | 233 | −0.194 | −0.343 | −0.077 | −0.191 |
| `stmt_diff_frac_chg` | 233 | −0.114 | −0.208 | 0.026 | −0.176 |
| `stmt_risk_tilt` | 153 | 0.396 | 0.646 | 0.439 | 0.157 |
| `stmt_dissent_net` | 192 | 0.124 | 0.186 | 0.032 | 0.137 |
| `stmt_tone` | 231 | 0.496 | 0.770 | 0.635 | 0.131 |
| `stmt_dissent_hawk` | 192 | 0.008 | −0.023 | −0.117 | 0.114 |
| `stmt_words` | 233 | 0.063 | 0.276 | −0.027 | 0.108 |
| `stmt_words_chg` | 233 | −0.069 | −0.155 | −0.210 | 0.095 |
| `stmt_diff_removed` | 233 | −0.092 | −0.203 | −0.038 | −0.090 |
| `stmt_dissent_dove` | 192 | −0.208 | −0.351 | −0.240 | −0.068 |
| `sep_disp_y0` | 116 | −0.024 | −0.287 | 0.013 | −0.044 |
| `sep_dot_y0` | 116 | 0.058 | −0.195 | 0.155 | −0.059 |
| `sep_disp_y1` | 116 | −0.024 | 0.000 | 0.043 | −0.071 |
| `sep_dot_y1` | 116 | 0.167 | 0.047 | 0.241 | 0.010 |
| `stmt_dissents` | 192 | −0.157 | −0.280 | −0.222 | −0.015 |
| `stmt_diff_added` | 233 | −0.148 | −0.363 | −0.212 | −0.010 |
| `stmt_tone_chg` | 229 | 0.011 | 0.035 | 0.209 | −0.173 |
| `stmt_age_days` | 233 | −0.036 | −0.058 | 0.195 | −0.224 |
| `sep_age_days` | 116 | 0.186 | 0.255 | −0.067 | 0.308 |

**The yardstick, on the same rows.** `ff_exp_move_bp`, the fed funds futures
column the engine already ships and the top of its fitted reaction function:

| | n | r | r_prev | r_partial |
|---|---|---|---|---|
| `ff_exp_move_bp` | 233 | **0.854** | 0.596 | **0.763** |
| the previous decision itself | 233 | 0.661 | — | — |

Nothing in this package is within a factor of two of that.

### Which of these is signal, and which is not

**Real, and worth wiring:**

* **`sep_dot_y0_chg` / `sep_dot_y1_chg` (r_partial 0.37 / 0.36).** The *change*
  in the median dot for a **fixed** target year since the previous SEP. The
  level says almost nothing (0.058); the revision says a lot. This is the
  Committee telling you it has changed its mind, three to six weeks before it
  acts on it. It survives the persistence control better than anything else
  here. Note the arithmetic is year-matched on both sides — a December SEP's
  "current year" and a March SEP's "current year" are different years, and
  differencing them would be nonsense dressed as a feature.
* **`sep_dot_minus_mid` (0.357).** The prior SEP's median for this year minus
  the target midpoint in force: how much more the Committee said it intended to
  do. Every SEP post-dates December 2008, so the midpoint is always `upper −
  0.125` and no regime branch is needed.
* **`stmt_risk_tilt_chg` (0.288).** The *change* in the balance-of-risks tilt.
  The level (0.157) is mostly persistence; the change is not — `r_prev` is 0.103,
  the lowest of any column with real correlation. A Committee that moved its
  tilt is a Committee about to move its rate. Covers 144 of 233.

**Real but small, and honest about being small:**

* **The redline, `stmt_diff_changed_frac` (−0.191).** Full coverage, almost no
  overlap with persistence (`r_prev` −0.077), and the sign is interpretable: a
  heavily rewritten statement precedes easing more often than tightening,
  because the Committee rewrites when it is explaining a downturn. It was the
  column I expected to be strongest — the market reads redlines, not statements —
  and it is not. A word-level diff measures *how much* changed, not *what*; the
  informative redline is three words in the forward-guidance sentence, and that
  is a semantic problem this package deliberately does not solve, because
  solving it means a model call and this is Class C.
* **`stmt_dissent_net` (0.137).** Direction-weighted dissents. Weaker than the
  folklore, and the raw count (`stmt_dissents`, −0.015) is worth nothing at all —
  it is *which way* they dissented that carries anything. 192/233 coverage.

**Not signal, and should not ship:**

* **The hawkish/dovish lexicon (`stmt_hawk_net_per_1k`, `stmt_tone`).** This is
  the finding the brief invited, so it gets said plainly. The raw correlation
  looks good — **0.529**, third-highest in the table — and it is **almost
  entirely persistence**. `r_prev` is 0.612: the lexicon score of the previous
  statement correlates with the previous *decision* more strongly than with the
  next one, because the statement's most direction-loaded sentence is the one
  announcing what the Committee just did. Partial out the previous decision and
  0.529 collapses to **0.209**, against a futures column at 0.763. Counting a
  direction word next to an object word recovers "they just hiked", which the
  matrix already knows from `last_dir` at zero cost and without a word list to
  maintain. `stmt_tone_chg` is worse than useless (−0.173). **Recommendation:
  keep the lexicon in the package as an instrument, do not wire it into the
  matrix.** The word list is checked in and cited, the arithmetic is tested, and
  the number above is the reason not to use it.
* **`stmt_age_days` (−0.224) and `sep_age_days` (0.308).** Both are artefacts,
  not information. Statement age proxies for the pre-2000 era when statements
  were rare; SEP age proxies for where in the quarter a meeting sits. Neither is
  the Committee telling you anything, and wiring a calendar artefact into a
  reaction function is how a backtest learns the shape of the sample instead of
  the shape of the policy rule.
* **Dot dispersion (`sep_disp_y0`, `sep_disp_y1`, ≈ −0.04).** A genuinely
  appealing idea — a split Committee should be a hesitant Committee — with no
  measured content at all. It is computed and reported so the idea is closed
  rather than left open.
* **`stmt_words` and `stmt_words_chg` (0.108, 0.095).** Length is a
  communications-policy variable, not a reaction-function one: 40 words in 1995
  and 880 in 2013 is a change in how the Fed talks, not in what it does.

**And the one caveat on the tone columns that is not in the table.** `stmt_tone`
is `(hawk − dove) / (hawk + dove)`, which saturates at ±1 on the 40-to-120-word
statements of the 1990s and the 2020s, where the denominator is three or four
hits. `stmt_hawk_net_per_1k` normalises by length instead and is the better of
the two. Neither is recommended.

---

## What is deliberately not here

* **Minutes text.** The minutes are indexed with their real release dates, and
  `index.load()` exposes them, so a point-in-time minutes lookup is one function
  away. No feature is derived from their body: it is 8,000 words of committee
  prose, the honest features in it are semantic, and every semantic tool is
  either a model call (not Class C) or a bigger lexicon than the one this file
  has just argued against.
* **The press conference and speeches.** Transcripts are PDFs; the Chair's tone
  is exactly the thing a counted dictionary cannot read.
* **The 2007–2011 SEPs.** Scanned PDFs with no rate projections in them. There
  is nothing to extract, so nothing is extracted.
* **Any model call, anywhere.** Class C. A hawkish/dovish score here is a
  counted lexicon with a checked-in source, and the score is reported with a
  measurement saying not to use it.

---

## Files

```
boardsite.py    HTTP + forever-cache + HTML-to-text for federalreserve.gov
index.py        the document index: statement / SEP / minutes URLs and publication dates
statements.py   the statement corpus, four page layouts, strict point-in-time lookups
sep.py          the dot plot parsed from the Board's projection table, strict lookups
lexicon.py      direction x object counting over statement language
lexicon/
  hawkish_dovish.txt   the word list. Cited method, checked in, and read the header
derive.py       the derived columns, all sourced strictly before their meeting
report.py       coverage, signal against the futures yardstick, and the leak demo
repo.py         the one place this package reads the rest of the engine (read-only)
cli.py          front door
test_text.py    the invariants, led by the leak gate
```

Cache lives in `engine/data/cache/text/`, which is already gitignored as
refetchable. A cold build is 287 documents in one polite pass; every later run
touches the network only for the index pages, which carry a 20-hour TTL.
