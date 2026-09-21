// @ts-check
// Run: node --test lib/*.test.mjs
//
// WHAT THESE TESTS PIN, AND WHY IT IS DIFFERENT NOW
// ------------------------------------------------
// They used to check that this module's arithmetic matched a Python module that
// computed the same move-subset baselines somewhere else. That was the wrong
// property to pin: it made two independent derivations agree by testing, which
// works right up until one of them is edited. There were three derivations —
// `engine/backtest.py`, this file, and a downstream consumer — and they agreed
// at 65.7534% / 91.7808% by luck until the artifact was regenerated mid-session.
//
// The engine publishes the table now. So the property worth pinning is the one
// that was missing: THE DASHBOARD REPORTS EXACTLY WHAT THE ENGINE PUBLISHED. No
// recomputation, no fallback to deriving from `preds`, no arithmetic of our own
// that could drift. The load-bearing test is "published values win even when the
// rows sitting next to them imply something else" — if this module ever starts
// computing again, that is the test that fails.
//
// The second property is structural: recall and the false-alarm rate that bought
// it are never separable. A row carrying one and not the other is dropped rather
// than rendered, because 91.8% read without its 45.6% is how a trigger-happy
// yield spread gets mistaken for a better forecaster than the model.
import { test } from "node:test";
import assert from "node:assert/strict";
import { calibrationRead, coefficients, moveSubset } from "./fedSubset.mjs";

const near = (/** @type {number | null} */ a, /** @type {number} */ b) =>
  assert.ok(a !== null && Math.abs(a - b) < 1e-12, `${a} !== ${b}`);

/**
 * A published `move_subset` block, shaped exactly as `engine/backtest.py` writes
 * it. The move-subset figures are the fixture in `engine/test_subset.py`
 * (model 1/3, repeat-last 2/3, curve proxy 3/3), so a change to the engine's
 * arithmetic shows up in that suite and a change to this reader shows up here.
 */
function published(overrides = {}) {
  return {
    n: 5,
    n_moves: 3,
    n_holds: 2,
    futures_band_bp: 12.5,
    note: "Recall and false alarms are one record per caller.",
    rows: [
      { key: "model", label: "this model", n: 5, n_moves: 3, n_holds: 2,
        all_direction: 0.6, move_direction: 1 / 3, hold_false_alarm: 0, move_5class: 1 / 3 },
      { key: "always_hold", label: "always say hold", n: 5, n_moves: 3, n_holds: 2,
        all_direction: 0.4, move_direction: 0, hold_false_alarm: 0, move_5class: null },
      { key: "repeat_last", label: "repeat the last action", n: 5, n_moves: 3, n_holds: 2,
        all_direction: 0.8, move_direction: 2 / 3, hold_false_alarm: 0, move_5class: null },
      { key: "market_proxy", label: "market-implied (1y - funds proxy)", n: 5, n_moves: 3, n_holds: 2,
        all_direction: 1, move_direction: 1, hold_false_alarm: 0, move_5class: null },
      { key: "market_futures", label: "market-implied (fed funds futures)", n: 4, n_moves: 2, n_holds: 2,
        all_direction: 0.75, move_direction: 1, hold_false_alarm: 0.5, move_5class: null },
    ],
    like_for_like: {
      basis: "market_futures",
      n: 4,
      n_moves: 2,
      n_holds: 2,
      rows: [
        { key: "model", label: "this model", n: 4, n_moves: 2, n_holds: 2,
          all_direction: 0.75, move_direction: 0.5, hold_false_alarm: 0, move_5class: 0.5 },
        { key: "market_futures", label: "market-implied (fed funds futures)", n: 4, n_moves: 2, n_holds: 2,
          all_direction: 0.75, move_direction: 1, hold_false_alarm: 0.5, move_5class: null },
      ],
    },
    ...overrides,
  };
}

/** The whole artifact, as `models/backtest.json` parses. */
const ARTIFACT = {
  n: 5,
  accuracy_direction: 0.6,
  baseline_futures_direction: 0.75,
  baseline_futures_n: 4,
  move_subset: published(),
  preds: [
    { actual: 1, pred: 0, prev_label: 1, mkt_1y: 0.9 },
    { actual: -1, pred: -1, prev_label: -1, mkt_1y: -0.9 },
    { actual: 1, pred: -1, prev_label: 0, mkt_1y: 0.9 },
    { actual: 0, pred: 0, prev_label: 0, mkt_1y: 0.0 },
    { actual: 0, pred: 0, prev_label: 0, mkt_1y: 0.0 },
  ],
};

