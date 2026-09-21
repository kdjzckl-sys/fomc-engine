"""fomc-text -- the front door for the statement / SEP data layer.

    python cli.py index        rebuild the document index from the Board's pages
    python cli.py fetch        pull every statement and SEP into the cache
    python cli.py coverage     what each derived column actually covers, by era
    python cli.py signal       what each column is worth, against the yardstick
    python cli.py leak         what using a meeting's OWN statement would score
    python cli.py features     the derived rows for the 233 backtest meetings
    python cli.py show DATE    the prior statement for DATE, and its redline
    python cli.py test         run the test suite

    --json      machine-readable
    --refresh   refetch instead of reading the cache

This package is **not wired into the design matrix.** It computes columns and
reports what they cover and what they are worth; nothing here can change a
prediction. `features.py`, `matrix.py` and `backtest.py` are untouched.

Class C: deterministic, no model calls, no keys. The only network calls are to
federalreserve.gov, cached forever.
"""

from __future__ import annotations

import json
import sys

import derive
import index as ix
import report
import repo
import sep as sep_mod
import statements as st


def _flag(name, default=None, cast=str):
    for a in sys.argv[1:]:
        if a == "--" + name:
            return True
        if a.startswith("--" + name + "="):
            return cast(a.split("=", 1)[1])
    return default


def _pct(a, b):
    return "%d/%d (%.0f%%)" % (a, b, 100.0 * a / b) if b else "0/0"


def cmd_index(as_json, refresh):
    docs = ix.refresh(refresh_http=refresh, verbose=not as_json)
    stt = sorted(d for d, r in docs.items() if "statement" in r)
    sp = sorted(d for d, r in docs.items() if "sep" in r)
    mi = sorted(d for d, r in docs.items() if "minutes" in r)
    out = {"documents": len(docs),
           "statements": {"n": len(stt), "first": stt[0], "last": stt[-1]},
           "sep": {"n": len(sp), "first": sp[0], "last": sp[-1]},
           "minutes": {"n": len(mi), "first": mi[0], "last": mi[-1],
                       "with_release_date":
                           sum(1 for d in mi if docs[d].get("minutes_published"))}}
    print(json.dumps(out, indent=1) if as_json else
          "\n".join("%-12s %s" % (k, json.dumps(v)) for k, v in out.items()))


def cmd_fetch(as_json, refresh):
    c = st.load(refresh=refresh, verbose=not as_json)
    s = sep_mod.load(refresh=refresh, verbose=not as_json)
    out = {"statements": len(c), "statement_span": [c[0].published, c[-1].published],
           "meeting_statements": sum(1 for x in c if x.kind == "meeting"),
           "seps": len(s), "sep_span": [s[0].published, s[-1].published]}
    print(json.dumps(out, indent=1))


def cmd_coverage(as_json, refresh):
    cov = report.coverage()
    if as_json:
        print(json.dumps(cov, indent=1))
        return
    ps, sp = cov["prior_statement"], cov["prior_sep"]
    print("\n%d scheduled meetings, %s -> %s (the backtest window)"
          % (cov["n"], repo.backtest_dates()[0], repo.backtest_dates()[-1]))
    print("\nupstream availability")
    print("  a prior statement exists          %s" % _pct(ps["with_prior_statement"], cov["n"]))
    print("  ...and it is the PREVIOUS meeting %s   %s"
          % (_pct(ps["is_the_previous_meeting"], cov["n"]),
             json.dumps(ps["by_era"])))
    print("  two prior statements (for a diff) %s" % _pct(ps["two_prior"], cov["n"]))
    print("  median age of the prior statement %d days (max %d)"
          % (ps["median_age_days"], ps["max_age_days"]))
    print("  a prior SEP exists                %s" % _pct(sp["with_prior_sep"], cov["n"]))
    print("\n%-26s %14s %11s %11s %11s" % ("column", "covered", *[e[0] for e in report.ERAS]))
    for k, v in cov["columns"].items():
        print("%-26s %14s %11s %11s %11s"
              % (k, _pct(v["n"], cov["n"]), *[v[e[0]] for e in report.ERAS]))


