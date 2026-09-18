// @ts-check
/**
 * Move-subset baselines and the calibration read, derived from the FOMC
 * backtest's per-meeting `preds` rows.
 *
 * WHY THIS EXISTS
 * ---------------
 * `models/backtest.json` publishes its baselines over ALL 233 scored meetings.
 * The Fed holds ~69% of them, so every all-meeting figure is dominated by the
 * holds: `baseline_always_hold` is 0.691 and the model's 0.833 direction
 * accuracy looks like a win. On the 72 meetings where something actually
 * happened, always-hold is worth exactly 0% and the ordering inverts — the free
 * 1y-minus-effective-funds spread calls direction on 91.7% of moves against the
 * engine's 65.3%. That is the number a desk would ask for first, and it is not
 * in the artifact.
 *
 * CANONICAL DEFINITION
 * --------------------
 * This is a deliberate port of `os/auctus/rates/differential.py::subset_baselines`,
 * which already computes exactly these figures for the /rates gate. Both must
 * agree or the two dashboards will quote different numbers off the same file,
 * so the sign rule and the curve-proxy dead band are reproduced verbatim:
 *
 *   sign(x)      = (x > 0) - (x < 0)
 *   proxy_call(p) = +1 if mkt_1y >  0.15
 *                   -1 if mkt_1y < -0.15
 *                    0 otherwise            (and 0 when mkt_1y is missing)
 *
 * `fedSubset.test.mjs` pins them against the same fixture as
 * `os/auctus/rates/test_rates.py`, so a change on either side fails a test here.
 *
 * DEFENSIVE BY CONTRACT. The engine's Python is under active development (a
 * futures feature module is being added), so every field is read with a guard:
 * unknown keys are ignored, a missing field degrades that one row or returns
 * null for the whole read, and nothing here throws on a shape it has not seen.
 */

/** @param {unknown} x */
function num(x) {
  return typeof x === "number" && Number.isFinite(x) ? x : null;
}

/** @param {number} x */
function sign(x) {
  return (x > 0 ? 1 : 0) - (x < 0 ? 1 : 0);
}

/**
 * The curve proxy's directional call for one meeting.
 *
 * `mkt_1y` is the 1y Treasury minus effective funds. The ±0.15 dead band is the
 * gate's, not ours: inside it the curve is not calling a move, and collapsing
 * that to "hold" is what makes the comparison fair rather than flattering.
 *
 * @param {Record<string, unknown>} p
 */
function proxyCall(p) {
  const m = num(p.mkt_1y);
  if (m === null) return 0;
  return m > 0.15 ? 1 : m < -0.15 ? -1 : 0;
}

/**
 * Every baseline recomputed on the meetings where the Fed actually moved.
 *
 * Returns fractions in [0,1] to match the rest of `backtest.json` (the Python
 * gate returns percentages; that is the only difference, and it is stated here
 * rather than left for someone to trip over).
 *
 * @param {unknown} preds the backtest's `preds` array, or anything at all
 * @returns {import("./fedSubset.d.mts").MoveSubset | null} null when there is nothing to score
 */
export function moveSubset(preds) {
  if (!Array.isArray(preds) || preds.length === 0) return null;

  const moves = preds.filter(
    (p) => p && typeof p === "object" && num(/** @type {any} */ (p).actual) !== null &&
      /** @type {any} */ (p).actual !== 0,
  );
  if (moves.length === 0) return null;

  /** @param {(p: any) => boolean} fn */
  const acc = (fn) => moves.filter((p) => { try { return fn(p); } catch { return false; } }).length / moves.length;

  return {
    n_moves: moves.length,
    // The engine, on moves only. `move_accuracy_direction` in the artifact —
    // recomputed here so all five figures come off the same 72 rows.
    model: acc((p) => sign(num(p.pred) ?? 0) === sign(num(p.actual) ?? 0)),
    // Repeat the last action.
    momentum: acc((p) => sign(num(p.prev_label) ?? 0) === sign(num(p.actual) ?? 0)),
    // What the market had already priced, for free.
    curve_proxy: acc((p) => proxyCall(p) === sign(num(p.actual) ?? 0)),
    // 0% by construction. Stated, not omitted: its ~69% all-meeting score is the
    // number that makes the engine look good and it is worth nothing here.
    always_hold: acc((p) => sign(num(p.actual) ?? 0) === 0),
  };
}

/**
 * The reliability read over the backtest's calibration bins.
 *
 * The README's standard is "tell you 30% when it is 30%". That is one number:
 * how far the realised frequency sits from the predicted one, weighted by how
 * many meetings landed in each bin. `gap_weighted` is the n-weighted mean
 * absolute deviation — the expected calibration error — and `gap_max` is the
 * worst single bin, because a small average can hide one badly broken bucket.
 *
 * @param {unknown} bins the backtest's `calibration` array
 * @returns {import("./fedSubset.d.mts").CalibrationRead | null}
 */
export function calibrationRead(bins) {
  if (!Array.isArray(bins)) return null;

  const live = bins
    .filter((b) => b && typeof b === "object")
    .map((b) => ({
      bin: typeof /** @type {any} */ (b).bin === "string" ? /** @type {any} */ (b).bin : "?",
      n: num(/** @type {any} */ (b).n) ?? 0,
      predicted: num(/** @type {any} */ (b).predicted),
      actual: num(/** @type {any} */ (b).actual),
    }))
    .filter((b) => b.n > 0 && b.predicted !== null && b.actual !== null);

  if (live.length === 0) return null;

  const n = live.reduce((s, b) => s + b.n, 0);
  if (n <= 0) return null;

  let weighted = 0;
  let max = 0;
  /** @type {string} */
  let worst = live[0].bin;
  for (const b of live) {
    const gap = Math.abs(/** @type {number} */ (b.actual) - /** @type {number} */ (b.predicted));
    weighted += (b.n / n) * gap;
    if (gap > max) {
      max = gap;
      worst = b.bin;
    }
  }

  return { n, bins: live, gap_weighted: weighted, gap_max: max, worst_bin: worst };
}

/**
 * The fitted reaction function, biggest absolute coefficient first.
 *
 * Read straight out of `models/current.json` rather than shelling out to
 * `cli.py coefficients --json`, which would cost a second Python process for
 * two arrays already sitting in a file the loader opens anyway. Sign convention
 * is the model's: the latent is policy pressure, so a positive β means a higher
 * value of that feature pushes toward tightening.
 *
 * @param {unknown} current parsed `models/current.json`
 * @param {number} top
 * @returns {import("./fedSubset.d.mts").Coefficient[] | null}
 */
export function coefficients(current, top = 18) {
  const model = current && typeof current === "object" ? /** @type {any} */ (current).model : null;
  if (!model || typeof model !== "object") return null;

  const cols = Array.isArray(model.columns) ? model.columns : null;
  const beta = Array.isArray(model.beta) ? model.beta : null;
  if (!cols || !beta) return null;

  /** @type {import("./fedSubset.d.mts").Coefficient[]} */
  const out = [];
  for (let i = 0; i < Math.min(cols.length, beta.length); i++) {
    const b = num(beta[i]);
    if (typeof cols[i] !== "string" || b === null) continue;
    out.push({ feature: cols[i], beta: b });
  }
  if (out.length === 0) return null;

  out.sort((a, b) => Math.abs(b.beta) - Math.abs(a.beta));
  return out.slice(0, top);
}
