/**
 * Data layer for the FOMC engine (`../engine`).
 *
 * One loader, used by both the /fed page (server-rendered first paint) and
 * /api/fed (client polling). Shelling out to the Python CLI rather than porting
 * the ordered-logit model to TypeScript is deliberate: one implementation of the
 * reaction function, so the cockpit and the CLI can never disagree about what
 * the Fed is going to do.
 *
 * Class C — no model call, no spend. FRED_API_KEY stays inside the Python
 * process; nothing user-controlled reaches the shell (the engine takes no input,
 * it reads the next meeting off the scraped calendar).
 *
 * READING THE ARTIFACTS DEFENSIVELY. The engine's Python is under active
 * development. Every artifact here is parsed as `unknown` and narrowed field by
 * field: an unrecognised key is ignored, a missing field degrades to null, and a
 * malformed file renders as "never built" rather than throwing a 500. The one
 * thing that must never happen is a half-written artifact reading as a result —
 * backtests have been killed mid-write on this box.
 */
import fs from "node:fs/promises";
import path from "node:path";
import { calibrationRead, coefficients, moveSubset } from "./fedSubset.mjs";
import type {
  CalibrationRead,
  Coefficient,
  MoveSubset,
} from "./fedSubset.d.mts";

/**
 * Where the Python engine lives, relative to the repo root.
 *
 * The web app runs with its cwd at `web/`, so the repo root is one level up.
 * FOMC_ENGINE_ROOT overrides it for the case where the dashboard is served
 * from somewhere other than this checkout.
 */
export const FOMC_DIR = "engine";

function repoRoot(): string {
  return process.env.FOMC_ENGINE_ROOT || path.resolve(process.cwd(), "..");
}

export type { CalibrationRead, Coefficient, MoveSubset };

export interface Driver {
  feature: string;
  value: number | null;
  z: number;
  beta: number;
  contribution: number;
}

export interface Forecast {
  meeting: string;
  start?: string;
  sep: boolean;
  days_away: number;
  current_target_upper: number | null;
  probs: Record<string, number>;
  p_cut: number;
  p_hold: number;
  p_hike: number;
  expected_bp: number;
  implied_target_upper: number | null;
  drivers_tightening: Driver[];
  drivers_easing: Driver[];
  model: { trained: string; n_meetings: number; train_to: string };
}

/**
 * One row of `cli.py path`. Structurally a Forecast plus the engine's own
 * `conditional_on_today` stamp — which predict.py sets on every row past the
 * first and which this app treats as load-bearing, not decorative.
 */
export interface PathRow extends Forecast {
  conditional_on_today?: boolean;
}

export interface Score {
  n: number;
  from: string;
  to: string;
  accuracy_5class: number;
  accuracy_direction: number;
  mae_bp: number;
  log_loss: number;
  brier: number;
  baseline_always_hold: number;
  baseline_market_direction: number;
  baseline_persistence: number;
  n_moves: number;
  move_accuracy_direction: number;
  move_accuracy_5class: number;
  false_alarm_rate: number;
}

export interface CalibrationBin {
  bin: string;
  n: number;
  predicted: number | null;
  actual: number | null;
}

export interface Decision {
  date: string;
  delta_bp: number;
  label_name: string;
  target_before: number;
  target_after: number;
  scheduled: boolean;
}

export interface FedBundle {
  forecast: Forecast | null;
  score: Score | null;
  calibration: CalibrationBin[] | null;
  /** The reliability summary over those bins: n-weighted error and worst bucket. */
  reliability: CalibrationRead | null;
  /** Every direction baseline recomputed on the meetings where the Fed moved. */
  moves: MoveSubset | null;
  /** The fitted reaction function, signed, biggest |β| first. */
  coefficients: Coefficient[] | null;
  history: Decision[];
  ablation: { label: string; accuracy_direction: number; log_loss: number }[] | null;
  missing: string | null;
  asOf: string;
}

async function readJson<T>(repo: string, rel: string): Promise<T | null> {
  try {
    return JSON.parse(await fs.readFile(path.join(repo, rel), "utf8")) as T;
  } catch {
    // A missing artifact is a real state ("never built"), not an error the
    // caller has to handle — the UI renders the gap and names the command.
    return null;
  }
}

/**
 * Run one FOMC CLI verb and parse its JSON.
 *
 * `execFile` with an argv array — nothing is interpolated into a shell, and the
 * only caller-supplied value is an integer meeting count that is clamped below.
 */
async function runCli<T>(repo: string, args: string[], timeoutMs: number): Promise<T | null> {
  try {
    const { execFile } = await import("child_process");
    const { promisify } = await import("util");
    const execFileAsync = promisify(execFile);
    const { stdout } = await execFileAsync(
      "python",
      [path.join(repo, FOMC_DIR, "cli.py"), ...args],
      {
        cwd: path.join(repo, FOMC_DIR),
        timeout: timeoutMs,
        maxBuffer: 8 * 1024 * 1024,
      },
    );
    return JSON.parse(stdout) as T;
  } catch {
    return null;
  }
}

