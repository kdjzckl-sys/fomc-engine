/**
 * Shared primitives and formatters for /fed.
 *
 * Pure presentation, no data access — so `reliability.tsx`, `path.tsx` and the
 * page itself all render the same tile, the same panel chrome and, crucially,
 * the same number formatting. A percentage that reads "65.3%" in one section
 * and "65%" in another is how two figures off the same file start to look like
 * two different measurements.
 */
import type { ReactNode } from "react";

/** A probability in [0,1] as a percentage, one decimal. */
export const pct = (p: number | null | undefined) =>
  p === null || p === undefined || !Number.isFinite(p) ? "n/a" : `${(p * 100).toFixed(1)}%`;

/**
 * A signed difference of two probabilities, in percentage POINTS.
 *
 * A value that rounds to zero loses its sign: a perfectly calibrated bucket
 * reading "−0.0pp" implies a direction the number does not have.
 */
export const pp = (d: number | null | undefined) => {
  if (d === null || d === undefined || !Number.isFinite(d)) return "n/a";
  const v = Math.abs(d * 100);
  const sign = v < 0.05 ? "" : d > 0 ? "+" : "−";
  return `${sign}${v.toFixed(1)}pp`;
};

/** Two decimal places, or "n/a". Never renders `NaN` or `undefined` at a user. */
export const fx = (v: number | null | undefined) =>
  v === null || v === undefined || !Number.isFinite(v) ? "n/a" : v.toFixed(2);

/** A bordered panel. The one box style on this page. */
export function Panel({
  children,
  className = "",
}: {
  children: ReactNode;
  className?: string;
}) {
  return (
    <div
      className={`rounded-lg border border-[var(--accent-edge)] bg-[var(--bg-raised)] ${className}`}
    >
      {children}
    </div>
  );
}

/** A section with a steel-blue rule heading and an optional standfirst. */
export function Section({
  title,
  note,
  children,
}: {
  title: string;
  note?: ReactNode;
  children: ReactNode;
}) {
  return (
    <section className="mb-10">
      <h3 className="mb-1 text-sm uppercase tracking-wider text-[var(--accent-2)]">{title}</h3>
      {note ? (
        <p className="mb-3 max-w-3xl text-[11px] leading-relaxed text-[var(--text-faint)]">
          {note}
        </p>
      ) : null}
      {children}
    </section>
  );
}

/** A label/value row inside a panel. */
export function Stat({
  label,
  value,
  tone,
}: {
  label: string;
  value: string;
  tone?: "up" | "down";
}) {
  const color =
    tone === "up" ? "text-[var(--urgent)]" : tone === "down" ? "text-[var(--ok)]" : "";
  return (
    <div className="mb-3 flex items-baseline justify-between gap-3">
      <span className="text-[11px] text-[var(--text-faint)]">{label}</span>
      <span className={`text-base tabular-nums ${color}`}>{value}</span>
    </div>
  );
}

/**
 * A standalone metric tile — used ONLY for figures that have no baseline to be
 * read against (log loss, Brier, mean absolute error in bp).
 *
 * Accuracy never renders through this component. An accuracy figure without the
 * thing it is beating is the single most misleading number this engine can
 * produce, so it goes through `<Ladder>` in page.tsx, which cannot draw a model
 * score without drawing the baselines beside it.
 */
export function Metric({
  label,
  value,
  note,
}: {
  label: string;
  value: string;
  note?: string;
}) {
  return (
    <Panel className="px-4 py-3">
      <div className="text-[10px] uppercase tracking-wider text-[var(--text-faint)]">{label}</div>
      <div className="mt-1 font-mono text-2xl tabular-nums text-[var(--accent)]">{value}</div>
      {note ? <div className="mt-1 text-[10px] text-[var(--text-faint)]">{note}</div> : null}
    </Panel>
  );
}

/**
 * A caveat that ships inline with the thing it qualifies.
 *
 * Deliberately a full-width bordered block in the section's own flow, not a
 * tooltip, not a footnote, not a hover title — the /fed README's rule for the
 * path view is that the caveat must be impossible to miss, and anything that
 * requires an interaction to reveal fails that.
 */
export function Caveat({ heading, children }: { heading: string; children: ReactNode }) {
  return (
    <div className="mb-4 rounded-lg border border-[var(--urgent)] bg-[rgba(212,105,74,0.08)] px-4 py-3">
      <div className="text-[10px] font-semibold uppercase tracking-wider text-[var(--urgent)]">
        {heading}
      </div>
      <div className="mt-1.5 max-w-3xl text-[12px] leading-relaxed text-[var(--text)]">
        {children}
      </div>
    </div>
  );
}
