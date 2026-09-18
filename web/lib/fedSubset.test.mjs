// @ts-check
// Run: node --test lib/fedSubset.test.mjs
//
// The first block is a straight port of the fixture in
// `os/auctus/rates/test_rates.py::_record` — same five rows, same expected
// splits (model 1/3, momentum 2/3, curve proxy 3/3). It is duplicated here on
// purpose: /fed and /rates now both quote move-subset baselines, and if the
// TypeScript read ever drifts from `differential.py::subset_baselines` the two
// dashboards will print different numbers off the same backtest.json. This test
// is the thing that fails first when that happens.
//
// The second block is the defensive contract. The FOMC engine's Python is under
// active development, so these assert that new keys are ignored, missing keys
// degrade rather than throw, and a shape we have never seen returns null.
import { test } from "node:test";
import assert from "node:assert/strict";
import { calibrationRead, coefficients, moveSubset } from "./fedSubset.mjs";

/** The `os/auctus/rates/test_rates.py` fixture, row for row. */
const PREDS = [
  // 3 moves: model 1/3, momentum 2/3, curve proxy 3/3.
  { actual: 1, pred: 0, prev_label: 1, mkt_1y: 0.9 },
  { actual: -1, pred: -1, prev_label: -1, mkt_1y: -0.9 },
  { actual: 1, pred: -1, prev_label: 0, mkt_1y: 0.9 },
  // holds, which must not enter any move-subset figure
  { actual: 0, pred: 0, prev_label: 0, mkt_1y: 0.0 },
  { actual: 0, pred: 0, prev_label: 0, mkt_1y: 0.0 },
];

const near = (/** @type {number} */ a, /** @type {number} */ b) =>
  assert.ok(Math.abs(a - b) < 1e-9, `${a} !== ${b}`);

test("holds never enter a move-subset figure", () => {
  const s = moveSubset(PREDS);
  assert.ok(s);
  assert.equal(s.n_moves, 3);
});

test("the three baselines match differential.py on the shared fixture", () => {
  const s = /** @type {NonNullable<ReturnType<typeof moveSubset>>} */ (moveSubset(PREDS));
  near(s.model, 1 / 3);
  near(s.momentum, 2 / 3);
  near(s.curve_proxy, 1);
});

test("always-hold scores exactly zero on moves, by construction", () => {
  const s = /** @type {NonNullable<ReturnType<typeof moveSubset>>} */ (moveSubset(PREDS));
  near(s.always_hold, 0);
});

test("the curve proxy's dead band is +/-0.15, not sign(mkt_1y)", () => {
  // Inside the band the curve is not calling a move. If the band were dropped,
  // a +0.10 spread would score as a correct hike call and the proxy's number
  // would be inflated — which is the direction of error that flatters us.
  const s = /** @type {NonNullable<ReturnType<typeof moveSubset>>} */ (
    moveSubset([{ actual: 1, pred: 1, prev_label: 0, mkt_1y: 0.1 }])
  );
  near(s.curve_proxy, 0);
  near(s.model, 1);
});

test("a missing mkt_1y reads as no call, not as a crash", () => {
  const s = /** @type {NonNullable<ReturnType<typeof moveSubset>>} */ (
    moveSubset([{ actual: 1, pred: 1, prev_label: 1 }])
  );
  near(s.curve_proxy, 0);
  near(s.momentum, 1);
});

test("50bp moves count as one directional move, not two", () => {
  // `actual` is an ordinal class (-2..2), so sign() — not equality — is what
  // makes a cut50 and a cut25 both count as "the Fed eased".
  const s = /** @type {NonNullable<ReturnType<typeof moveSubset>>} */ (
    moveSubset([{ actual: -2, pred: -1, prev_label: -2, mkt_1y: -0.9 }])
  );
  near(s.model, 1);
  near(s.curve_proxy, 1);
});

test("no moves, no preds, and junk all return null rather than 0%", () => {
  // A subset of zero rows must not render as "the model got 0% right".
  assert.equal(moveSubset([{ actual: 0, pred: 0, prev_label: 0, mkt_1y: 0 }]), null);
  assert.equal(moveSubset([]), null);
  assert.equal(moveSubset(null), null);
  assert.equal(moveSubset({ preds: [] }), null);
});

test("unknown keys from a newer engine are ignored, not fatal", () => {
  const s = /** @type {NonNullable<ReturnType<typeof moveSubset>>} */ (
    moveSubset([
      { actual: 1, pred: 1, prev_label: 1, mkt_1y: 0.9, futures_implied: 0.8, ff1: 4.25 },
    ])
  );
  near(s.model, 1);
  near(s.curve_proxy, 1);
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
