/**
 * GET /api/fed — the FOMC reaction-function engine's read on the next meeting.
 *
 * Thin wrapper over lib/fed, which shells out to
 * `engine/cli.py predict --json` and reads the backtest artifacts.
 *
 * Takes one optional flag and nothing else: `?path=1` adds the conditional-path
 * rows. Nothing user-controlled reaches the shell — the meeting is whatever is
 * next on the scraped calendar, and the path's meeting count is a constant the
 * loader clamps. There is no injection surface here.
 *
 * CACHING lives in lib/fed now, not here. It used to live in this file, which
 * meant the /fed page — which calls getFed() directly, because it is
 * force-dynamic and renders the real number on first paint — bypassed it
 * entirely and spawned Python on every page load. One cache, both callers.
 *
 * ANY CONSUMER OF THIS ENDPOINT INHERITS AN OBLIGATION. `score.accuracy_*` is
 * meaningless without `score.baseline_*` and `moves`, and `path` is explicitly
 * not a rate-path forecast (see `conditional_on_today` on every row past the
 * first). Both are served together so a client cannot render one without the
 * other being to hand.
 *
 * Class C — no model call, no spend.
 */
import { NextResponse } from "next/server";
import { getFed, getFedPath } from "@/lib/fed";

export const dynamic = "force-dynamic";

export async function GET(req: Request) {
  const wantPath = new URL(req.url).searchParams.get("path") === "1";

  const [bundle, path] = await Promise.all([
    getFed(),
    wantPath ? getFedPath() : Promise.resolve(null),
  ]);

  return NextResponse.json(
    path
      ? {
          ...bundle,
          path: path.rows,
          path_missing: path.missing,
          // Stated in the payload, not just in the docs: a machine consumer
          // reading these rows must not quote them as a rate path.
          path_caveat:
            "Every meeting past the first is scored on TODAY's data. This is a read on " +
            "pressure already in the data, not a rate-path forecast, and must not be quoted " +
            "as one.",
        }
      : bundle,
  );
}
