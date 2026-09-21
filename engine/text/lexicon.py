"""Hawkish / dovish counting over FOMC statement language.

Deterministic. A counted lexicon with a checked-in, cited word list
(`lexicon/hawkish_dovish.txt`) -- **not** a sentiment model, not a model call of
any kind. Class C.

The construction is direction x object inside a short window, because counting
loaded words alone does not work on central-bank prose: "inflation" appears in
every statement ever written and carries no sign on its own. "inflation has
moderated" and "inflation has picked up" are opposite, and only the pair says
which.

Two refinements that a flat word count gets wrong, both of which matter more
than the base method:

  * **Slack inverts.** "inflation rose" is hawkish, "unemployment rose" is
    dovish. A single object list scores both as hawkish and is then wrong about
    every labour-market sentence in a cutting cycle -- the sentence that matters
    most. `[object.slack]` carries the flip.
  * **Negation flips.** "inflation is not expected to rise" is not a hawkish
    hit. A negator between the direction word and the object inverts the sign.

Read `README.md` before using the score. It is reported there against the
decision it is supposed to predict, and the number is not flattering.
"""

from __future__ import annotations

import re
from pathlib import Path

HERE = Path(__file__).resolve().parent
LEX_PATH = HERE / "lexicon" / "hawkish_dovish.txt"

# Tokens between a direction word and its object. Six is roughly a clause:
# "inflation has moved up over the past year" is 5 apart, "growth in economic
# activity has slowed" is 4. Widening it to a sentence starts pairing a
# direction word with an object in the *next* clause, which is where a counted
# lexicon stops meaning anything.
WINDOW = 6


def _parse(path: Path) -> dict[str, set[str]]:
    groups: dict[str, set[str]] = {}
    current = None
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        if line.startswith("[") and line.endswith("]"):
            current = line[1:-1].strip()
            groups.setdefault(current, set())
            continue
        if current:
            groups[current].add(line.lower())
    return groups


_G = _parse(LEX_PATH)
UP = _G["direction.up"]
DOWN = _G["direction.down"]
PRICE = _G["object.price"]
ACTIVITY = _G["object.activity"]
SLACK = _G["object.slack"]
POLICY = _G["object.policy"]
NEGATOR = _G["negator"]

# object class -> does an UP-direction word next to it read hawkish?
_UP_IS_HAWKISH = {"price": True, "activity": True, "slack": False, "policy": True}


def _object_class(tok: str) -> str | None:
    if tok in SLACK:
        return "slack"
    if tok in PRICE:
        return "price"
    if tok in ACTIVITY:
        return "activity"
    if tok in POLICY:
        return "policy"
    return None


def score(tokens: list[str]) -> dict:
    """Hawkish / dovish hit counts over a token stream.

    Every direction word is paired with the NEAREST object within WINDOW, and a
    pair is counted once. Returns raw counts plus a per-1000-word net so two
    statements of different length are comparable -- the 1990s statements are
    40 words and the 2013 ones are 880, and an unnormalised count would be
    reading length, not tone.
    """
    hawk = dove = 0
    n = len(tokens)
    for i, tok in enumerate(tokens):
        up = tok in UP
        if not up and tok not in DOWN:
            continue
        # nearest object on either side, within the window
        best = None
        for j in range(max(0, i - WINDOW), min(n, i + WINDOW + 1)):
            if j == i:
                continue
            cls = _object_class(tokens[j])
            if cls and (best is None or abs(j - i) < abs(best[0] - i)):
                best = (j, cls)
        if best is None:
            continue
        j, cls = best
        hawkish = _UP_IS_HAWKISH[cls] if up else not _UP_IS_HAWKISH[cls]
        lo, hi = (i, j) if i < j else (j, i)
        if any(t in NEGATOR for t in tokens[lo:hi + 1]):
            hawkish = not hawkish
        if hawkish:
            hawk += 1
        else:
            dove += 1

    total = max(n, 1)
    return {"hawk": hawk, "dove": dove, "words": n,
            "net_per_1k": round(1000.0 * (hawk - dove) / total, 3),
            "tone": round((hawk - dove) / (hawk + dove), 4) if (hawk + dove) else None}


if __name__ == "__main__":
    import boardsite as bs
    import statements as st
    c = st.load()
    for s in (c[-1], c[len(c) // 2]):
        print(s.published, score(bs.words(s.text)))
