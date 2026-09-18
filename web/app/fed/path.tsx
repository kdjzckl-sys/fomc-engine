import { getFedPath, type PathRow } from "@/lib/fed";
import { Caveat, Panel, Section, fx, pct } from "./ui";

/**
 * The conditional path — `cli.py path`.
 *
 * THE CAVEAT IS THE FEATURE. From the engine's README, verbatim: "`cli.py path`
 * runs the next N meetings — but every meeting past the first is scored on
 * today's data. It is 'what would the Fed do at that meeting if nothing
 * changed', a read on how much pressure is already in the data. It is not a
 * rate-path forecast and must not be quoted as one."
 *
 * So the caveat renders as a bordered block above the table, in the section's
 * own flow — not a tooltip, not a footnote, not a hover title, nothing that a
 * screenshot of this table could be taken without. Every row past the first also
 * carries its own "today's data" stamp in the table itself, because a screenshot
 * cropped to the rows has to stay honest too. The stamp is driven by the
 * engine's own `conditional_on_today` flag where it is present, and falls back
 * to row index — the flag disappearing from the artifact must not silently
 * remove the warning.
 *
 * COST. `predict.path_forecast` rebuilds the full live feature row per meeting —
 * roughly 7s for five meetings against 1.4s for a single `predict`. This is an
 * async server component behind a Suspense boundary in page.tsx so the headline
 * and the calibration read paint immediately and the path streams in.
 */
export async function PathSection() {
  const { rows, missing } = await getFedPath();

  if (missing || rows.length === 0) {
    return (
      <Section title="conditional path">
        <Panel className="px-4 py-6 text-center text-xs text-[var(--text-dim)]">
          {missing ?? "no path available"}
        </Panel>
      </Section>
    );
  }

  const base = rows[0]?.current_target_upper ?? null;

  return (
    <Section title="conditional path — pressure already in the data">
      <Caveat heading="This is not a rate-path forecast. Do not quote it as one.">
        Every meeting past the first is scored on <b>today&apos;s</b> data. The model has no
        mechanism to forecast where CPI or unemployment will be in six months, so these rows
        answer &ldquo;what would the Fed do at that meeting if nothing changed&rdquo; — a read on
        how much tightening or easing pressure is <i>already</i> in the data, not a call on the
        terminal rate. The only row that is a forecast is the first one.
      </Caveat>

      <Panel className="overflow-x-auto">
        <table className="w-full font-mono text-xs">
          <caption className="sr-only">
            The reaction function evaluated at the next {rows.length} scheduled meetings, all on
            today&apos;s data. Not a rate path.
          </caption>
          <thead className="text-[var(--text-faint)]">
            <tr className="border-b border-[var(--accent-edge)]">
              <th scope="col" className="px-3 py-2 text-left font-normal">
                meeting
              </th>
              <th scope="col" className="px-3 py-2 text-left font-normal">
                basis
              </th>
              <th scope="col" className="px-3 py-2 text-right font-normal">
                cut
              </th>
              <th scope="col" className="px-3 py-2 text-right font-normal">
                hold
              </th>
              <th scope="col" className="px-3 py-2 text-right font-normal">
                hike
              </th>
              <th scope="col" className="px-3 py-2 text-right font-normal">
                exp. move
              </th>
              <th scope="col" className="px-3 py-2 text-right font-normal">
                implied
              </th>
            </tr>
          </thead>
          <tbody>
            {rows.map((r, i) => (
              <Row key={r.meeting ?? i} r={r} first={i === 0} />
            ))}
          </tbody>
        </table>
      </Panel>

      <p className="mt-2 text-[11px] leading-relaxed text-[var(--text-faint)]">
        Implied targets are each meeting&apos;s expected move applied to the{" "}
        <b>current</b> target{base === null ? "" : ` (${fx(base)}%)`} — they do not compound down
        the column, because each row is an independent read on today&apos;s data rather than a
        step in a sequence. Reading the last row as a terminal rate is the specific misuse this
        table is built to prevent.
      </p>
    </Section>
  );
}

function Row({ r, first }: { r: PathRow; first: boolean }) {
  // The engine stamps every path row `conditional_on_today: true`. Trust it when
  // it is there; fall back to position when it is not. The warning label must
  // survive the flag being renamed or dropped by an engine rewrite.
  const conditional = r.conditional_on_today !== false && !first;
  const exp = Number.isFinite(r.expected_bp) ? r.expected_bp : null;

  return (
    <tr
      className={`border-b border-[var(--accent-edge)] last:border-0 ${
        first ? "bg-[rgba(232,179,74,0.05)]" : ""
      }`}
    >
      <td className="px-3 py-2 whitespace-nowrap">
        <span className={first ? "text-[var(--accent)]" : "text-[var(--text-dim)]"}>
          {r.meeting}
        </span>
        {r.sep ? <span className="ml-2 text-[9px] text-[var(--accent-2)]">SEP</span> : null}
      </td>
      <td className="px-3 py-2 whitespace-nowrap text-[10px]">
        {first ? (
          <span className="text-[var(--accent)]">the forecast</span>
        ) : (
          <span className="text-[var(--urgent)]">
            {conditional ? "today's data" : "unstamped"}
          </span>
        )}
      </td>
      <td className="px-3 py-2 text-right tabular-nums text-[var(--ok)]">{pct(r.p_cut)}</td>
      <td className="px-3 py-2 text-right tabular-nums">{pct(r.p_hold)}</td>
      <td className="px-3 py-2 text-right tabular-nums text-[var(--urgent)]">{pct(r.p_hike)}</td>
      <td className="px-3 py-2 text-right tabular-nums">
        {exp === null ? "n/a" : `${exp >= 0 ? "+" : "−"}${Math.abs(exp).toFixed(1)}bp`}
      </td>
      <td className="px-3 py-2 text-right tabular-nums text-[var(--text-dim)]">
        {r.implied_target_upper === null || r.implied_target_upper === undefined
          ? "n/a"
          : `${fx(r.implied_target_upper)}%`}
      </td>
    </tr>
  );
}

/** Placeholder while the path streams in. Same height, so nothing jumps. */
export function PathSkeleton() {
  return (
    <Section title="conditional path — pressure already in the data">
      <Caveat heading="This is not a rate-path forecast. Do not quote it as one.">
        Every meeting past the first is scored on <b>today&apos;s</b> data — a read on how much
        pressure is already in the data, not a call on the terminal rate.
      </Caveat>
      <Panel className="px-4 py-8 text-center text-xs text-[var(--text-faint)]">
        running the reaction function across the next five meetings…
      </Panel>
    </Section>
  );
}
