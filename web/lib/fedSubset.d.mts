/**
 * Type surface for `fedSubset.mjs` — the move-subset scorecard as the engine
 * published it, the calibration read, and the fitted reaction function. All of
 * it comes off the engine's artifacts; none of it is a second model, and since
 * the engine started publishing the move subset, none of it is a second
 * derivation either.
 */

/** One calibration bucket that actually has meetings in it. */
export interface LiveBin {
  /** The bucket label as the engine wrote it, e.g. "0-20%". */
  bin: string;
  /** Meetings that landed in this bucket. Always > 0 here. */
  n: number;
  /** Mean P(move) the model assigned inside the bucket. Never null here. */
  predicted: number | null;
  /** Share of those meetings that actually moved. Never null here. */
  actual: number | null;
}

/**
 * The reliability read: how close predicted probability sits to realised
 * frequency. `gap_weighted` is the n-weighted mean absolute deviation (expected
 * calibration error); `gap_max` is the worst single bucket, named by `worst_bin`.
 */
export interface CalibrationRead {
  n: number;
  bins: LiveBin[];
  gap_weighted: number;
  gap_max: number;
  worst_bin: string;
}

/**
 * One caller on the engine's scorecard — the model or a baseline.
 *
 * `move_direction` and `hold_false_alarm` are recall and what recall cost, and
 * they are never apart: a row that arrives carrying one and not the other is
 * dropped by the reader rather than rendered. Both are null together only when
 * the caller covered nothing.
 *
 * `n` is coverage, and it is not the same for every row: the fed funds futures
 * baseline is null where the strip does not reach the meeting, so it is scored
 * on fewer meetings than the others and says so here.
 */
export interface SubsetRow {
  /** Stable identifier: model | always_hold | repeat_last | market_proxy | market_futures. */
  key: string;
  /** The engine's own label for the row. */
  label: string;
  /** Meetings this caller covered at all. */
  n: number;
  n_moves: number;
  n_holds: number;
  /** Direction accuracy over every meeting it covered. */
  all_direction: number | null;
  /** Recall: of the meetings where the Fed moved, the share it called. */
  move_direction: number | null;
  /** The price of that recall: of the holds, the share it cried "move" at. */
  hold_false_alarm: number | null;
  /** Exact 25/50bp size on moves. Only a 5-class predictor has one. */
  move_5class: number | null;
}

/** Every caller rescored on exactly the meetings the futures strip reaches. */
export interface LikeForLike {
  basis: string;
  n: number;
  n_moves: number;
  n_holds: number;
  rows: SubsetRow[];
}

/**
 * The move subset, as `engine/backtest.py` published it. Fractions in [0,1].
 *
 * The named fields are pass-throughs for the figures the page quotes in prose;
 * `rows` is the whole table and is what the scorecard renders, so a baseline
 * added on the engine side appears without a change here.
 */
export interface MoveSubset {
  /** Meetings scored in total. */
  n: number;
  n_moves: number;
  n_holds: number;
  /** The band past which the futures strip counts as calling a move, in bp. */
  futures_band_bp: number | null;
  /** The engine's own note on why recall and false alarms are one record. */
  note: string | null;
  rows: SubsetRow[];
  like_for_like: LikeForLike | null;

  /** The model's recall on moves. */
  model: number;
  /** What that recall cost it on the holds. */
  model_false_alarm: number | null;
  /** The 1y-minus-funds curve proxy's recall on moves. */
  curve_proxy: number;
  /** What that recall cost IT on the holds — the number that reframes the first. */
  curve_proxy_false_alarm: number | null;
  /** Repeat-the-last-action, on moves. */
  momentum: number | null;
  /** 0 by construction, carried anyway: its ~69% all-meeting score is worth nothing here. */
  always_hold: number;
  /** The real fed funds futures baseline, whole row, or null if not published. */
  futures: SubsetRow | null;
}

/** One fitted coefficient. Positive β pushes toward tightening. */
export interface Coefficient {
  feature: string;
  beta: number;
}

export function moveSubset(artifact: unknown): MoveSubset | null;
export function calibrationRead(bins: unknown): CalibrationRead | null;
export function coefficients(current: unknown, top?: number): Coefficient[] | null;
