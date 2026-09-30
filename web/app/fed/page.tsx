import { Suspense } from "react";
import {
  getFed,
  type Coefficient,
  type Driver,
  type FedBundle,
  type MoveSubset,
  type Score,
  type SubsetRow,
} from "@/lib/fed";
import { Caveat, Metric, Panel, Section, Stat, fx, pct, pp } from "./ui";
import { Reliability } from "./reliability";
import { PathSection, PathSkeleton } from "./path";

export const dynamic = "force-dynamic";

/**
 * /fed — the FOMC reaction-function engine.
 *
 * Server-rendered from the same loader /api/fed serves, so the first paint is
 * the real number rather than a spinner.
 *
 * THREE RULES THIS PAGE EXISTS TO ENFORCE, all of them from the engine's README:
 *
 *  1. Calibration is the point. The output is a distribution, not a call, and
 *     "the model's job is to tell you 30% when it is 30%". The trust strip sits
 *     directly under the header — above the forecast — and the full reliability
 *     diagram renders before the path and the drivers.
 *
 *  2. No accuracy figure without its baselines, and no recall without its
 *     price. The Fed holds ~69% of scheduled meetings, so 69% is nothing.
 *     Every accuracy number on this page goes through <Ladder>, which renders
 *     the model and every baseline in one table and draws the move column and
 *     the false-alarm column from the same row object, so neither can appear
 *     alone. The move column is the one that matters, and on the current
 *     record it is a LOSS: what the market had already priced calls direction
 *     better than the engine does. That renders in red, at the top.
 *
 *     Those numbers are READ, not computed. engine/backtest.py publishes the
 *     whole scorecard; this page used to rederive it from the per-meeting rows
 *     and a third consumer derived it again, and the three drifted.
 *
 *  3. The path is not a rate path. See path.tsx — the caveat is a block in the
 *     section's own flow, never a tooltip.
 */
