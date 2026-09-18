"""Score the walk-forward backtest over a narrowed window, without publishing it.

A one-shot comparison harness, not part of the engine. It trains on the full
prior history either way and only narrows the window that gets SCORED, which is
the only fair way to ask "does the futures block help in the era where the
futures data is actually good". It deliberately does not write
models/backtest.json -- a narrowed score must never replace the published one.

    python cmp_window.py 2000-01-01 > out.json
    FOMC_FUTURES=1 python cmp_window.py 2000-01-01 > out.json
"""
import json
import sys

import backtest as B

start = sys.argv[1] if len(sys.argv) > 1 else None
r = B.run(start=start, verbose=False)
r.pop("preds", None)
print(json.dumps(r))
