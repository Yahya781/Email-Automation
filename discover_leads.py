"""Finds new companies with Hunter's Discover API and appends them to the sheet as 'Needs review' rows.

It only fills Company, Website and Status. It never fills 'Verified email'. find_emails.py runs next and suggests
addresses for these rows. Discover itself is free on Hunter's side (rate limited, and not on every plan).
Set DRY_RUN=1 to print only. Filters come from env vars (see build_filters)."""
import os

import requests

from find_emails import domain_of
from google_io import Queue, credentials

DRY = os.environ.get("DRY_RUN") == "1"
LIMIT = int(os.environ.get("DISCOVER_LIMIT") or "10")  # new rows per run
MIN_PERSONAL = int(os.environ.get("DISCOVER_MIN_PERSONAL") or "1")  # skip companies Hunter knows no people at
NEW_STATUS = "Needs review"
log = lambda *a: print(*a, flush=True)


def _csv(name: str, default: str):
    # `or default` (not get's default) so a variable that is set but empty, as GitHub passes it, still uses the default
    return [x.strip() for x in (os.environ.get(name) or default).split(",") if x.strip()]


def build_filters() -> dict:
    """Hunter Discover request body. Defaults: software companies, 1-50 people, US and GB, dev-facing keywords.
    Industry names must match Hunter's list (hunter.io/files/industries.json); headcount values are Hunter's buckets."""
    return {
        "industry": {"include": _csv("DISCOVER_INDUSTRIES", "Software Development")},
        "headcount": _csv("DISCOVER_HEADCOUNT", "1-10,11-50"),
        "headquarters_location": {"include": [{"country": c} for c in _csv("DISCOVER_COUNTRIES", "US,GB")]},
        "keywords": {"include": _csv("DISCOVER_KEYWORDS", "documentation,API,developer"), "match": "any"},
        "limit": 100,
    }


def hunter_discover(filters: dict, key: str):
    """Returns Hunter's list of companies ([{domain, organization, emails_count}]). Raises RuntimeError for anything
    that should stop the run (limits, access, key, bad filters); the message never contains the key."""
    r = requests.post("https://api.hunter.io/v2/discover", params={"api_key": key}, json=filters, timeout=60)
    if r.status_code == 429:
        raise RuntimeError("Hunter usage limit reached")
    if r.status_code in (401, 403):
        raise RuntimeError(f"Hunter refused the request ({r.status_code}): bad API key, rate limit, or plan without Discover")
    if r.status_code != 200:
        try:
            ids = ",".join(str(e.get("id")) for e in (r.json().get("errors") or []))
        except ValueError:
            ids = ""
        raise RuntimeError(f"Hunter Discover returned {r.status_code} {ids}".strip())
    return (r.json() or {}).get("data") or []


def main():
    key = os.environ.get("HUNTER_API_KEY")
    if not key:
        log("HUNTER_API_KEY is not set, nothing to do")
        return
    q = Queue(credentials(), os.environ["SHEET_ID"], os.environ.get("QUEUE_TAB", "Outreach Queue"))
    seen = {d for d in (domain_of(r.get("Website", "")) for r in q.load()) if d}
    try:
        found = hunter_discover(build_filters(), key)
    except RuntimeError as e:
        log("stopping:", e)
        return
    new = []
    for c in found:
        if len(new) >= LIMIT:
            break
        domain = domain_of(c.get("domain") or "")
        if not domain or domain in seen:
            continue
        if int((c.get("emails_count") or {}).get("personal", MIN_PERSONAL) or 0) < MIN_PERSONAL:
            continue
        seen.add(domain)  # also dedupes inside this batch
        new.append({"Company": (c.get("organization") or domain).strip(), "Website": f"https://{domain}",
                    "Status": NEW_STATUS})
    log(f"Hunter returned {len(found)} companies, {len(new)} are new (limit {LIMIT})")
    for row in new:
        log(f"  {'would add' if DRY else 'adding'}: {row['Company']} ({row['Website']})")
    if new and not DRY:
        q.append(new)


if __name__ == "__main__":
    main()
