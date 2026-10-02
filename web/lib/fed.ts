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
import snapshot from "../data/snapshot.json";
import { calibrationRead, coefficients, moveSubset } from "./fedSubset.mjs";
import type {
  CalibrationRead,
  Coefficient,
  LikeForLike,
  MoveSubset,
  SubsetRow,
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

export type { CalibrationRead, Coefficient, LikeForLike, MoveSubset, SubsetRow };

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
  /** What the futures strip prices for this meeting. Absent on older snapshots. */
  market?: MarketRead;
  model_call?: "cut" | "hold" | "hike";
  /** Who carries the headline, decided by engine/horizon.py's measured record. */
  handoff?: Handoff;
}

/**
 * The fed funds futures read for one meeting (engine/predict.py `market_read`).
 * p_* are the FedWatch two-outcome arithmetic on `exp_move_bp` -- the same
 * quantity the scorecard grades. `options_implied` is the Atlanta Fed option
 * read and is NOT this meeting's odds; it covers the nearest option window.
 */
export interface MarketRead {
  exp_move_bp: number | null;
  source: string;
  p_cut: number | null;
  p_hold: number | null;
  p_hike: number | null;
  call: "cut" | "hold" | "hike" | null;
  options_implied: { p_cut: number | null; p_hike: number | null; note: string } | null;
}

export interface HorizonRow {
  horizon_days: number;
  n: number;
  model_direction: number | null;
  market_direction: number | null;
  model_move_direction: number | null;
  market_move_direction: number | null;
  model_false_alarm: number | null;
  market_false_alarm: number | null;
}

export interface Handoff {
  horizon_days: number;
  days_to_meeting: number;
  handoff_days: number | null;
  handoff_date: string | null;
  headline_source: "model" | "market";
  reason: string | null;
  measured: HorizonRow[] | null;
  at_horizon: HorizonRow | null;
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
  /** The 1y-minus-funds YIELD SPREAD standing in for the market. A proxy. */
  baseline_market_direction: number;
  baseline_persistence: number;
  /**
   * The real one: the CME ZQ strip through the FedWatch arithmetic, scored only
   * on the meetings it reaches. `baseline_futures_n` is that coverage and is not
   * decoration -- the strip cannot price 37 late-month meetings, and those are
   * null here rather than quietly falling back to the spread above.
   */
  baseline_futures_direction: number | null;
  baseline_futures_n: number | null;
  baseline_futures_band_bp: number | null;
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
  /**
   * Where these numbers came from.
   *
   * "live" — the Python engine ran just now against the cached FRED series.
   * "snapshot" — the engine is not reachable (no Python, no repo root: a
   * serverless host), so the bundle is the frozen `data/snapshot.json`. The UI
   * MUST render `generatedAt` alongside the forecast in that case. A frozen
   * prediction served as if it were live is the same class of error as quoting
   * an accuracy figure without its baseline.
   */
  source: "live" | "snapshot";
  /** When the snapshot was frozen. Null on a live read. */
  generatedAt: string | null;
}

/**
 * True when the Python engine is not reachable from this process.
 *
 * Set by the first failed `runCli` and read when the bundle is assembled, so a
 * bundle reports one coherent provenance instead of a live forecast stitched to
 * snapshot baselines. It does NOT gate the artifact reads in the same bundle —
 * those run concurrently with the CLI call in `Promise.all` and fall back on
 * their own missing-file path. It is a latch, never reset: a host without
 * Python does not grow one mid-process.
 */
let engineUnreachable = false;

type SnapshotArtifacts = Record<string, unknown>;

function fromSnapshot<T>(rel: string): T | null {
  const artifacts = (snapshot as { artifacts?: SnapshotArtifacts }).artifacts ?? {};
  return (artifacts[rel] as T | undefined) ?? null;
}

async function readJson<T>(repo: string, rel: string): Promise<T | null> {
  if (engineUnreachable) return fromSnapshot<T>(rel);
  try {
    return JSON.parse(await fs.readFile(path.join(repo, rel), "utf8")) as T;
  } catch {
    // A missing artifact is a real state ("never built"), not an error the
    // caller has to handle — the UI renders the gap and names the command.
    // Off this box it is not a gap but a deployment fact, so fall back to the
    // frozen artifact rather than rendering "never built" to the public.
    return fromSnapshot<T>(rel);
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
    // No Python, or no engine at this path. On this box that is a broken
    // install; on a serverless host it is the normal case. Either way the
    // frozen verb output is a better answer than null, and `source` tells the
    // page which one it got.
    engineUnreachable = true;
    const frozen = snapshotVerb<T>(args);
    return frozen;
  }
}

/**
 * The frozen output of one CLI verb.
 *
 * Only the two verbs the dashboard actually calls are stored. An unrecognised
 * verb returns null rather than guessing, so a new call site fails visibly here
 * instead of silently rendering someone else's numbers.
 */
