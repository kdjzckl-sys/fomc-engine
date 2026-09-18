import { Suspense } from "react";
import {
  getFed,
  type Coefficient,
  type Driver,
  type FedBundle,
  type MoveSubset,
  type Score,
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
 *  2. No accuracy figure without its baselines. The Fed holds ~69% of scheduled
 *     meetings, so 69% is nothing. Every accuracy number on this page goes
 *     through <Ladder>, which renders the model and all three baselines in one
 *     table and cannot draw one without the others. The move-only column is the
 *     one that matters, and on the current record it is a LOSS — the free
 *     1y-minus-funds spread calls direction better than the engine does. That
 *     renders in red, at the top, rather than being left for someone to derive.
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

              <div className="grid gap-6 md:grid-cols-[1.4fr_1fr]">
                <Panel className="p-5">
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
                </Panel>

                <Panel className="p-5 font-mono text-sm">
                  <Stat label="current target (upper)" value={`${fx(f.current_target_upper)}%`} />
                  <Stat
                    label="expected move"
                    value={`${f.expected_bp >= 0 ? "+" : "−"}${Math.abs(
                      f.expected_bp,
                    ).toFixed(1)} bp`}
                    tone={f.expected_bp > 2 ? "up" : f.expected_bp < -2 ? "down" : undefined}
                  />
                  <Stat label="implied target" value={`${fx(f.implied_target_upper)}%`} />
                  <p className="mt-4 border-t border-[var(--accent-edge)] pt-3 text-[11px] leading-relaxed text-[var(--text-faint)]">
                    Read the probability, not the argmax. The expected move is the
                    distribution&apos;s mean in basis points, not a prediction that the Fed will
                    move by that amount — the Committee only chooses in 25bp steps.
                  </p>
                </Panel>
              </div>
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
              No statement language, no dot plot, no fed funds futures — the market-expectation
              columns are Treasury-vs-effective-funds spreads standing in for CME pricing, and
              that substitution is the feature set&apos;s biggest known limitation. It cannot see
              a shock, and it is weakest at turning points, which is where it matters most. Not
              advice; a prior to argue with.
            </footer>
          </>
        )}
      </div>
    </main>
  );
}

/* ── the trust strip ─────────────────────────────────────────────────────── */

/**
 * Three facts, above the forecast, answering "should I believe the number below".
 *
 * The middle one is the engine's own verdict on itself: on the meetings where
 * the Fed actually moved, does it beat what the market had already priced for
 * free? On the 2026-09-17 record it does not, by 26 points. That belongs above
 * the forecast, not under it.
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
  const edge = moves ? moves.model - moves.curve_proxy : null;
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
          vs. the market, on moves
        </div>
        <div
          className={`mt-0.5 font-mono text-lg tabular-nums ${
            edge === null ? "" : beats ? "text-[var(--ok)]" : "text-[var(--urgent)]"
          }`}
        >
          {edge === null ? "not scored" : pp(edge)}
        </div>
        <div className="mt-0.5 text-[10px] text-[var(--text-faint)]">
          {moves
            ? `${pct(moves.model)} vs ${pct(moves.curve_proxy)} priced · n=${moves.n_moves}`
            : "no move subset"}
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
  const edge = moves ? moves.model - moves.curve_proxy : null;

  return (
    <Section
      title="walk-forward record"
      note={
        <>
          {s.n} meetings, {s.from} → {s.to}. Refit at every step on prior meetings only — scaler,
          imputation medians, cutpoints and coefficients, so no fit ever saw its own test row.
          Headline accuracy is meaningless on its own: the Fed held {pct(s.baseline_always_hold)}{" "}
          of these meetings, so that is the floor, not the bar.
        </>
      }
    >
      {edge !== null && edge <= 0 ? (
        <Caveat heading="On the meetings that matter, the engine loses to what was already priced.">
          Across the {moves?.n_moves} meetings where the Fed actually moved, the free
          1y-minus-effective-funds spread called direction {pct(moves?.curve_proxy)} of the time
          against this model&apos;s {pct(moves?.model)} — a gap of {pp(edge)}. The all-meeting
          column below reads well only because holds dominate it. Treat the distribution as a
          structured read on <i>why</i> pressure sits where it does, not as an edge over the
          curve.
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
          note={`vs 0.0% for always-hold · n=${s.n_moves}`}
        />
        <Metric
          label="false alarms on holds"
          value={pct(s.false_alarm_rate)}
          note="called a move when it held"
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
 * The model and every baseline, in one table, on both subsets.
 *
 * This component is the mechanism behind rule 2: there is no code path here that
 * renders the model's accuracy without rendering the three baselines beside it,
 * so the page cannot regress into quoting a bare number.
 */