/* ── the property this file exists for ───────────────────────────────────── */

test("the dashboard reports exactly what the engine published", () => {
  const s = moveSubset(ARTIFACT);
  assert.ok(s);
  near(s.model, 1 / 3);
  near(s.momentum, 2 / 3);
  near(s.curve_proxy, 1);
  near(s.always_hold, 0);
  assert.equal(s.n_moves, 3);
  assert.equal(s.n_holds, 2);
});

test("published numbers win over anything the rows beside them would imply", () => {
  // The same artifact, but the engine published figures that do NOT match what
  // recomputing from `preds` would give. A reader that still derives fails here
  // and only here — which is the whole point of the test.
  const drifted = {
    ...ARTIFACT,
    move_subset: published({
      rows: published().rows.map((r) =>
        r.key === "model" ? { ...r, move_direction: 0.4242, hold_false_alarm: 0.1337 } : r,
      ),
    }),
  };
  const s = /** @type {NonNullable<ReturnType<typeof moveSubset>>} */ (moveSubset(drifted));
  near(s.model, 0.4242);
  near(s.model_false_alarm, 0.1337);
});

test("an artifact with only preds reads as not scored, never as a second opinion", () => {
  // Deriving the subset here is the thing that was removed. A pre-move_subset
  // backtest.json must render "not scored" rather than quietly producing a
  // number the CLI would disagree with.
  assert.equal(moveSubset({ preds: ARTIFACT.preds }), null);
  assert.equal(moveSubset(ARTIFACT.preds), null);
});

/* ── recall and its price are inseparable ────────────────────────────────── */

test("every rendered row carries recall AND the false alarms it cost", () => {
  const s = /** @type {NonNullable<ReturnType<typeof moveSubset>>} */ (moveSubset(ARTIFACT));
  for (const r of s.rows) {
    assert.ok("move_direction" in r && "hold_false_alarm" in r, r.key);
    assert.equal(r.move_direction === null, r.hold_false_alarm === null);
  }
  // The pair that makes the curve proxy readable: 100% of moves called, but it
  // false-alarmed on holds to get there. Both come off the same record.
  near(s.curve_proxy, 1);
  assert.notEqual(s.curve_proxy_false_alarm, null);
});

test("a row carrying recall without its false-alarm rate is dropped, not shown", () => {
  const half = published({
    rows: published().rows.map((r) =>
      r.key === "repeat_last" ? { ...r, hold_false_alarm: null } : r,
    ),
  });
  const s = /** @type {NonNullable<ReturnType<typeof moveSubset>>} */ (
    moveSubset({ move_subset: half })
  );
  assert.equal(s.rows.find((r) => r.key === "repeat_last"), undefined);
  assert.equal(s.momentum, null);
  // and the rows that were whole still render
  near(s.model, 1 / 3);
});

test("the read is null when the model or the proxy row is missing", () => {
  // With one of the two gone there is no comparison to draw, and a half-drawn
  // ladder is worse than an honest "not scored".
  for (const drop of ["model", "market_proxy"]) {
    const s = moveSubset({
      move_subset: published({ rows: published().rows.filter((r) => r.key !== drop) }),
    });
    assert.equal(s, null, drop);
  }
});

/* ── the futures baseline and its coverage ───────────────────────────────── */

test("the futures baseline arrives whole, with the coverage it was scored on", () => {
  const s = /** @type {NonNullable<ReturnType<typeof moveSubset>>} */ (moveSubset(ARTIFACT));
  assert.ok(s.futures);
  assert.equal(s.futures.n, 4); // fewer than the 5 the other rows cover
  near(s.futures.move_direction, 1);
  near(s.futures.hold_false_alarm, 0.5);
  near(s.futures_band_bp, 12.5);
});

test("a scorecard with no futures row reads null there rather than borrowing the proxy", () => {
  const s = /** @type {NonNullable<ReturnType<typeof moveSubset>>} */ (
    moveSubset({
      move_subset: published({
        rows: published().rows.filter((r) => r.key !== "market_futures"),
        like_for_like: null,
      }),
    })
  );
  assert.equal(s.futures, null);
  assert.equal(s.like_for_like, null);
  near(s.curve_proxy, 1); // the proxy is still itself, and still labelled as itself
});