export default async function FedPage() {
  const d = await getFed();
  const f = d.forecast;

  return (
    <main className="min-h-screen bg-[var(--bg)] text-[var(--text)] px-4 py-8 sm:px-8">
      <div className="mx-auto w-full max-w-5xl">
        <header className="mb-6 flex flex-wrap items-baseline justify-between gap-2 border-b border-[var(--accent-edge)] pb-4">
          <div>
            <h1 className="text-xl font-semibold tracking-wide text-[var(--accent)]">
              FOMC · reaction function
            </h1>
            <p className="mt-1 text-xs text-[var(--text-faint)]">
              Ordered logit on 36 years of decisions · point-in-time data · walk-forward scored
            </p>
          </div>
          <div className="flex items-baseline gap-4">
            <span className="font-mono text-[11px] text-[var(--text-faint)]">
              {new Date(d.asOf).toLocaleString()}
            </span>
          </div>
        </header>

        {/*
          PROVENANCE, STATED UP FRONT.

          Off this box there is no Python and no engine directory, so the page
          serves the frozen snapshot in `data/snapshot.json`. That is a fine way
          to publish the read — the numbers were produced by the same CLI — but
          it stops being fine the moment the page lets someone believe they are
          looking at a live run. Same rule as the accuracy baselines and the
          path caveat: the qualification travels with the number, in the flow,
          not in a tooltip.
        */}
        {d.source === "snapshot" && (
          <div className="mb-6 rounded border border-[var(--accent-edge)] bg-[var(--panel)] px-4 py-3">
            <p className="text-xs text-[var(--text)]">
              <span className="font-semibold text-[var(--accent)]">Frozen snapshot.</span>{" "}
              The engine does not run on this host. These are the numbers{" "}
              <span className="font-mono">cli.py</span> produced on{" "}
              <span className="font-mono">
                {d.generatedAt ? new Date(d.generatedAt).toLocaleString() : "an unrecorded date"}
              </span>
              , not a live read.
            </p>
            <p className="mt-1 text-xs text-[var(--text-faint)]">
              Incoming data since then is not in this page, and the futures read below
              is that day&apos;s pricing, not today&apos;s.
            </p>
          </div>
        )}

        {!f ? (
          <Empty message={d.missing ?? "No forecast available."} />
        ) : (
          <>
            <TrustStrip score={d.score} moves={d.moves} reliability={d.reliability} />

            {/* ── the call ─────────────────────────────────────────────── */}
            <section className="mb-10">
              <div className="mb-4 flex flex-wrap items-baseline gap-x-4 gap-y-1">
                <h2 className="text-lg text-[var(--text)]">
                  Next meeting{" "}
                  <span className="font-mono text-[var(--accent)]">{f.meeting}</span>
                </h2>
                <span className="text-xs text-[var(--text-dim)]">
                  {f.days_away} days away
                  {f.sep ? " · SEP / projections meeting" : ""}
                </span>
              </div>

              {f.handoff ? <Headline f={f} /> : null}

              <div className="grid gap-6 md:grid-cols-2">
                <Panel className="p-5">
                  <h3 className="mb-3 text-xs uppercase tracking-wider text-[var(--text-dim)]">
                    model{f.handoff?.headline_source === "model" ? " · headline" : ""}
                  </h3>
                  {Object.entries(f.probs ?? {}).map(([name, p]) => (
                    <ProbBar key={name} name={name} p={p} />
                  ))}
                  <div className="mt-4 flex flex-wrap gap-x-6 gap-y-1 border-t border-[var(--accent-edge)] pt-3 font-mono text-xs">
                    <span>
                      P(cut) <b className="text-[var(--ok)]">{pct(f.p_cut)}</b>
                    </span>
                    <span>
                      P(hold) <b>{pct(f.p_hold)}</b>
                    </span>
                    <span>
                      P(hike) <b className="text-[var(--urgent)]">{pct(f.p_hike)}</b>
                    </span>
                  </div>
                  <div className="mt-3 font-mono text-sm">
                    <Stat label="current target (upper)" value={`${fx(f.current_target_upper)}%`} />
                    <Stat
                      label="expected move"
                      value={bp(f.expected_bp)}
                      tone={f.expected_bp > 2 ? "up" : f.expected_bp < -2 ? "down" : undefined}
                    />
                    <Stat label="implied target" value={`${fx(f.implied_target_upper)}%`} />
                  </div>
                  <p className="mt-4 border-t border-[var(--accent-edge)] pt-3 text-[11px] leading-relaxed text-[var(--text-faint)]">
                    Read the probability, not the argmax. The expected move is the
                    distribution&apos;s mean in basis points, not a prediction that the Fed will
                    move by that amount — the Committee only chooses in 25bp steps.
                  </p>
                </Panel>

                <MarketPanel f={f} />
              </div>

              {f.handoff?.measured ? <HorizonTable h={f.handoff} /> : null}
            </section>

            {/* ── the record, always next to its baselines ─────────────── */}
            {d.score ? <Scorecard s={d.score} moves={d.moves} /> : null}

            {/* ── calibration ──────────────────────────────────────────── */}
            <Reliability read={d.reliability} bins={d.calibration} />

            {/* ── the conditional path (streams; ~7s of Python) ─────────── */}
            <Suspense fallback={<PathSkeleton />}>
              <PathSection />
            </Suspense>

            {/* ── attribution ──────────────────────────────────────────── */}
            <Section
              title="what is driving this meeting"
              note={
                <>
                  Exact arithmetic, not an approximation: each contribution is β<sub>j</sub> ·
                  z<sub>j</sub>, the fitted coefficient times the standardised feature value, in
                  latent policy-pressure units. They sum to the latent score the cutpoints are
                  applied to, so this decomposition IS the forecast rather than a story told
                  about it.
                </>
              }
            >
              <div className="grid gap-6 md:grid-cols-2">
                <DriverList
                  title="pushing toward tightening"
                  tone="up"
                  rows={f.drivers_tightening?.slice(0, 6) ?? []}
                />
                <DriverList
                  title="pushing toward easing"
                  tone="down"
                  rows={f.drivers_easing?.slice(0, 6) ?? []}
                />
              </div>
            </Section>

            {/* ── the fitted reaction function ─────────────────────────── */}
            <Coefficients rows={d.coefficients} />

            {/* ── history ──────────────────────────────────────────────── */}
            <Section title="recent decisions">
              <Panel className="overflow-x-auto">
                <table className="w-full font-mono text-xs">
                  <caption className="sr-only">
                    The most recent policy decisions, derived from the FRED target series.
                  </caption>
                  <tbody>
                    {d.history.slice(0, 14).map((h) => (
                      <tr
                        key={h.date}
                        className="border-b border-[var(--accent-edge)] last:border-0"
                      >
                        <td className="px-3 py-2 text-[var(--text-dim)]">{h.date}</td>
                        <td
                          className={`px-3 py-2 text-right tabular-nums ${
                            h.delta_bp > 0
                              ? "text-[var(--urgent)]"
                              : h.delta_bp < 0
                                ? "text-[var(--ok)]"
                                : "text-[var(--text-faint)]"
                          }`}
                        >
                          {h.delta_bp > 0 ? "+" : ""}
                          {h.delta_bp.toFixed(0)}bp
                        </td>
                        <td className="px-3 py-2">{h.label_name}</td>
                        <td className="px-3 py-2 text-right tabular-nums text-[var(--text-dim)]">
                          {h.target_before.toFixed(2)}% → {h.target_after.toFixed(2)}%
                        </td>
                        <td className="px-3 py-2 text-[10px] text-[var(--text-faint)]">
                          {h.scheduled ? "" : "UNSCHEDULED"}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </Panel>
            </Section>

            <footer className="border-t border-[var(--accent-edge)] pt-4 text-[11px] leading-relaxed text-[var(--text-faint)]">
              Model trained {f.model?.trained?.slice(0, 10) ?? "n/a"} on{" "}
              {f.model?.n_meetings ?? "?"} scheduled meetings through {f.model?.train_to ?? "?"}.
              No statement language, no dot plot, no press conference — the whole
              transcript layer is missing. The fed funds futures strip IS in the feature set
              and is also scored as a baseline in its own right, on the meetings it reaches;
              where it does not reach, a Treasury-minus-funds spread stands in for the
              feature and the baseline is left null rather than quietly proxied. It cannot
              see a shock, and it is weakest at turning points, which is where it matters
              most. Not advice; a prior to argue with.
            </footer>
          </>
        )}
      </div>
    </main>
  );
}

/* ── the trust strip ─────────────────────────────────────────────────────── */

/**
 * The bar the model has to clear, and the price that bar pays for clearing it.
 *
 * "What the market had already priced" is TWO different series in this artifact.
 * `market_futures` is the CME ZQ strip run through the FedWatch arithmetic —
 * what the market actually priced — and it is null on the meetings the strip
 * cannot reach. `market_proxy` is a 1y-minus-effective-funds yield spread
 * standing in for it, and it covers every meeting. The real one is the bar
 * whenever the engine publishes it; the proxy is the fallback and is labelled as
 * a proxy wherever it is used, because the two do not say the same thing.
 *
 * Recall travels with `falseAlarm` in the same object on purpose: there is no
 * shape here that lets the page quote one without the other.
 */
function benchmark(moves: MoveSubset | null) {
  if (!moves) return null;
  const ff = moves.futures;
  if (ff && ff.move_direction !== null) {
    return {
      label: "fed funds futures",
      proxy: false,
      recall: ff.move_direction,
      falseAlarm: ff.hold_false_alarm,
      n: ff.n_moves,
      coverage: ff.n,
    };
  }
  return {
    label: "1y − funds proxy",
    proxy: true,
    recall: moves.curve_proxy,
    falseAlarm: moves.curve_proxy_false_alarm,
    n: moves.n_moves,
    coverage: moves.n,
  };
}

/**
 * Three facts, above the forecast, answering "should I believe the number below".
 *
 * The middle one is the engine's own verdict on itself: on the meetings where
 * the Fed actually moved, does it beat what the market had already priced? On
 * the current record it does not, and against the real futures strip it does not
 * by a wider margin than against the proxy. That belongs above the forecast, not
 * under it.
 */
function TrustStrip({
  score,
  moves,
  reliability,
}: {
  score: Score | null;
  moves: MoveSubset | null;
  reliability: FedBundle["reliability"];
}) {
  if (!score && !moves && !reliability) return null;
  const bench = benchmark(moves);
  const edge = moves && bench ? moves.model - bench.recall : null;
  const beats = edge !== null && edge > 0;

  return (
    <div className="mb-8 grid gap-3 sm:grid-cols-3">
      <Panel className="px-4 py-3">
        <div className="text-[10px] uppercase tracking-wider text-[var(--text-faint)]">
          calibrated to
        </div>
        <div className="mt-0.5 font-mono text-lg tabular-nums text-[var(--accent)]">
          {reliability ? pp(reliability.gap_weighted).replace("+", "") : "not scored"}
        </div>
        <div className="mt-0.5 text-[10px] text-[var(--text-faint)]">
          mean gap, said vs happened{reliability ? ` · ${reliability.n} meetings` : ""}
        </div>
      </Panel>

      <Panel
        className={`px-4 py-3 ${
          edge !== null && !beats ? "border-[var(--urgent)]" : ""
        }`}
      >
        <div className="text-[10px] uppercase tracking-wider text-[var(--text-faint)]">
          vs. {bench ? bench.label : "the market"}, on moves
        </div>
        <div
          className={`mt-0.5 font-mono text-lg tabular-nums ${
            edge === null ? "" : beats ? "text-[var(--ok)]" : "text-[var(--urgent)]"
          }`}
        >
          {edge === null ? "not scored" : pp(edge)}
        </div>
        <div className="mt-0.5 text-[10px] leading-relaxed text-[var(--text-faint)]">
          {moves && bench ? (
            <>
              {pct(moves.model)} called, {pct(moves.model_false_alarm)} false alarms ·{" "}
              {pct(bench.recall)} priced, {pct(bench.falseAlarm)} false alarms · n=
              {bench.n} moves
            </>
          ) : (
            "no move subset published"
          )}
        </div>
      </Panel>

      <Panel className="px-4 py-3">
        <div className="text-[10px] uppercase tracking-wider text-[var(--text-faint)]">
          walk-forward window
        </div>
        <div className="mt-0.5 font-mono text-lg tabular-nums">
          {score ? `${score.n}` : "—"}
          <span className="ml-1 text-xs text-[var(--text-dim)]">meetings</span>
        </div>
        <div className="mt-0.5 text-[10px] text-[var(--text-faint)]">
          {score ? `${score.from} → ${score.to}` : "never backtested"}
        </div>
      </Panel>
    </div>
  );
}

/* ── the scorecard ───────────────────────────────────────────────────────── */

function Scorecard({ s, moves }: { s: Score; moves: MoveSubset | null }) {
  const bench = benchmark(moves);
  const edge = moves && bench ? moves.model - bench.recall : null;

  return (
    <Section
      title="walk-forward record"
      note={
        <>
          {s.n} meetings, {s.from} → {s.to}. Refit at every step on prior meetings only — scaler,
          imputation medians, cutpoints and coefficients, so no fit ever saw its own test row.
          Headline accuracy is meaningless on its own: the Fed held {pct(s.baseline_always_hold)}{" "}
          of these meetings, so that is the floor, not the bar. Every figure below is read
          straight out of the engine&apos;s scorecard — this page does not recompute any of it.
        </>
      }
    >
      {edge !== null && edge <= 0 && moves && bench ? (
        <Caveat heading="On the meetings that matter, the engine loses to what was already priced.">
          Across the {bench.n} meetings where the Fed moved{" "}
          {bench.proxy ? "" : `and the futures strip reached (of ${moves.n_moves} in all) `}, the{" "}
          {bench.label} called direction {pct(bench.recall)} of the time against this
          model&apos;s {pct(moves.model)} — a gap of {pp(edge)}.{" "}
          {bench.proxy ? (
            <>
              It buys that by crying &quot;move&quot; at {pct(bench.falseAlarm)} of holds, against
              the model&apos;s {pct(moves.model_false_alarm)}, so read the two columns together.
            </>
          ) : (
            <>
              And unlike the yield-spread proxy it does not buy that recall with false alarms:{" "}
              {pct(bench.falseAlarm)} of holds against the model&apos;s{" "}
              {pct(moves.model_false_alarm)}. It is better on both axes at once. Treat the
              distribution as a structured read on <i>why</i> pressure sits where it does, not as
              an edge over the market.
            </>
          )}
        </Caveat>
      ) : null}

      <Ladder s={s} moves={moves} />

      <div className="mt-3 grid gap-3 sm:grid-cols-4">
        <Metric
          label="exact size, all meetings"
          value={pct(s.accuracy_5class)}
          note={`vs ${pct(s.baseline_always_hold)} for always-hold`}
        />
        <Metric
          label="exact size, on moves"
          value={pct(s.move_accuracy_5class)}
          note={`vs 0.0% for always-hold · n=${moves?.n_moves ?? s.n_moves}`}
        />
        <Metric
          label="false alarms on holds"
          value={pct(s.false_alarm_rate)}
          note={`called a move when it held · n=${moves?.n_holds ?? "?"}`}
        />
        <Metric
          label="mean |error|"
          value={`${s.mae_bp.toFixed(1)} bp`}
          note={`log loss ${s.log_loss.toFixed(3)} · Brier ${s.brier.toFixed(3)}`}
        />
      </div>
    </Section>
  );
}

/**
 * The model and every baseline, on all meetings, on the moves, and on the holds.
 *
 * This component is the mechanism behind rule 2, and it now enforces a second
 * rule as well: the "when it moved" column and the "false alarms on holds"
 * column are rendered from the same row object, in the same loop, so there is no
 * code path that draws recall without the price of it. That is the misread the
 * whole scorecard exists to prevent — the curve proxy calls 9 moves in 10 and
 * buys it by shouting at nearly half of all holds.
 *
 * Rows come from the engine. Nothing here is computed; when the artifact
 * predates the published scorecard, the fallback below carries only what the
 * top-level scalars actually contain, and the rest renders as n/a rather than as
 * a locally derived guess.
 */
function Ladder({ s, moves }: { s: Score; moves: MoveSubset | null }) {
  const rows: SubsetRow[] =
    moves && moves.rows.length > 0 ? moves.rows : fallbackRows(s);

  const baselines = rows.filter((r) => r.key !== "model");
  const model = rows.find((r) => r.key === "model") ?? null;
  const bestAll = Math.max(...baselines.map((r) => r.all_direction ?? 0), 0);
  const bestMove = Math.max(...baselines.map((r) => r.move_direction ?? 0), 0);
  const mixedCoverage = rows.some((r) => r.n !== rows[0].n);

  return (
    <Panel className="overflow-x-auto">
      <table className="w-full font-mono text-xs">
        <caption className="sr-only">
          Direction accuracy for the model and each baseline, over all scored meetings, over the
          subset where the Fed moved, and the false-alarm rate on the meetings where it held.
        </caption>
        <thead className="text-[var(--text-faint)]">
          <tr className="border-b border-[var(--accent-edge)]">
            <th scope="col" className="px-3 py-2 text-left font-normal">
              direction called by
            </th>
            <th scope="col" className="px-3 py-2 text-right font-normal">
              scored on
            </th>
            <th scope="col" className="px-3 py-2 text-right font-normal">
              all meetings
            </th>
            <th scope="col" className="px-3 py-2 text-right font-normal">
              when it moved
            </th>
            <th scope="col" className="px-3 py-2 text-right font-normal">
              false alarms on holds
            </th>
          </tr>
        </thead>
        <tbody>
          {rows.map((r) => {
            const isModel = r.key === "model";
            return (
              <tr
                key={r.key}
                className={`border-b border-[var(--accent-edge)] last:border-0 ${
                  isModel ? "bg-[rgba(232,179,74,0.05)]" : ""
                }`}
              >
                <th
                  scope="row"
                  className={`px-3 py-2 text-left font-normal ${
                    isModel ? "text-[var(--accent)]" : "text-[var(--text-dim)]"
                  }`}
                >
                  {r.label}
                </th>
                <td className="px-3 py-2 text-right tabular-nums text-[var(--text-faint)]">
                  {r.n}
                </td>
                <Cell
                  v={r.all_direction}
                  best={!isModel && r.all_direction !== null && r.all_direction >= bestAll}
                  model={isModel}
                />
                <Cell
                  v={r.move_direction}
                  best={
                    !isModel &&
                    r.move_direction !== null &&
                    r.move_direction >= bestMove &&
                    bestMove > 0
                  }
                  model={isModel}
                />
                {/* Same row object, same loop: recall cannot render alone. */}
                <td
                  className={`px-3 py-2 text-right tabular-nums ${
                    isModel ? "text-[var(--accent)]" : "text-[var(--text-dim)]"
                  }`}
                >
                  {pct(r.hold_false_alarm)}
                </td>
              </tr>
            );
          })}
          <tr className="border-t border-[var(--accent-edge)]">
            <th
              scope="row"
              className="px-3 py-2 text-left font-normal text-[var(--text-faint)]"
            >
              model edge vs. best baseline
            </th>
            <td />
            <Edge d={model?.all_direction != null ? model.all_direction - bestAll : null} />
            <Edge
              d={
                model?.move_direction != null && bestMove > 0
                  ? model.move_direction - bestMove
                  : null
              }
            />
            <td className="px-3 py-2 text-right text-[10px] text-[var(--text-faint)]">
              read across, not down
            </td>
          </tr>
        </tbody>
      </table>
      <p className="border-t border-[var(--accent-edge)] px-3 py-2 text-[10px] leading-relaxed text-[var(--text-faint)]">
        The &quot;when it moved&quot; column is recall and the column beside it is what that
        recall cost. A caller can buy the first with the second, which is exactly what the
        1y-minus-funds proxy does.
        {mixedCoverage ? (
          <>
            {" "}
            Coverage differs by row: the fed funds futures strip cannot price every meeting, and
            those meetings are left out of its denominator rather than scored against the
            Treasury proxy under its name.
            {moves?.like_for_like ? (
              <>
                {" "}
                On the {moves.like_for_like.n} meetings it does reach, every caller scores{" "}
                {moves.like_for_like.rows
                  .map((r) => `${r.label}: ${pct(r.move_direction)} / ${pct(r.hold_false_alarm)}`)
                  .join(" · ")}{" "}
                (moves / false alarms).
              </>
            ) : null}
          </>
        ) : null}
      </p>
    </Panel>
  );
}

/**
 * What the ladder can show from an artifact that predates the published
 * scorecard: the model's own row and the all-meeting baselines, and nothing on
 * the move subset for the baselines, because the engine did not publish it and
 * this page no longer derives it.
 */
function fallbackRows(s: Score): SubsetRow[] {
  const shell = { n: s.n, n_moves: s.n_moves, n_holds: s.n - s.n_moves, move_5class: null };
  return [
    {
      key: "model",
      label: "this model",
      ...shell,
      all_direction: s.accuracy_direction,
      move_direction: s.move_accuracy_direction,
      hold_false_alarm: s.false_alarm_rate,
    },
    {
      key: "always_hold",
      label: "always say hold",
      ...shell,
      all_direction: s.baseline_always_hold,
      move_direction: 0,
      hold_false_alarm: 0,
    },
    {
      key: "repeat_last",
      label: "repeat the last action",
      ...shell,
      all_direction: s.baseline_persistence,
      move_direction: null,
      hold_false_alarm: null,
    },
    {
      key: "market_proxy",
      label: "market-implied (1y − funds proxy)",
      ...shell,
      all_direction: s.baseline_market_direction,
      move_direction: null,
      hold_false_alarm: null,
    },
  ];
}

function Cell({ v, best, model }: { v: number | null; best?: boolean; model?: boolean }) {
  return (
    <td
      className={`px-3 py-2 text-right tabular-nums ${
        model ? "text-[var(--accent)]" : best ? "text-[var(--text)]" : "text-[var(--text-dim)]"
      }`}
    >
      {pct(v)}
      {best ? <span className="ml-1.5 text-[9px] text-[var(--text-faint)]">best</span> : null}
    </td>
  );
}

function Edge({ d }: { d: number | null }) {
  return (
    <td
      className={`px-3 py-2 text-right font-semibold tabular-nums ${
        d === null ? "text-[var(--text-faint)]" : d > 0 ? "text-[var(--ok)]" : "text-[var(--urgent)]"
      }`}
    >
      {d === null ? "n/a" : pp(d)}
    </td>
  );
}

/* ── the fitted reaction function ────────────────────────────────────────── */

function Coefficients({ rows }: { rows: Coefficient[] | null }) {
  if (!rows?.length) return null;
  const max = Math.max(...rows.map((r) => Math.abs(r.beta)), 0.001);

  return (
    <Section
      title="the fitted reaction function"
      note={
        <>
          One signed coefficient per feature, biggest first — the thing a desk would actually
          argue with. The latent is policy pressure, so a positive β means a higher value of that
          feature pushes toward tightening. These are L2-penalised and fitted on standardised
          columns, so they are comparable to each other in size but are not elasticities, and a
          feature correlated with three others will have its weight split across them.
        </>
      }
    >
      <Panel className="p-4">
        <ul className="space-y-1.5">
          {rows.map((r) => {
            const w = (Math.abs(r.beta) / max) * 50;
            const tight = r.beta > 0;
            return (
              <li key={r.feature} className="flex items-center gap-2 font-mono text-xs">
                <span className="w-40 shrink-0 truncate text-[var(--text-dim)]" title={r.feature}>
                  {r.feature}
                </span>
                <span className="w-16 shrink-0 text-right tabular-nums">
                  {r.beta >= 0 ? "+" : "−"}
                  {Math.abs(r.beta).toFixed(3)}
                </span>
                {/* Diverging bar off a centre line: left = easing, right = tightening. */}
                <span className="relative h-2.5 flex-1">
                  <span className="absolute inset-y-0 left-1/2 w-px bg-[var(--accent-edge)]" />
                  <span
                    className="absolute inset-y-0 rounded-sm"
                    style={{
                      width: `${w}%`,
                      left: tight ? "50%" : `${50 - w}%`,
                      background: tight ? "var(--urgent)" : "var(--ok)",
                    }}
                  />
                </span>
              </li>
            );
          })}
        </ul>
        <div className="mt-3 flex justify-between border-t border-[var(--accent-edge)] pt-2 text-[10px] text-[var(--text-faint)]">
          <span className="text-[var(--ok)]">← pushes toward easing</span>
          <span className="text-[var(--urgent)]">pushes toward tightening →</span>
        </div>
      </Panel>
    </Section>
  );
}

/* ── pieces ─────────────────────────────────────────────────────────────── */

type Fc = NonNullable<FedBundle["forecast"]>;

const bp = (v: number | null | undefined) =>
  v == null || !Number.isFinite(v) ? "—" : `${v >= 0 ? "+" : "−"}${Math.abs(v).toFixed(1)} bp`;

/**
 * The headline: the model's call or the market's, whichever the measured record
 * says to trust at this distance from the meeting. The choice and its reason are
 * computed by engine/predict.py from engine/models/horizon.json. This renders
 * them and does not decide them, so the CLI and the page cannot disagree.
 */
function Headline({ f }: { f: Fc }) {
  const h = f.handoff!;
  const market = h.headline_source === "market";
  const src = market
    ? { call: f.market?.call ?? null, cut: f.market?.p_cut, hold: f.market?.p_hold, hike: f.market?.p_hike }
    : { call: f.model_call ?? null, cut: f.p_cut, hold: f.p_hold, hike: f.p_hike };
  const p = src.call === "hike" ? src.hike : src.call === "cut" ? src.cut : src.hold;
  const tone =
    src.call === "hike"
      ? "text-[var(--urgent)]"
      : src.call === "cut"
        ? "text-[var(--ok)]"
        : "text-[var(--text)]";
  const disagree = f.market?.call && f.model_call && f.market.call !== f.model_call;
  return (
    <Panel className="mb-6 p-5">
      <p className="text-[11px] uppercase tracking-wider text-[var(--text-faint)]">
        headline · {market ? "fed funds futures" : "the model"}
      </p>
      <p className="mt-1 text-2xl font-semibold">
        <span className={tone}>{src.call ?? "—"}</span>{" "}
        <span className="font-mono text-base text-[var(--text-dim)]">{pct(p ?? null)}</span>
      </p>
      {h.reason ? <p className="mt-2 text-xs text-[var(--text-dim)]">{h.reason}.</p> : null}
      {disagree ? (
        <p className="mt-1 text-xs text-[var(--accent)]">
          The two disagree: the model says {f.model_call}, the market prices {f.market!.call}.
        </p>
      ) : null}
    </Panel>
  );
}

function MarketPanel({ f }: { f: Fc }) {
  const m = f.market;
  if (!m || m.call === null) {
    return (
      <Panel className="p-5">
        <h3 className="mb-3 text-xs uppercase tracking-wider text-[var(--text-dim)]">
          fed funds futures
        </h3>
        <p className="text-xs text-[var(--text-dim)]">
          Market feed missing: the futures strip does not reach this meeting in this read, so the
          headline stays with the model.
        </p>
      </Panel>
    );
  }
  return (
    <Panel className="p-5">
      <h3 className="mb-3 text-xs uppercase tracking-wider text-[var(--text-dim)]">
        fed funds futures{f.handoff?.headline_source === "market" ? " · headline" : ""}
      </h3>
      <ProbBar name="cut" p={m.p_cut ?? 0} />
      <ProbBar name="hold" p={m.p_hold ?? 0} />
      <ProbBar name="hike" p={m.p_hike ?? 0} />
      <div className="mt-3 font-mono text-sm">
        <Stat label="priced move" value={bp(m.exp_move_bp)} />
        <Stat label="call (±12.5bp band)" value={m.call ?? "—"} />
      </div>
      <p className="mt-4 border-t border-[var(--accent-edge)] pt-3 text-[11px] leading-relaxed text-[var(--text-faint)]">
        CME ZQ strip through the FedWatch arithmetic, the same number the scorecard grades.
        {m.options_implied ? (
          <>
            {" "}
            Options (Atlanta Fed) put P(hike) at {pct(m.options_implied.p_hike)} and P(cut) at{" "}
            {pct(m.options_implied.p_cut)}, but over the nearest option window, not this meeting.
          </>
        ) : null}
      </p>
    </Panel>
  );
}

/** The measured record the handoff is decided on, cited rather than asserted. */
function HorizonTable({ h }: { h: NonNullable<Fc["handoff"]> }) {
  const rows = h.measured ?? [];
  return (
    <div className="mt-6 overflow-x-auto">
      <table className="w-full font-mono text-xs">
        <caption className="mb-2 text-left text-[11px] text-[var(--text-faint)]">
          Direction called, walk-forward, by days before the meeting opens, on the meetings the
          strip reached that day. Recall on moves and false alarms on holds read together.
        </caption>
        <thead className="text-[var(--text-faint)]">
          <tr>
            <th scope="col" className="pb-1 text-left font-normal">days out</th>
            <th scope="col" className="pb-1 text-right font-normal">n</th>
            <th scope="col" className="pb-1 text-right font-normal">model</th>
            <th scope="col" className="pb-1 text-right font-normal">market</th>
            <th scope="col" className="pb-1 text-right font-normal">moves m / mkt</th>
            <th scope="col" className="pb-1 text-right font-normal">false alarm m / mkt</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((r) => {
            const here = h.at_horizon?.horizon_days === r.horizon_days;
            return (
              <tr key={r.horizon_days} className={here ? "text-[var(--accent)]" : undefined}>
                <td className="py-1">
                  {r.horizon_days}
                  {here ? " ← now" : ""}
                </td>
                <td className="py-1 text-right tabular-nums">{r.n}</td>
                <td className="py-1 text-right tabular-nums">{pct(r.model_direction)}</td>
                <td className="py-1 text-right tabular-nums">{pct(r.market_direction)}</td>
                <td className="py-1 text-right tabular-nums">
                  {pct(r.model_move_direction)} / {pct(r.market_move_direction)}
                </td>
                <td className="py-1 text-right tabular-nums">
                  {pct(r.model_false_alarm)} / {pct(r.market_false_alarm)}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

function ProbBar({ name, p }: { name: string; p: number }) {
  const tone = name.startsWith("cut")
    ? "var(--ok)"
    : name.startsWith("hike")
      ? "var(--urgent)"
      : "var(--accent-2)";
  const safe = Number.isFinite(p) ? p : 0;
  return (
    <div className="mb-2 flex items-center gap-3 font-mono text-xs">
      <span className="w-16 shrink-0 text-[var(--text-dim)]">{name}</span>
      <span className="w-12 shrink-0 text-right tabular-nums">{pct(safe)}</span>
      <span className="h-2 flex-1 overflow-hidden rounded-sm bg-white/5">
        <span
          className="block h-full rounded-sm"
          style={{ width: `${Math.max(safe * 100, 0.6)}%`, background: tone }}
        />
      </span>
    </div>
  );
}

function DriverList({
  title,
  tone,
  rows,
}: {
  title: string;
  tone: "up" | "down";
  rows: Driver[];
}) {
  const color = tone === "up" ? "text-[var(--urgent)]" : "text-[var(--ok)]";
  return (
    <Panel className="p-5">
      <h4 className={`mb-3 text-xs uppercase tracking-wider ${color}`}>{title}</h4>
      <table className="w-full font-mono text-xs">
        <thead className="text-[var(--text-faint)]">
          <tr>
            <th scope="col" className="pb-1 text-left text-[10px] font-normal">
              feature
            </th>
            <th scope="col" className="pb-1 text-right text-[10px] font-normal">
              value
            </th>
            <th scope="col" className="pb-1 text-right text-[10px] font-normal">
              β·z
            </th>
          </tr>
        </thead>
        <tbody>
          {rows.map((r) => (
            <tr key={r.feature}>
              <td className="py-1 pr-2 text-[var(--text-dim)]">{r.feature}</td>
              <td className="py-1 pr-2 text-right tabular-nums">{fx(r.value)}</td>
              <td className={`py-1 text-right tabular-nums ${color}`}>
                {r.contribution > 0 ? "+" : "−"}
                {Math.abs(r.contribution).toFixed(2)}
              </td>
            </tr>
          ))}
          {rows.length === 0 ? (
            <tr>
              <td colSpan={3} className="py-2 text-center text-[var(--text-faint)]">
                none
              </td>
            </tr>
          ) : null}
        </tbody>
      </table>
    </Panel>
  );
}

function Empty({ message }: { message: string }) {
  return (
    <Panel className="p-8 text-center">
      <p className="text-sm text-[var(--text-dim)]">{message}</p>
      <code className="mt-3 block font-mono text-xs text-[var(--accent)]">
        cd engine &amp;&amp; python cli.py all
      </code>
    </Panel>
  );
}
