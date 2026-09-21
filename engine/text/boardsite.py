"""HTTP + disk cache + HTML-to-text for federalreserve.gov.

The same shape as `fred.py`'s cache layer, for the same reason: one file per
document under `data/cache/`, so a cold build is one polite burst and every
later run is free. The Board's archive is frozen for everything older than the
last meeting, so a cached statement from 2004 can never go stale -- documents
are cached **forever** unless `--refresh` is passed, and only the index pages
(which gain a row every six weeks) carry a TTL.

On robots.txt: `federalreserve.gov/robots.txt` returns **404** -- fetched
2026-09-18, not assumed. There is no exclusion to respect, so the politeness
here is self-imposed: a descriptive User-Agent, a serial fetcher with a delay
between requests, and a cache that means the whole corpus is downloaded exactly
once.

No LLM anywhere in this package. Class C.
"""

from __future__ import annotations

import hashlib
import json
import re
import time
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
CACHE = HERE.parent / "data" / "cache" / "text"

BASE = "https://www.federalreserve.gov"
UA = "jarvis-fomc/1.0 (research; contact via github)"

# Index pages gain a row every six weeks. Documents never change.
INDEX_TTL_S = 20 * 3600
DELAY_S = 0.30          # between live fetches; cache hits do not sleep


def url_abs(href: str) -> str:
    """Absolute URL for an href harvested off a Board page."""
    href = href.strip()
    if href.startswith("http://"):
        href = "https://" + href[7:]
    if href.startswith("https://"):
        return href
    if not href.startswith("/"):
        href = "/" + href
    return BASE + href


def _slot(url: str) -> Path:
    """Cache filename: readable stem + hash, so two eras' URLs never collide."""
    stem = re.sub(r"[^A-Za-z0-9]+", "-", url.split("federalreserve.gov", 1)[-1]).strip("-")
    stem = stem[-70:] or "doc"
    return CACHE / (stem + "." + hashlib.sha1(url.encode()).hexdigest()[:8] + ".json")


def fetch(url: str, *, refresh: bool = False, ttl: int | None = None,
          allow_404: bool = False) -> str | None:
    """Page body for `url`, from cache when possible.

    `ttl=None` means cache forever, which is right for an archived document.
    Returns None on a 404 when `allow_404` -- the Board genuinely has no
    statement for many pre-2000 meetings, and a missing document is a fact
    about the record, not an error to paper over.
    """
    path = _slot(url)
    if not refresh and path.exists():
        blob = json.loads(path.read_text(encoding="utf-8"))
        fresh = ttl is None or (time.time() - path.stat().st_mtime) < ttl
        if fresh:
            return blob.get("body")

    req = urllib.request.Request(url, headers={"User-Agent": UA})
    body, last = None, None
    for attempt in range(3):
        try:
            with urllib.request.urlopen(req, timeout=45) as r:
                body = r.read().decode("utf-8", errors="ignore")
            break
        except urllib.error.HTTPError as e:            # noqa: PERF203
            last = e
            if e.code == 404:
                body = None
                break
            time.sleep(1.2 * (attempt + 1))
        except Exception as e:                          # noqa: BLE001
            last = e
            time.sleep(1.2 * (attempt + 1))
    else:
        if path.exists():                               # serve stale over failing
            return json.loads(path.read_text(encoding="utf-8")).get("body")
        raise RuntimeError("could not fetch %s: %s" % (url, last))

    if body is None and not allow_404:
        if path.exists():
            return json.loads(path.read_text(encoding="utf-8")).get("body")
        raise RuntimeError("404 on %s" % url)

    CACHE.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"url": url, "fetched": time.strftime("%Y-%m-%dT%H:%M:%S"),
                                "body": body}), encoding="utf-8")
    time.sleep(DELAY_S)
    return body


# -- HTML -> text -----------------------------------------------------------

_DROP = re.compile(r"(?is)<(script|style|noscript|svg)[^>]*>.*?</\1>")
_ENT = {"&nbsp;": " ", "&amp;": "&", "&quot;": '"', "&#39;": "'", "&apos;": "'",
        "&ldquo;": '"', "&rdquo;": '"', "&lsquo;": "'", "&rsquo;": "'",
        "&mdash;": "--", "&ndash;": "-", "&#160;": " ", "&#8217;": "'",
        "&lt;": "<", "&gt;": ">", "&hellip;": "...", "&#151;": "--"}


def to_text(html: str) -> str:
    """Tags out, paragraph breaks kept. Deliberately not a parser.

    Word counts and diffs are computed on this output, so the one property that
    matters is that it is *stable* across the Board's four page layouts -- the
    same statement has to produce the same token stream whether it was served in
    1996 or 2026, or the length and diff features would measure the Board's
    web redesigns instead of the Committee's language.
    """
    h = _DROP.sub(" ", html)
    h = re.sub(r"(?i)<br\s*/?>", "\n", h)
    h = re.sub(r"(?i)</(p|div|li|tr|h[1-6])\s*>", "\n\n", h)
    h = re.sub(r"<[^>]+>", " ", h)
    for k, v in _ENT.items():
        h = h.replace(k, v)
    h = re.sub(r"&#\d+;", " ", h)
    h = h.replace(" ", " ").replace("’", "'").replace("‘", "'")
    h = h.replace("“", '"').replace("”", '"')
    h = h.replace("—", "--").replace("–", "-").replace("﻿", "")
    h = re.sub(r"[ \t]+", " ", h)
    h = re.sub(r" *\n *", "\n", h)
    h = re.sub(r"\n{3,}", "\n\n", h)
    return h.strip()


WORD = re.compile(r"[A-Za-z][A-Za-z'\-]*")


def words(text: str) -> list[str]:
    """Lower-cased word tokens. The unit every text feature is counted in."""
    return [w.lower() for w in WORD.findall(text)]