function Ladder({ s, moves }: { s: Score; moves: MoveSubset | null }) {
  // All-meeting figures come from the artifact. Move-only figures are recomputed
  // from the per-meeting rows so all four land on the same 72 meetings — the
  // artifact only publishes the model's own move accuracy, not the baselines'.
  // The definition is a deliberate port of
  // os/auctus/rates/differential.py::subset_baselines; lib/fedSubset.test.mjs
  // pins it against that module's own fixture.
  const rows: { label: string; all: number | null; move: number | null; model?: boolean }[] = [
    { label: "this model", all: s.accuracy_direction, move: moves?.model ?? s.move_accuracy_direction, model: true },
    { label: "always say hold", all: s.baseline_always_hold, move: moves?.always_hold ?? 0 },
    { label: "repeat the last action", all: s.baseline_persistence, move: moves?.momentum ?? null },
    { label: "market-implied (1y − funds)", all: s.baseline_market_direction, move: moves?.curve_proxy ?? null },
  ];

  const bestAll = Math.max(...rows.slice(1).map((r) => r.all ?? 0));
  const bestMove = Math.max(...rows.slice(1).map((r) => r.move ?? 0));
  const modelAll = rows[0].all ?? 0;
  const modelMove = rows[0].move ?? 0;

  return (
    <Panel className="overflow-x-auto">
      <table className="w-full font-mono text-xs">
        <caption className="sr-only">
          Direction accuracy for the model and each baseline, over all scored meetings and over
          the subset where the Fed moved.
        </caption>
        <thead className="text-[var(--text-faint)]">
          <tr className="border-b border-[var(--accent-edge)]">
            <th scope="col" className="px-3 py-2 text-left font-normal">
              direction called by
            </th>
            <th scope="col" className="px-3 py-2 text-right font-normal">
              all meetings (n={s.n})
            </th>
            <th scope="col" className="px-3 py-2 text-right font-normal">
              when it moved (n={moves?.n_moves ?? s.n_moves})
            </th>
          </tr>
        </thead>
        <tbody>
          {rows.map((r) => (
            <tr
              key={r.label}
              className={`border-b border-[var(--accent-edge)] last:border-0 ${
                r.model ? "bg-[rgba(232,179,74,0.05)]" : ""
              }`}
            >
              <th
                scope="row"
                className={`px-3 py-2 text-left font-normal ${
                  r.model ? "text-[var(--accent)]" : "text-[var(--text-dim)]"
                }`}
              >
                {r.label}
              </th>
              <Cell v={r.all} best={!r.model && r.all !== null && r.all >= bestAll} model={r.model} />
              <Cell
                v={r.move}
                best={!r.model && r.move !== null && r.move >= bestMove && bestMove > 0}
                model={r.model}
              />
            </tr>
          ))}
          <tr className="border-t border-[var(--accent-edge)]">
            <th
              scope="row"
              className="px-3 py-2 text-left font-normal text-[var(--text-faint)]"
            >
              model edge vs. best baseline
            </th>
            <Edge d={modelAll - bestAll} />
            <Edge d={bestMove > 0 ? modelMove - bestMove : null} />
          </tr>
        </tbody>
      </table>
    </Panel>
  );
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
