import type { CalibrationBin, CalibrationRead } from "@/lib/fed";
import { Panel, Section, pct, pp } from "./ui";

/**
 * The reliability read — predicted probability against realised frequency.
 *
 * WHY THIS IS THE TOP SECTION AND NOT A FOOTNOTE. The engine's output is a
 * distribution, not a call. Its own README sets the standard: "The model's job
 * is to tell you 30% when it is 30%, and the calibration table in the backtest
 * is where you check whether it does." A single forecast is only worth reading
 * once this is read, so this renders above the forecast rather than below it.
 *
 * FORM. A reliability diagram: one point per bucket, x = mean P(move) the model
 * assigned, y = share of those meetings that actually moved, plotted on a square
 * so the perfect-calibration diagonal reads as a true 45°. The vertical stem
 * from each point to the diagonal IS the miscalibration — that geometry is the
 * whole chart, which is why the stem is drawn rather than left implicit. Point
 * area is proportional to n, so a wild-looking bucket holding four meetings does
 * not read with the same weight as one holding 138.
 *
 * One series, so no legend — the heading names it. Values live in the table
 * beneath rather than as labels on every point, and every point carries a native
 * <title> for hover. No client JS: this is a server component.
 */
export function Reliability({
  read,
  bins,
}: {
  read: CalibrationRead | null;
  bins: CalibrationBin[] | null;
}) {
  // Prefer the derived read (empty buckets already dropped, error computed). Fall
  // back to whatever raw bins survived, so a backtest artifact that gains or
  // loses a calibration field still renders something rather than nothing.
  const live =
    read?.bins ??
    (bins ?? []).filter(
      (b) =>
        b &&
        typeof b.n === "number" &&
        b.n > 0 &&
        typeof b.predicted === "number" &&
        typeof b.actual === "number",
    );

  if (!live.length) {
    return (
      <Section
        title="calibration"
        note="The backtest carries no calibration table. Re-run `python cli.py backtest` in engine/ — until it exists, the probabilities below are unvalidated and should be read as a ranking, not as odds."
      >
        <Panel className="px-4 py-6 text-center text-xs text-[var(--text-dim)]">
          no calibration data
        </Panel>
      </Section>
    );
  }

  const nTotal = read?.n ?? live.reduce((s, b) => s + (b.n ?? 0), 0);
  const maxN = Math.max(...live.map((b) => b.n ?? 1), 1);

  // Geometry. Square plot, because a reliability diagram drawn on a rectangle
  // puts the identity line at the wrong angle and every deviation reads wrong.
  const S = 240;
  const PAD = 34;
  const x = (p: number) => PAD + p * S;
  const y = (p: number) => PAD + S - p * S;
  const W = S + PAD * 2;

  const grid = [0, 0.25, 0.5, 0.75, 1];

  return (
    <Section
      title="calibration — is the distribution trustworthy?"
      note={
        <>
          Read this before any single forecast. Each point is a bucket of meetings the model
          assigned a similar P(move); the diagonal is perfect calibration. A point above the line
          means the Fed moved more often than the model said, below means it moved less. Scored
          walk-forward over {nTotal} meetings.
        </>
      }
    >
      <div className="grid gap-5 lg:grid-cols-[auto_1fr]">
        <Panel className="p-4">
          <svg
            viewBox={`0 0 ${W} ${W}`}
            width={W}
            height={W}
            role="img"
            aria-label={`Reliability diagram: predicted probability of a policy move against realised frequency, over ${nTotal} walk-forward meetings. The table beside it carries every value.`}
            className="max-w-full"
          >
            {/* recessive grid */}
            {grid.map((g) => (
              <g key={g}>
                <line
                  x1={x(g)}
                  y1={y(0)}
                  x2={x(g)}
                  y2={y(1)}
                  stroke="rgba(233,231,225,0.07)"
                  strokeWidth={1}
                />
                <line
                  x1={x(0)}
                  y1={y(g)}
                  x2={x(1)}
                  y2={y(g)}
                  stroke="rgba(233,231,225,0.07)"
                  strokeWidth={1}
                />
              </g>
            ))}

            {/* perfect calibration */}
            <line
              x1={x(0)}
              y1={y(0)}
              x2={x(1)}
              y2={y(1)}
              stroke="var(--text-faint)"
              strokeWidth={1.5}
              strokeDasharray="4 4"
            />
            {/* Parallel to the line but offset PERPENDICULAR to it, into the
                empty lower-right quadrant. Centred on the line it collided with
                the dashes and neither was legible. */}
            <text
              x={x(0.66) + 11}
              y={y(0.66) + 15}
              fill="var(--text-faint)"
              fontSize={9}
              transform={`rotate(-45 ${x(0.66) + 11} ${y(0.66) + 15})`}
            >
              perfectly calibrated
            </text>

            {/* the miscalibration itself: the stem from each point to the diagonal */}
            {live.map((b) => {
              const p = b.predicted ?? 0;
              const a = b.actual ?? 0;
              return (
                <line
                  key={`stem-${b.bin}`}
                  x1={x(p)}
                  y1={y(p)}
                  x2={x(p)}
                  y2={y(a)}
                  stroke="var(--accent-2)"
                  strokeWidth={2}
                />
              );
            })}

            {/* one point per bucket, area proportional to n */}
            {live.map((b) => {
              const p = b.predicted ?? 0;
              const a = b.actual ?? 0;
              const r = 4.5 + 6 * Math.sqrt((b.n ?? 1) / maxN);
              return (
                <circle
                  key={`pt-${b.bin}`}
                  cx={x(p)}
                  cy={y(a)}
                  r={r}
                  fill="var(--accent)"
                  // 2px surface ring so overlapping buckets stay separable
                  stroke="#0a0a0d"
                  strokeWidth={2}
                >
                  <title>
                    {`${b.bin}: model said ${pct(p)}, it happened ${pct(a)} (n=${b.n})`}
                  </title>
                </circle>
              );
            })}

            {/* axes */}
            <line x1={x(0)} y1={y(0)} x2={x(1)} y2={y(0)} stroke="var(--accent-edge)" />
            <line x1={x(0)} y1={y(0)} x2={x(0)} y2={y(1)} stroke="var(--accent-edge)" />
            {grid.map((g) => (
              <g key={`t-${g}`}>
                <text
                  x={x(g)}
                  y={y(0) + 14}
                  fill="var(--text-faint)"
                  fontSize={9}
                  textAnchor="middle"
                >
                  {g * 100}
                </text>
                <text
                  x={x(0) - 6}
                  y={y(g) + 3}
                  fill="var(--text-faint)"
                  fontSize={9}
                  textAnchor="end"
                >
                  {g * 100}
                </text>
              </g>
            ))}
            <text x={x(0.5)} y={W - 4} fill="var(--text-dim)" fontSize={10} textAnchor="middle">
              model said P(move) %
            </text>
            <text
              x={12}
              y={y(0.5)}
              fill="var(--text-dim)"
              fontSize={10}
              textAnchor="middle"
              transform={`rotate(-90 12 ${y(0.5)})`}
            >
              it happened %
            </text>
          </svg>
        </Panel>

        <div>
          {read ? (
            <div className="mb-3 grid gap-3 sm:grid-cols-2">
              <Panel className="px-4 py-3">
                <div className="text-[10px] uppercase tracking-wider text-[var(--text-faint)]">
                  calibration error (n-weighted)
                </div>
                <div className="mt-1 font-mono text-2xl tabular-nums text-[var(--accent)]">
                  {pp(read.gap_weighted).replace("+", "")}
                </div>
                <div className="mt-1 text-[10px] text-[var(--text-faint)]">
                  mean gap between what it said and what happened
                </div>
              </Panel>
              <Panel className="px-4 py-3">
                <div className="text-[10px] uppercase tracking-wider text-[var(--text-faint)]">
                  worst bucket
                </div>
                <div className="mt-1 font-mono text-2xl tabular-nums text-[var(--accent)]">
                  {pp(read.gap_max).replace("+", "")}
                </div>
                <div className="mt-1 text-[10px] text-[var(--text-faint)]">
                  in the {read.worst_bin} bucket — an average can hide one broken bin
                </div>
              </Panel>
            </div>
          ) : null}

          <Panel className="overflow-x-auto">
            <table className="w-full font-mono text-xs">
              <caption className="sr-only">
                Calibration of the predicted probability of a policy move, by bucket.
              </caption>
              <thead className="text-[var(--text-faint)]">
                <tr className="border-b border-[var(--accent-edge)]">
                  <th scope="col" className="px-3 py-2 text-left font-normal">
                    model said
                  </th>
                  <th scope="col" className="px-3 py-2 text-right font-normal">
                    n
                  </th>
                  <th scope="col" className="px-3 py-2 text-right font-normal">
                    predicted
                  </th>
                  <th scope="col" className="px-3 py-2 text-right font-normal">
                    happened
                  </th>
                  <th scope="col" className="px-3 py-2 text-right font-normal">
                    gap
                  </th>
                </tr>
              </thead>
              <tbody>
                {live.map((b) => {
                  const gap = (b.actual ?? 0) - (b.predicted ?? 0);
                  return (
                    <tr key={b.bin} className="border-b border-[var(--accent-edge)] last:border-0">
                      <td className="px-3 py-2">{b.bin}</td>
                      <td className="px-3 py-2 text-right text-[var(--text-dim)]">{b.n}</td>
                      <td className="px-3 py-2 text-right tabular-nums">{pct(b.predicted)}</td>
                      <td className="px-3 py-2 text-right tabular-nums text-[var(--accent)]">
                        {pct(b.actual)}
                      </td>
                      <td className="px-3 py-2 text-right tabular-nums text-[var(--text-dim)]">
                        {pp(gap)}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </Panel>
          <p className="mt-2 text-[10px] leading-relaxed text-[var(--text-faint)]">
            Buckets are the engine&apos;s own, from{" "}
            <code className="text-[var(--text-dim)]">models/backtest.json</code>. A bucket holding
            a handful of meetings is noise — read n before reading its gap.
          </p>
        </div>
      </div>
    </Section>
  );
}