def cmd_signal(as_json, refresh):
    sig = report.signal()
    if as_json:
        print(json.dumps(sig, indent=1))
        return
    print("\nCorrelation with the actual decision (delta, bp), %d meetings." % sig["n"])
    print("r_prev is the correlation with the PREVIOUS decision; r_partial is what")
    print("survives once that persistence is removed. The engine already carries")
    print("last_dir and run_length, so only r_partial is new information.\n")
    print("%-26s %5s %8s %10s %8s %10s"
          % ("column", "n", "r", "r|moves", "r_prev", "r_partial"))
    for k, v in sig["columns"].items():
        print("%-26s %5d %8s %10s %8s %10s"
              % (k, v["n"], v["r_delta"], v["r_delta_on_moves"],
                 v["r_prev_delta"], v["r_partial"]))
    y = sig["yardstick"]
    print("\nyardstick, same rows")
    print("%-26s %5d %8s %10s %8s %10s"
          % ("ff_exp_move_bp", y["ff_exp_move_bp"]["n"], y["ff_exp_move_bp"]["r_delta"],
             "-", y["ff_exp_move_bp"]["r_prev_delta"], y["ff_exp_move_bp"]["r_partial"]))
    print("%-26s %5d %8s" % ("previous decision (bp)", y["prev_delta_bp"]["n"],
                             y["prev_delta_bp"]["r_delta"]))
    cmd_leak(False, refresh, precomputed=sig["leak"])


def cmd_leak(as_json, refresh, precomputed=None):
    lk = precomputed if precomputed is not None else report.leak_demo()
    if as_json:
        print(json.dumps(lk, indent=1))
        return
    d = lk["decision_sentence_direction"]
    print("\nthe leak, measured -- NOT a feature, and not shipped")
    print("  three regexes on the meeting's OWN statement:  %.1f%% direction (n=%d)"
          % (d["own"]["direction_accuracy"], d["own"]["n"]))
    print("  the same regexes on the PRIOR statement:       %.1f%% direction (n=%d)"
          % (d["prior"]["direction_accuracy"], d["prior"]["n"]))
    print("  always-hold on the same rows:                  %.1f%%" % d["always_hold"])
    print("  the published model scores 84.55%. A statement published at 2 p.m.")
    print("  with the decision beats it from three regexes, which is why every")
    print("  lookup in this package is `published < meeting`, never `<=`.")


def cmd_features(as_json, refresh):
    rows = derive.build()
    if as_json:
        print(json.dumps(rows, indent=1))
        return
    cols = ["stmt_date", "stmt_words_chg", "stmt_risk_tilt", "stmt_dissent_net",
            "stmt_diff_changed_frac", "sep_dot_y0", "sep_dot_y0_chg", "sep_dot_minus_mid"]
    print("%-12s %-12s %8s %5s %5s %7s %7s %7s %8s"
          % ("meeting", "prior stmt", "dwords", "risk", "diss", "diff", "dot_y0",
             "dot_chg", "dot-mid"))
    for r in rows[-24:]:
        print("%-12s %-12s %8s %5s %5s %7s %7s %7s %8s"
              % (r["date"], r.get("stmt_date", "-"),
                 *[("-" if r.get(c) is None else r[c]) for c in cols[1:]]))
    print("\n%d rows. `--json` for all %d columns."
          % (len(rows), len(derive.COLUMNS)))


def cmd_show(as_json, refresh):
    when = None
    for a in sys.argv[2:]:
        if not a.startswith("-"):
            when = a
    if not when:
        raise SystemExit("usage: python cli.py show YYYY-MM-DD")
    s1, s2 = st.latest_before(when), st.prior(when, 2)
    if not s1:
        raise SystemExit("no statement published before " + when)
    r = derive.redline(s1.text, s2.text) if s2 else None
    print("meeting        %s" % when)
    print("prior statement %s  (%s)" % (s1.published, s1.url))
    print("               %d words, tilt=%s" % (len(s1.text.split()),
                                                derive.risk_tilt(s1.text)[0]))
    if r:
        print("redline vs %s: +%d / -%d words, %.1f%% changed"
              % (s2.published, r["added"], r["removed"], 100 * r["changed_frac"]))
    print("\n" + s1.text)


def cmd_test(as_json, refresh):
    import test_text
    raise SystemExit(test_text.main())


COMMANDS = {"index": cmd_index, "fetch": cmd_fetch, "coverage": cmd_coverage,
            "signal": cmd_signal, "leak": cmd_leak, "features": cmd_features,
            "show": cmd_show, "test": cmd_test}


def main() -> int:
    cmd = (sys.argv[1] if len(sys.argv) > 1 and not sys.argv[1].startswith("-")
           else "coverage")
    if cmd not in COMMANDS:
        print(__doc__)
        return 2
    COMMANDS[cmd](bool(_flag("json", False)), bool(_flag("refresh", False)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
