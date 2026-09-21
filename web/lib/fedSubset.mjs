// @ts-check
/**
 * What the dashboard reads off the FOMC engine's artifacts.
 *
 * WHAT CHANGED, AND WHY IT MATTERS
 * --------------------------------
 * This module used to DERIVE the move subset — it walked the backtest's `preds`
 * array and recomputed each baseline on the meetings where the Fed moved,
 * because `models/backtest.json` published the model's own move accuracy and no
 * baselines at all. Two other places did the same arithmetic: `engine/backtest.py`
 * for the CLI, and a downstream consumer in another repo. Three derivations of
 * one number agreed at 65.7534% / 91.7808% by luck and stopped agreeing the
 * first time the artifact was regenerated mid-session.
 *
 * The engine publishes the whole table now (`backtest.json → move_subset`), and
 * this file reads it. It does not recompute, it does not fall back to deriving
 * from `preds`, and an artifact too old to carry the block reads as "not
 * scored" rather than as a second opinion. The dashboard reports exactly what
 * the engine published, which is the property the tests here pin.
 *
 * RECALL AND ITS PRICE TRAVEL TOGETHER
 * ------------------------------------
 * Every row carries both `move_direction` (of the meetings where the Fed moved,
 * how many this caller called) and `hold_false_alarm` (of the meetings where it
 * held, how many this caller shouted "move" at). A row that arrives with one and
 * not the other is DROPPED rather than rendered, because 91.8% read without its
 * 45.6% is exactly how a trigger-happy yield spread gets mistaken for a better
 * forecaster than the model.
 *
 * DEFENSIVE BY CONTRACT. The engine's Python is under active development, so
 * every field is read with a guard: unknown keys are ignored, a missing field
 * degrades that one row or returns null for the whole read, and nothing here
 * throws on a shape it has not seen.
 */

/** @param {unknown} x */
function num(x) {
  return typeof x === "number" && Number.isFinite(x) ? x : null;
}

/** @param {unknown} x */
function int(x) {
  const v = num(x);
  return v === null ? null : Math.trunc(v);
}

/** @param {unknown} x */
function str(x) {
  return typeof x === "string" && x.length > 0 ? x : null;
}

/** @param {unknown} x */
function obj(x) {
  return x && typeof x === "object" && !Array.isArray(x) ? /** @type {any} */ (x) : null;
}

/**
 * One published scorecard row, or null if it is not a whole one.
 *
 * "Whole" is the load-bearing word: a row must carry its coverage, its recall on
 * moves AND the false-alarm rate on holds that recall cost. A partial row is not
 * rendered at all — there is no code path in this app that can show one of those
 * two numbers without the other, and dropping the row is how that is enforced
 * rather than promised.
 *
 * @param {unknown} raw
 * @returns {import("./fedSubset.d.mts").SubsetRow | null}
 */
function readRow(raw) {
  const r = obj(raw);
  if (!r) return null;
  const key = str(r.key);
  const n = int(r.n);
  const nMoves = int(r.n_moves);
  const nHolds = int(r.n_holds);
  if (key === null || n === null || nMoves === null || nHolds === null) return null;

  const move = num(r.move_direction);
  const falseAlarm = num(r.hold_false_alarm);
  // Both, or neither. A caller that covered no moves legitimately has null for
  // both; a caller carrying recall alone is a malformed row.
  if ((move === null) !== (falseAlarm === null)) return null;

  return {
    key,
    label: str(r.label) ?? key,
    n,
    n_moves: nMoves,
    n_holds: nHolds,
    all_direction: num(r.all_direction),
    move_direction: move,
    hold_false_alarm: falseAlarm,
    move_5class: num(r.move_5class),
  };
}

/** @param {unknown} raw */
function readTable(raw) {
  if (!Array.isArray(raw)) return [];
  /** @type {import("./fedSubset.d.mts").SubsetRow[]} */
  const out = [];
  for (const r of raw) {
    const row = readRow(r);
    if (row) out.push(row);
  }
  return out;
}

/**
 * The like-for-like table: every caller rescored on the meetings the futures
 * strip actually reaches, so the futures column is not compared against
 * baselines scored over a larger denominator.
 *
 * @param {unknown} raw
 * @returns {import("./fedSubset.d.mts").LikeForLike | null}
 */
function readLikeForLike(raw) {
  const l = obj(raw);
  if (!l) return null;
  const rows = readTable(l.rows);
  const n = int(l.n);
  if (rows.length === 0 || n === null) return null;
  return {
    basis: str(l.basis) ?? "market_futures",
    n,
    n_moves: int(l.n_moves) ?? 0,
    n_holds: int(l.n_holds) ?? 0,
    rows,
  };
}

/**
 * The move subset, exactly as the engine published it.
 *
 * Accepts the parsed `backtest.json` (the normal call) or the `move_subset`
 * block on its own. It does NOT accept a `preds` array: deriving the subset here
 * is the thing that was removed, and silently falling back to it would restore
 * the drift this change exists to end.
 *
 * @param {unknown} artifact parsed `models/backtest.json`, or its `move_subset` block
 * @returns {import("./fedSubset.d.mts").MoveSubset | null} null when the engine published none
 */
export function moveSubset(artifact) {
  const top = obj(artifact);
  if (!top) return null;
  const block = obj(top.move_subset) ?? (Array.isArray(top.rows) ? top : null);
  if (!block) return null;

  const rows = readTable(block.rows);
  if (rows.length === 0) return null;

  /** @param {string} key */
  const by = (key) => rows.find((r) => r.key === key) ?? null;
  const model = by("model");
  const proxy = by("market_proxy");
  // The two figures the page is built around. Without both, there is no
  // comparison to draw and "not scored" is the honest render.
  if (!model || model.move_direction === null) return null;
  if (!proxy || proxy.move_direction === null) return null;

  const momentum = by("repeat_last");
  const hold = by("always_hold");

  return {
    n_moves: int(block.n_moves) ?? model.n_moves,
    n_holds: int(block.n_holds) ?? model.n_holds,
    n: int(block.n) ?? model.n,
    futures_band_bp: num(block.futures_band_bp),
    note: str(block.note),
    rows,
    like_for_like: readLikeForLike(block.like_for_like),

    // Named accessors for the figures the page quotes in prose. Each one is the
    // published number passed through — nothing here is computed.
    model: model.move_direction,
    model_false_alarm: model.hold_false_alarm,
    curve_proxy: proxy.move_direction,
    curve_proxy_false_alarm: proxy.hold_false_alarm,
    momentum: momentum?.move_direction ?? null,
    always_hold: hold?.move_direction ?? 0,
    /** The real fed funds futures baseline, whole row, or null if not published. */
    futures: by("market_futures"),
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
