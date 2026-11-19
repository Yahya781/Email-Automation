"""Suggests an email address for leads that have a founder name and a website but no email yet.
Uses the Hunter.io Email Finder API (free plan has a small monthly credit allowance).

It ONLY writes the 'Suggested email' and 'Email confidence' columns. It never fills 'Verified email' and never
changes Status: you look at the suggestion, check it, copy it into 'Verified email' and set Status to Ready.
Set DRY_RUN=1 to print only."""
import os
import re
from urllib.parse import urlparse

import requests

from google_io import Queue, credentials

DRY = os.environ.get("DRY_RUN") == "1"
LIMIT = int(os.environ.get("EMAIL_LOOKUP_LIMIT", "10"))  # lookups per run, protects your free credits
MIN_SCORE = int(os.environ.get("EMAIL_MIN_SCORE", "70"))
FOUNDER_COL = "Founder / decision maker (check on LinkedIn)"
log = lambda *a: print(*a, flush=True)

SKIP_WORDS = ("not found", "not confirmed", "skipped", "see ")


def founder_name(cell: str):
    """'Dima Goncharov, co-founder & CEO (GitHub)' -> ('Dima', 'Goncharov'). None if there is no usable name."""
    text = (cell or "").strip()
    if not text or text.lower().startswith(SKIP_WORDS):
        return None
    name = re.split(r"[,(;]| appears | founded ", text)[0].strip()
    parts = [p for p in re.findall(r"[A-Za-zÀ-ÿ'’\-]+", name) if p]
    if len(parts) < 2:
        return None
    return parts[0], parts[-1]


def domain_of(url: str):
    host = urlparse(url if "//" in (url or "") else "//" + (url or "")).netloc.lower()
    host = host.removeprefix("www.")
    return host or None


def hunter_lookup(first, last, domain, key):
    """Returns (email, score) or (None, 0). Costs one Hunter credit only when an address is found."""
    r = requests.get("https://api.hunter.io/v2/email-finder",
                     params={"domain": domain, "first_name": first, "last_name": last, "api_key": key}, timeout=30)
    if r.status_code == 429:
        raise RuntimeError("Hunter rate limit or credits used up")
    if r.status_code != 200:
        return None, 0
    data = (r.json() or {}).get("data") or {}
    return data.get("email"), int(data.get("score") or 0)


def main():
    key = os.environ.get("HUNTER_API_KEY")
    if not key:
        log("HUNTER_API_KEY is not set, nothing to do")
        return
    q = Queue(credentials(), os.environ["SHEET_ID"], os.environ.get("QUEUE_TAB", "Outreach Queue"))
    rows = q.load()
    done = 0
    for r in rows:
        if done >= LIMIT:
            break
        if r.get("Status") != "Needs email" or r.get("Verified email", "").strip() or r.get("Suggested email", "").strip():
            continue
        name, domain = founder_name(r.get(FOUNDER_COL, "")), domain_of(r.get("Website", ""))
        if not name or not domain:
            continue
        try:
            email, score = hunter_lookup(name[0], name[1], domain, key)
        except RuntimeError as e:
            log("stopping:", e)
            break
        done += 1
        if not email or score < MIN_SCORE:
            log(f"[{r['Company']}] no confident match (score {score})")
            if not DRY:
                q.update(r["_row"], **{"Suggested email": "none found", "Email confidence": str(score)})
            continue
        log(f"[{r['Company']}] suggested {email} (score {score})")
        if not DRY:
            q.update(r["_row"], **{"Suggested email": email, "Email confidence": f"{score} (check before use)"})


if __name__ == "__main__":
    main()