function snapshotVerb<T>(args: string[]): T | null {
  const snap = snapshot as { forecast?: unknown; path?: unknown; watch?: unknown };
  if (args[0] === "predict") return (snap.forecast as T | undefined) ?? null;
  if (args[0] === "path") return (snap.path as T | undefined) ?? null;
  if (args[0] === "watch") return (snap.watch as T | undefined) ?? null;
  return null;
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
    readJson<
      Score & {
        preds?: unknown;
        calibration?: CalibrationBin[];
        move_subset?: unknown;
      }
    >(repo, `${FOMC_DIR}/models/backtest.json`),
    readJson<unknown>(repo, `${FOMC_DIR}/models/current.json`),
    readJson<{ decisions: Decision[] }>(repo, `${FOMC_DIR}/data/decisions.json`),
    readJson<{ ablation?: { label: string; accuracy_direction: number; log_loss: number }[] }>(
      repo,
      `${FOMC_DIR}/models/tuning.json`,
    ),
  ]);

  // The backtest artifact carries every scored meeting plus the scorecard the
  // engine computed off them. The move subset is READ here, not derived: it used
  // to be recomputed in fedSubset.mjs from `preds`, in engine/backtest.py for the
  // CLI, and a third time by a downstream consumer, and three derivations of one
  // number drift. Shipping 233 prediction objects on every poll is wasted bytes,
  // so `preds` is dropped once the reader has had the artifact, and `move_subset`
  // is dropped from `score` because `moves` already carries it in full.
  let score: Score | null = null;
  let calibration: CalibrationBin[] | null = null;
  let reliability: CalibrationRead | null = null;
  let moves: MoveSubset | null = null;
  if (backtest) {
    const cal = backtest.calibration;
    // Everything except the three blocks that are either huge or already
    // represented elsewhere in the bundle. Built by omission rather than by
    // listing the keepers, so a metric the engine adds reaches the page without
    // a change here.
    const rest = { ...backtest } as Record<string, unknown>;
    delete rest.preds;
    delete rest.calibration;
    delete rest.move_subset;
    score = rest as unknown as Score;
    calibration = Array.isArray(cal) ? cal : null;
    reliability = calibrationRead(cal);
    // The whole artifact, because the scorecard is what is read. An artifact too
    // old to carry `move_subset` returns null here and the page renders "not
    // scored" -- which is the honest state, and better than a second derivation
    // that can disagree with the CLI.
    moves = moveSubset(backtest);
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
    source: engineUnreachable ? "snapshot" : "live",
    generatedAt: engineUnreachable ? snapshotGeneratedAt() : null,
  };
}

/** When `data/snapshot.json` was frozen, or null if the file carries no stamp. */
function snapshotGeneratedAt(): string | null {
  const at = (snapshot as { generated_at?: unknown }).generated_at;
  return typeof at === "string" ? at : null;
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

/* ── the CME watch ────────────────────────────────────────────────────────── */

export interface WatchRange {
  lower: number;
  upper: number;
  label: string;
  change_bp: number;
  p: number;
}

/** One meeting of `cli.py watch` (engine/watch.py). */
export interface WatchRow {
  meeting: string;
  sep: boolean;
  effective: string;
  days_away: number;
  contract: string;
  contract_price: number;
  quote_date: string;
  stale: boolean;
  method: string;
  next_month_has_meeting: boolean;
  rate_in: number;
  implied_effr: number;
  move_bp: number;
  cum_bp: number;
  implied_target_upper: number;
  this_meeting: { p_cut: number; p_hold: number; p_hike: number };
  vs_today: { p_lower: number; p_unchanged: number; p_higher: number };
  ranges: WatchRange[];
  most_likely: string;
}

export interface WatchCheck {
  window: string;
  meeting: string;
  options_p_higher: number | null;
  options_p_lower: number | null;
  futures_p_higher: number;
  futures_p_lower: number;
}

export interface Watch {
  as_of: string;
  current_target_upper: number | null;
  current_range: string | null;
  effr_start: number | null;
  rows: WatchRow[];
  unreached: string[];
  note: string | null;
  options_check: { report_date: string; source: string; licence: string; rows: WatchCheck[] } | null;
}

export interface FedWatch {
  watch: Watch | null;
  /** Null when the watch ran; a reason to render otherwise. */
  missing: string | null;
  source: "live" | "snapshot";
}

/**
 * `cli.py watch` — what the futures strip prices at each upcoming meeting, as a
 * distribution over target ranges. The MARKET's path, read off a separate
 * contract per meeting; unlike `path`, it is a rate path, the priced one.
 *
 * ~15s warm (it parses the full futures archive), so it streams behind its own
 * Suspense boundary like the path does.
 */
async function loadWatch(): Promise<FedWatch> {
  const watch = await runCli<Watch>(repoRoot(), ["watch", "--json", "--n=8"], 120_000);
  const source = engineUnreachable ? "snapshot" : "live";
  if (!watch || !Array.isArray(watch.rows) || watch.rows.length === 0) {
    return {
      watch: null,
      missing: watch?.note ?? "The CME watch did not run. `python cli.py watch` in engine/.",
      source,
    };
  }
  return { watch, missing: null, source };
}

const watchMemo = memo(TTL_MS, loadWatch, (w) => w.watch !== null);

export function getFedWatch(): Promise<FedWatch> {
  return watchMemo();
}
