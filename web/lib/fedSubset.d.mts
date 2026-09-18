/**
 * Type surface for `fedSubset.mjs` — the move-subset baselines, the calibration
 * read, and the fitted reaction function, all derived from the FOMC engine's
 * artifacts rather than from a second model.
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
 * Every direction baseline recomputed on the meetings where the Fed moved.
 * Fractions in [0,1]. `always_hold` is 0 by construction and is carried anyway.
 */
export interface MoveSubset {
  n_moves: number;
  model: number;
  momentum: number;
  curve_proxy: number;
  always_hold: number;
}

/** One fitted coefficient. Positive β pushes toward tightening. */
export interface Coefficient {
  feature: string;
  beta: number;
}

export function moveSubset(preds: unknown): MoveSubset | null;
export function calibrationRead(bins: unknown): CalibrationRead | null;
export function coefficients(current: unknown, top?: number): Coefficient[] | null;