test("the like-for-like table comes through with its own denominator", () => {
  const s = /** @type {NonNullable<ReturnType<typeof moveSubset>>} */ (moveSubset(ARTIFACT));
  assert.ok(s.like_for_like);
  assert.equal(s.like_for_like.n, 4);
  assert.equal(s.like_for_like.rows.length, 2);
  near(s.like_for_like.rows[0].move_direction, 0.5);
});

/* ── defensive contract ──────────────────────────────────────────────────── */

test("unknown keys from a newer engine are ignored, not fatal", () => {
  const s = /** @type {NonNullable<ReturnType<typeof moveSubset>>} */ (
    moveSubset({
      move_subset: published({ some_new_block: { anything: 1 } }),
      another_new_top_level_key: [1, 2, 3],
    })
  );
  near(s.model, 1 / 3);
});

test("junk, empties and malformed shapes all return null rather than 0%", () => {
  assert.equal(moveSubset(null), null);
  assert.equal(moveSubset(undefined), null);
  assert.equal(moveSubset({}), null);
  assert.equal(moveSubset({ move_subset: {} }), null);
  assert.equal(moveSubset({ move_subset: { rows: [] } }), null);
  assert.equal(moveSubset({ move_subset: { rows: "not an array" } }), null);
  assert.equal(moveSubset("backtest.json"), null);
});

test("the move_subset block on its own is accepted, for a caller that already unwrapped it", () => {
  const s = moveSubset(published());
  assert.ok(s);
  near(s.model, 1 / 3);
});

/* ── calibration ──────────────────────────────────────────────────────────── */

test("calibration error is n-weighted, and the worst bin is named", () => {
  const r = /** @type {NonNullable<ReturnType<typeof calibrationRead>>} */ (
    calibrationRead([
      { bin: "0-20%", n: 90, predicted: 0.1, actual: 0.1 },
      { bin: "80-100%", n: 10, predicted: 0.9, actual: 0.5 },
    ])
  );
  assert.equal(r.n, 100);
  near(r.gap_weighted, 0.04); // 0.9*0 + 0.1*0.4 — the big clean bin dominates
  near(r.gap_max, 0.4);
  assert.equal(r.worst_bin, "80-100%");
});

test("empty bins are dropped, so they cannot fake a perfect diagonal", () => {
  const r = /** @type {NonNullable<ReturnType<typeof calibrationRead>>} */ (
    calibrationRead([
      { bin: "0-20%", n: 5, predicted: 0.1, actual: 0.2 },
      { bin: "20-40%", n: 0, predicted: null, actual: null },
    ])
  );
  assert.equal(r.bins.length, 1);
  assert.equal(r.n, 5);
});

test("a calibration table with nothing scored returns null", () => {
  assert.equal(calibrationRead([{ bin: "0-20%", n: 0, predicted: null, actual: null }]), null);
  assert.equal(calibrationRead([]), null);
  assert.equal(calibrationRead(undefined), null);
});

/* ── coefficients ─────────────────────────────────────────────────────────── */

test("coefficients sort by absolute size and keep their sign", () => {
  const c = /** @type {NonNullable<ReturnType<typeof coefficients>>} */ (
    coefficients({ model: { columns: ["a", "b", "c"], beta: [0.1, -0.9, 0.4] } }, 2)
  );
  assert.deepEqual(c, [
    { feature: "b", beta: -0.9 },
    { feature: "c", beta: 0.4 },
  ]);
});

test("a columns/beta length mismatch truncates instead of emitting undefined", () => {
  // A half-written artifact is a real state here; backtests have been killed
  // mid-write on this box.
  const c = /** @type {NonNullable<ReturnType<typeof coefficients>>} */ (
    coefficients({ model: { columns: ["a", "b"], beta: [0.5] } })
  );
  assert.deepEqual(c, [{ feature: "a", beta: 0.5 }]);
});

test("a model artifact without a fitted beta returns null", () => {
  assert.equal(coefficients({ model: { columns: ["a"] } }), null);
  assert.equal(coefficients({}), null);
  assert.equal(coefficients(null), null);
});
