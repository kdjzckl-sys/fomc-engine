import { getFedWatch, type Watch, type WatchRow } from "@/lib/fed";
import { Panel, Section, pct } from "./ui";

/**
 * The CME watch — `cli.py watch`.
 *
 * What the fed funds futures strip prices at every meeting it reaches, read off
 * a separate ZQ contract per meeting and chained into a distribution over target
 * ranges: the FedWatch table, computed by engine/watch.py, rendered here and
 * never recomputed.
 *
 * It sits next to the conditional path on purpose, because the two are easy to
 * confuse and say different things. The path is the MODEL re-run on today's data
 * and is not a rate path. This is the MARKET, and it is one: the priced path.
 * Row one is the same number the scorecard grades (test_fomc.py holds the
 * engine to that).
 */
export async function WatchSection() {
  const { watch: w, missing, source } = await getFedWatch();

  if (!w) {
    return (
      <Section title="CME watch — the priced path">
        <Panel className="px-4 py-6 text-center text-xs text-[var(--text-dim)]">
          {missing ?? "no watch available"}
        </Panel>
      </Section>
    );
  }

  const labels = Array.from(
    new Map(
      w.rows.flatMap((r) => r.ranges).map((x) => [x.label, x.upper] as const),
    ).entries(),
  )
    .sort((a, b) => b[1] - a[1])
    .map(([label]) => label);
  const stale = w.rows.filter((r) => r.stale);

  return (
    <Section
      title="CME watch — the priced path"
      note={
        <>
          Fed funds futures, meeting by meeting: each row reads its own ZQ contract through the
          FedWatch arithmetic, splits the priced move across the two 25bp outcomes that bracket
          it, and carries every earlier path forward. Unlike the conditional path below, this
          <b> is</b> a rate path: the one the market has priced
          {source === "snapshot" ? " (as of the snapshot date, not today)" : ""}. Read it as
          risk-neutral pricing, not a forecast: the two-outcome split assumes a shape the strip
          does not carry, so tails are understated. The options check under the table shows
          where that bites.
        </>
      }
    >
      {stale.length > 0 ? (
        <p className="mb-3 text-xs text-[var(--urgent)]">
          Stale quote on {stale.map((r) => `${r.contract} (${r.quote_date})`).join(", ")}: the feed
          has not printed for those contracts in over four days.
        </p>
      ) : null}

      {/* ── per meeting: what is priced at it ─────────────────────────────── */}
      <Panel className="mb-4 overflow-x-auto">
        <table className="w-full font-mono text-xs">
          <caption className="sr-only">
            The move priced at each upcoming meeting, the contract it was read from, and the
            cumulative probability the target range is above today&apos;s.
          </caption>
          <thead className="text-[var(--text-faint)]">
            <tr className="border-b border-[var(--accent-edge)]">
              <th scope="col" className="px-3 py-2 text-left font-normal">meeting</th>
              <th scope="col" className="px-3 py-2 text-left font-normal">contract</th>
              <th scope="col" className="px-3 py-2 text-right font-normal">priced here</th>
              <th scope="col" className="px-3 py-2 text-right font-normal">cut</th>
              <th scope="col" className="px-3 py-2 text-right font-normal">hold</th>
              <th scope="col" className="px-3 py-2 text-right font-normal">hike</th>
              <th scope="col" className="px-3 py-2 text-right font-normal">implied EFFR</th>
              <th scope="col" className="px-3 py-2 text-right font-normal">most likely</th>
              <th scope="col" className="px-3 py-2 text-right font-normal">
                above {w.current_range ?? "today"}
              </th>
            </tr>
          </thead>
          <tbody>
            {w.rows.map((r, i) => (
              <MeetingRow key={r.meeting} r={r} first={i === 0} />
            ))}
          </tbody>
        </table>
      </Panel>

      {/* ── the FedWatch matrix ───────────────────────────────────────────── */}
      <Panel className="overflow-x-auto">
        <table className="w-full font-mono text-[11px]">
          <caption className="sr-only">
            Probability of each target range after each meeting, in percent, chained across
            meetings.
          </caption>
          <thead className="text-[var(--text-faint)]">
            <tr className="border-b border-[var(--accent-edge)]">
              <th scope="col" className="px-2 py-2 text-left font-normal">after</th>
              {labels.map((lb) => (
                <th
                  key={lb}
                  scope="col"
                  className={`px-2 py-2 text-right font-normal ${
                    lb === w.current_range ? "text-[var(--accent)]" : ""
                  }`}
                >
                  {lb}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {w.rows.map((r) => {
              const by = new Map(r.ranges.map((x) => [x.label, x.p]));
              return (
                <tr key={r.meeting} className="border-b border-[var(--accent-edge)] last:border-0">
                  <th
                    scope="row"
                    className="whitespace-nowrap px-2 py-1.5 text-left font-normal text-[var(--text-dim)]"
                  >
                    {r.meeting}
                  </th>
                  {labels.map((lb) => (
                    <Heat key={lb} p={by.get(lb) ?? 0} top={lb === r.most_likely} />
                  ))}
                </tr>
              );
            })}
          </tbody>
        </table>
      </Panel>
      <p className="mt-2 text-[11px] leading-relaxed text-[var(--text-faint)]">
        Current range {w.current_range ?? "n/a"} (highlighted). EFFR going in{" "}
        {w.effr_start === null ? "n/a" : `${w.effr_start.toFixed(3)}%`}, the 10-day median.
        Probabilities under 0.05% are left blank.
        {w.unreached.length > 0
          ? ` Not reached by the listed strip: ${w.unreached.join(", ")}.`
          : ""}
        {w.rows.some((r) => r.next_month_has_meeting)
          ? " * read off a next-month contract that also holds a meeting, so part of that meeting's move is in it."
          : ""}
      </p>

      {w.options_check && w.options_check.rows.length > 0 ? (
        <OptionsCheck check={w.options_check} range={w.current_range} />
      ) : null}
    </Section>
  );
}

function MeetingRow({ r, first }: { r: WatchRow; first: boolean }) {
  const t = r.this_meeting;
  const mv = r.move_bp;
  return (
    <tr
      className={`border-b border-[var(--accent-edge)] last:border-0 ${
        first ? "bg-[rgba(232,179,74,0.05)]" : ""
      }`}
    >
      <td className="whitespace-nowrap px-3 py-2">
        <span className={first ? "text-[var(--accent)]" : "text-[var(--text-dim)]"}>
          {r.meeting}
        </span>
        {r.sep ? <span className="ml-2 text-[9px] text-[var(--accent-2)]">SEP</span> : null}
      </td>
      <td className="whitespace-nowrap px-3 py-2 text-[var(--text-dim)]" title={r.method}>
        {r.contract}
        {r.next_month_has_meeting ? "*" : ""}
        <span className="ml-1.5 text-[10px] text-[var(--text-faint)]">
          {r.contract_price.toFixed(3)}
        </span>
      </td>
      <td
        className={`px-3 py-2 text-right tabular-nums ${
          mv > 2 ? "text-[var(--urgent)]" : mv < -2 ? "text-[var(--ok)]" : ""
        }`}
      >
        {`${mv >= 0 ? "+" : "−"}${Math.abs(mv).toFixed(1)}bp`}
      </td>
      <td className="px-3 py-2 text-right tabular-nums text-[var(--ok)]">{pct(t.p_cut)}</td>
      <td className="px-3 py-2 text-right tabular-nums">{pct(t.p_hold)}</td>
      <td className="px-3 py-2 text-right tabular-nums text-[var(--urgent)]">{pct(t.p_hike)}</td>
      <td className="px-3 py-2 text-right tabular-nums text-[var(--text-dim)]">
        {r.implied_effr.toFixed(3)}%
      </td>
      <td className="px-3 py-2 text-right tabular-nums">{r.most_likely}</td>
      <td className="px-3 py-2 text-right tabular-nums text-[var(--urgent)]">
        {pct(r.vs_today.p_higher)}
      </td>
    </tr>
  );
}

/** One matrix cell, shaded by probability so the path reads at a glance. */
function Heat({ p, top }: { p: number; top: boolean }) {
  const show = p >= 0.0005;
  return (
    <td
      className={`px-2 py-1.5 text-right tabular-nums ${
        top ? "font-semibold text-[var(--text)]" : "text-[var(--text-dim)]"
      }`}
      style={show ? { background: `rgba(232,179,74,${(0.06 + 0.5 * p).toFixed(3)})` } : undefined}
    >
      {show ? (p * 100).toFixed(1) : ""}
    </td>
  );
}

function OptionsCheck({
  check,
  range,
}: {
  check: NonNullable<Watch["options_check"]>;
  range: string | null;
}) {
  return (
    <div className="mt-5">
      <h4 className="mb-1 text-xs uppercase tracking-wider text-[var(--text-dim)]">
        cross-check · SOFR options (Atlanta Fed, report {check.report_date})
      </h4>
      <p className="mb-2 max-w-3xl text-[11px] leading-relaxed text-[var(--text-faint)]">
        A different instrument pricing the same question. Where the two agree, the strip read is
        corroborated. Where they part it is usually the tails: options put weight on a cut inside a
        hiking path, and a two-outcome split of the futures mean cannot.
      </p>
      <Panel className="overflow-x-auto">
        <table className="w-full font-mono text-xs">
          <caption className="sr-only">
            Probability the target range sits above or below {range ?? "today's"} by each option
            window, futures tree beside SOFR options.
          </caption>
          <thead className="text-[var(--text-faint)]">
            <tr className="border-b border-[var(--accent-edge)]">
              <th scope="col" className="px-3 py-2 text-left font-normal">by</th>
              <th scope="col" className="px-3 py-2 text-left font-normal">after meeting</th>
              <th scope="col" className="px-3 py-2 text-right font-normal">above · futures</th>
              <th scope="col" className="px-3 py-2 text-right font-normal">above · options</th>
              <th scope="col" className="px-3 py-2 text-right font-normal">below · futures</th>
              <th scope="col" className="px-3 py-2 text-right font-normal">below · options</th>
            </tr>
          </thead>
          <tbody>
            {check.rows.map((r) => (
              <tr key={r.window} className="border-b border-[var(--accent-edge)] last:border-0">
                <td className="px-3 py-2 text-[var(--text-dim)]">{r.window}</td>
                <td className="px-3 py-2 text-[var(--text-dim)]">{r.meeting}</td>
                <td className="px-3 py-2 text-right tabular-nums">{pct(r.futures_p_higher)}</td>
                <td className="px-3 py-2 text-right tabular-nums">{pct(r.options_p_higher)}</td>
                <td className="px-3 py-2 text-right tabular-nums">{pct(r.futures_p_lower)}</td>
                <td className="px-3 py-2 text-right tabular-nums">{pct(r.options_p_lower)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </Panel>
      <p className="mt-2 text-[10px] text-[var(--text-faint)]">{check.licence}</p>
    </div>
  );
}

/** Placeholder while the watch streams in. */
export function WatchSkeleton() {
  return (
    <Section title="CME watch — the priced path">
      <Panel className="px-4 py-8 text-center text-xs text-[var(--text-faint)]">
        reading the fed funds futures strip across the next eight meetings…
      </Panel>
    </Section>
  );
}