/**
 * A tiny in-process TTL cache, shared by the page and the API route.
 *
 * It lives here rather than in the route because the page calls `getFed()`
 * directly (it is `force-dynamic`, so the first paint is the real number, not a
 * spinner) and so never touched the route's cache at all — every page load was
 * spawning Python. The prediction is deterministic given the cached FRED series
 * and those refresh at most daily, so a shared 10-minute window is free.
 */
function memo<T>(ttlMs: number, load: () => Promise<T>, worthCaching: (v: T) => boolean) {
  let cache: { at: number; value: T } | null = null;
  let inflight: Promise<T> | null = null;
  return async (): Promise<T> => {
    if (cache && Date.now() - cache.at < ttlMs) return cache.value;
    if (!inflight) {
      inflight = load()
        .then((value) => {
          // A FAILED READ IS NEVER CACHED. The engine's Python is under active
          // development, so `cli.py predict` failing is a transient state, not a
          // result. Pinning it for ten minutes would leave the cockpit reading
          // "no trained model" long after the engine came back, and the natural
          // debugging move — reload the page — would be the one thing that
          // could not fix it.
          if (worthCaching(value)) cache = { at: Date.now(), value };
          return value;
        })
        .finally(() => {
          inflight = null;
        });
    }
    return inflight;
  };
}

const TTL_MS = 10 * 60_000;

async function loadFed(): Promise<FedBundle> {
  const repo = repoRoot();

  const [forecast, backtest, current, decisions, tuning] = await Promise.all([
    runCli<Forecast>(repo, ["predict", "--json"], 60_000),
    readJson<Score & { preds?: unknown; calibration?: CalibrationBin[] }>(
      repo,
      `${FOMC_DIR}/models/backtest.json`,
    ),
    readJson<unknown>(repo, `${FOMC_DIR}/models/current.json`),
    readJson<{ decisions: Decision[] }>(repo, `${FOMC_DIR}/data/decisions.json`),
    readJson<{ ablation?: { label: string; accuracy_direction: number; log_loss: number }[] }>(
      repo,
      `${FOMC_DIR}/models/tuning.json`,
    ),
  ]);

  // The backtest artifact carries every scored meeting. The dashboard needs the
  // headline metrics, the calibration table, and the move-subset baselines
  // derived from the rows; shipping 233 prediction objects on every poll is
  // wasted bytes, so `preds` is reduced here and dropped.
  let score: Score | null = null;
  let calibration: CalibrationBin[] | null = null;
  let reliability: CalibrationRead | null = null;
  let moves: MoveSubset | null = null;
  if (backtest) {
    const { preds, calibration: cal, ...rest } = backtest;
    score = rest as Score;
    calibration = Array.isArray(cal) ? cal : null;
    reliability = calibrationRead(cal);
    moves = moveSubset(preds);
  }

  return {
    forecast,
    score,
    calibration,
    reliability,
    moves,
    coefficients: coefficients(current),
    history: (decisions?.decisions ?? []).slice(-24).reverse(),
    ablation: tuning?.ablation ?? null,
    missing: forecast
      ? null
      : "No trained model. Run `python cli.py all` in engine/.",
    asOf: new Date().toISOString(),
  };
}

export const getFed: () => Promise<FedBundle> = memo(
  TTL_MS,
  loadFed,
  // Only a bundle that actually carries a forecast is worth holding.
  (b) => b.forecast !== null,
);

/* ── the conditional path ─────────────────────────────────────────────────── */

export interface FedPath {
  rows: PathRow[];
  /** Null when the path ran; a reason to render otherwise. */
  missing: string | null;
}

/**
 * `cli.py path` — the next N meetings, every one of them scored on TODAY's data.
 *
 * This is NOT a rate-path forecast and the UI is obliged to say so on the same
 * screen. `predict.path_forecast` re-runs the full live feature row per meeting,
 * so this costs ~7s cold against ~1.4s for `predict` — which is why it is a
 * separate loader with its own cache and is streamed into the page behind a
 * Suspense boundary rather than blocking the headline.
 */
async function loadPath(n: number): Promise<FedPath> {
  const repo = repoRoot();
  // Clamp: the count is the only number that reaches argv. `cli.py` parses
  // `--n=<int>` (a bare `--n 5` binds as the boolean True and silently becomes
  // one meeting), so the `=` form is not cosmetic.
  const count = Math.max(2, Math.min(8, Math.trunc(n) || 5));
  const rows = await runCli<PathRow[]>(repo, ["path", "--json", `--n=${count}`], 90_000);
  if (!Array.isArray(rows) || rows.length === 0) {
    return { rows: [], missing: "The conditional path did not run." };
  }
  return { rows, missing: null };
}

const pathMemo = memo(
  TTL_MS,
  () => loadPath(5),
  (p) => p.rows.length > 0,
);

export function getFedPath(): Promise<FedPath> {
  return pathMemo();
}
