"""Suggests an email address for leads that have a website but no email yet.
Uses the Hunter.io Email Finder API when there is a founder name, and falls back to Hunter Domain Search (pick the
founder/CEO from the domain's known people) when there is not. Free plan has a small monthly credit allowance.

It ONLY writes the 'Suggested email' and 'Email confidence' columns (plus 'Notes', only if empty, on a Domain
Search hit, as "Hunter: Name, Position"). It never fills 'Verified email' and never
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

STATUSES = ("Needs email", "Needs review")  # rows we look up; the bot never changes Status itself
SKIP_WORDS =("not found", "not confirmed", "skipped", "see ")


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


def _check_limits(r):
    """Stop the whole run (never write 'none found') when Hunter says the key, rate limit or credits are the problem."""
    if r.status_code == 429:
        raise RuntimeError("Hunter usage limit reached (credits used up)")
    if r.status_code == 403:
        raise RuntimeError("Hunter rate limit reached")
    if r.status_code == 401:
        raise RuntimeError("Hunter rejected the API key")


def hunter_lookup(first, last, domain, key):
    """Returns (email, score) or (None, 0). Costs one Hunter credit only when an address is found."""
    r = requests.get("https://api.hunter.io/v2/email-finder",
                     params={"domain": domain, "first_name": first, "last_name": last, "api_key": key}, timeout=30)
    _check_limits(r)
    if r.status_code != 200:
        return None, 0
    data = (r.json() or {}).get("data") or {}
    return data.get("email"), int(data.get("score") or 0)


def hunter_domain_search(domain, key):
    """Returns the list of email entries Hunter knows for the domain ([] if none), or None on an unexpected error.
    Costs credits (about one per 10 addresses returned), so callers count it against EMAIL_LOOKUP_LIMIT."""
    r = requests.get("https://api.hunter.io/v2/domain-search",
                     params={"domain": domain, "limit": 10, "api_key": key}, timeout=30)
    _check_limits(r)
    if r.status_code != 200:
        return None
    return ((r.json() or {}).get("data") or {}).get("emails") or []


GENERIC_LOCALPARTS = {"info", "support", "hello", "hi", "contact", "sales", "team", "admin", "help", "office",
                      "mail", "press", "careers", "jobs", "hr", "billing", "marketing", "enquiries", "inquiries",
                      "customerservice", "noreply", "no-reply"}
# Lower rank is preferred: founder / CEO first, then owner / head of product.
_RANK0 = re.compile(r"\bco-?founders?\b|\bfounders?\b|\bceo\b|\bchief executive\b", re.I)
_RANK1 = re.compile(r"(?<!product )\bowner\b|\bhead of product\b", re.I)


def _role_rank(position: str):
    if _RANK0.search(position or ""):
        return 0
    if _RANK1.search(position or ""):
        return 1
    return None


def pick_person(emails, min_score):
    """Pick ONE named person from Domain Search results: founder/CEO preferred, then owner/head of product.
    Ignores generic addresses and anything under min_score. Returns (email, score, name, position) or None."""
    best = None
    for e in emails or []:
        addr = (e.get("value") or "").strip()
        if not addr or "@" not in addr or e.get("type") == "generic":
            continue
        if addr.split("@")[0].lower() in GENERIC_LOCALPARTS:
            continue
        position = (e.get("position_raw") or e.get("position") or "").strip()
        rank = _role_rank(position)
        try:
            score = int(e.get("confidence") or 0)
        except (TypeError, ValueError):
            score = 0
        if rank is None or score < min_score:
            continue
        name = " ".join(p for p in (e.get("first_name"), e.get("last_name")) if p).strip()
        if not name:
            continue
        if best is None or (rank, -score) < best[0]:
            best = ((rank, -score), addr, score, name, position)
    return None if best is None else best[1:]


def main():
    key = os.environ.get("HUNTER_API_KEY")
    if not key:
        log("HUNTER_API_KEY is not set, nothing to do")
        return
    q = Queue(credentials(), os.environ["SHEET_ID"], os.environ.get("QUEUE_TAB", "Outreach Queue"))
    rows = q.load()
    done = 0  # lookups of either type, against EMAIL_LOOKUP_LIMIT
    for r in rows:
        if done >= LIMIT:
            break
        if r.get("Status") not in STATUSES or r.get("Verified email", "").strip() or r.get("Suggested email", "").strip():
            continue
        name, domain = founder_name(r.get(FOUNDER_COL, "")), domain_of(r.get("Website", ""))
        if not domain:
            continue
        if not name:
            # No usable founder name: fall back to Domain Search.
            try:
                emails = hunter_domain_search(domain, key)
            except RuntimeError as e:
                log("stopping:", e)
                break
            done += 1
            if emails is None:
                log(f"[{r['Company']}] domain search failed, skipped")
                continue
            hit = pick_person(emails, MIN_SCORE)
            if not hit:
                log(f"[{r['Company']}] domain search: no confident founder/CEO match")
                if not DRY:
                    q.update(r["_row"], **{"Suggested email": "none found", "Email confidence": "0"})
                continue
            email, score, person, position = hit
            log(f"[{r['Company']}] domain search suggested {email} (score {score}, {position})")
            if not DRY:
                fields = {"Suggested email": email, "Email confidence": f"{score} (check before use)"}
                if "Notes" in r and not r["Notes"].strip():  # never overwrite an existing note
                    fields["Notes"] = f"Hunter: {person}, {position}"
                q.update(r["_row"], **fields)
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
