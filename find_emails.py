"""Suggests an email address for leads that have a website but no email yet.
Uses the Hunter.io Email Finder API when there is a founder name, and falls back to Hunter Domain Search (pick the
founder/CEO from the domain's known people) when there is not. Free plan has a small monthly credit allowance.

If PROSPEO_API_KEY is set, Prospeo takes over for the rest of the run as soon as Hunter cannot continue (credits used
up, rate limit, bad key): Enrich Person when there is a founder name, else Search Person + Enrich Person for the
founder/CEO. Prospeo only suggests verified emails by default (PROSPEO_ONLY_VERIFIED=0 to allow others).

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

STATUSES = ("Needs email", "Needs review")  # rows we look up; the bot never changes Status itself
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


def _candidates(emails):
    """Named, non-generic people with a matching role: (rank, score, email, name, position)."""
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
        name = " ".join(p for p in (e.get("first_name"), e.get("last_name")) if p).strip()
        if rank is None or not name:
            continue
        yield rank, score, addr, name, position


def pick_person(emails, min_score):
    """Pick ONE named person from Domain Search results: founder/CEO preferred, then owner/head of product.
    Ignores generic addresses and anything under min_score. Returns (email, score, name, position) or None."""
    ok = [c for c in _candidates(emails) if c[1] >= min_score]
    if not ok:
        return None
    rank, score, addr, name, position = min(ok, key=lambda c: (c[0], -c[1]))
    return addr, score, name, position


def best_score(emails):
    """Highest confidence among role-matching people (0 if none), written next to 'none found'."""
    return max((c[1] for c in _candidates(emails)), default=0)


def hunter_credits_left(key):
    """Credits (or searches) left this billing period from the free GET /v2/account call, else None.
    Hunter's lookup responses only say how much that one request used, so this asks the account once per run."""
    try:
        r = requests.get("https://api.hunter.io/v2/account", params={"api_key": key}, timeout=30)
        if r.status_code != 200:
            return None
        reqs = ((r.json() or {}).get("data") or {}).get("requests") or {}
        for kind in ("credits", "searches"):
            b = reqs.get(kind) or {}
            if b.get("available") is not None and b.get("used") is not None:
                return max(int(b["available"]) - int(b["used"]), 0)
    except (requests.RequestException, ValueError, TypeError):
        pass
    return None


PROSPEO_URL = "https://api.prospeo.io"
PROSPEO_ONLY_VERIFIED = (os.environ.get("PROSPEO_ONLY_VERIFIED") or "1") != "0"  # default: verified emails only
_TWO_PART_TLDS = {"co", "com", "org", "net", "gov", "ac", "edu"}


def root_domain(domain: str) -> str:
    """Prospeo wants root domains only: 'docs.acme.io' -> 'acme.io', 'shop.acme.co.uk' -> 'acme.co.uk'."""
    parts = domain.split(".")
    if len(parts) > 2:
        keep = 3 if parts[-2] in _TWO_PART_TLDS and len(parts[-1]) == 2 else 2
        return ".".join(parts[-keep:])
    return domain


def _prospeo_post(path, body, key):
    """POST to Prospeo. Returns the parsed JSON body (always a dict). Raises RuntimeError when the run should stop
    using Prospeo (rate limit, credits used up, bad key). The key only goes in the X-KEY header, never in a message."""
    r = requests.post(PROSPEO_URL + path, headers={"X-KEY": key, "Content-Type": "application/json"},
                      json=body, timeout=60)
    if r.status_code == 429:
        raise RuntimeError("Prospeo rate limit reached")
    try:
        data = r.json() or {}
    except ValueError:
        data = {}
    code = data.get("error_code")
    if code == "INSUFFICIENT_CREDITS":
        raise RuntimeError("Prospeo credits used up")
    if code == "INVALID_API_KEY":
        raise RuntimeError("Prospeo rejected the API key")
    return data


def _prospeo_email(data):
    """('email', 'STATUS') from an enrich-person response, or None if it has no usable address."""
    if data.get("error"):
        return None
    mail = ((data.get("person") or {}).get("email")) or {}
    addr = (mail.get("email") or "").strip()
    if "@" not in addr or "*" in addr:  # masked or missing: not usable
        return None
    return addr, str(mail.get("status") or "UNKNOWN")


def prospeo_find(first, last, domain, key):
    """Email Finder equivalent. Returns (email, status) / "" for no match / None on an unexpected error.
    1 credit only when an email is found."""
    data = _prospeo_post("/enrich-person", {"only_verified_email": PROSPEO_ONLY_VERIFIED, "data": {
        "first_name": first, "last_name": last, "company_website": root_domain(domain)}}, key)
    if data.get("error") and data.get("error_code") != "NO_MATCH":
        return None
    return _prospeo_email(data) or ""


PROSPEO_TITLES = ["Founder", "Co-Founder", "CEO", "Chief Executive Officer", "Owner"]


def prospeo_find_by_domain(domain, key):
    """Domain Search equivalent: find the founder/CEO at the domain (search, 1 credit if any result), then reveal that
    one person's email (1 credit if found). Returns (email, status, name, position) / "" / None on an error."""
    data = _prospeo_post("/search-person", {"page": 1, "filters": {
        "company": {"websites": {"include": [root_domain(domain)]}},
        "person_job_title": {"include": PROSPEO_TITLES, "match_mode": "CONTAINS"},
        "max_person_per_company": 5}}, key)
    if data.get("error"):
        return "" if data.get("error_code") == "NO_RESULTS" else None
    best = None
    for item in data.get("results") or []:
        p = (item or {}).get("person") or {}
        rank = _role_rank(p.get("current_job_title") or "")
        if rank is None or not p.get("person_id") or not (p.get("full_name") or p.get("first_name")):
            continue
        if best is None or rank < best[0]:
            best = (rank, p)
    if best is None:
        return ""
    p = best[1]
    got = _prospeo_post("/enrich-person", {"only_verified_email": PROSPEO_ONLY_VERIFIED,
                                           "data": {"person_id": p["person_id"]}}, key)
    if got.get("error") and got.get("error_code") != "NO_MATCH":
        return None
    hit = _prospeo_email(got)
    if not hit:
        return ""
    name = p.get("full_name") or " ".join(x for x in (p.get("first_name"), p.get("last_name")) if x)
    return hit[0], hit[1], name, (p.get("current_job_title") or "")


def prospeo_credits_left(key):
    """remaining_credits from the free GET /account-information call, else None."""
    try:
        r = requests.get(PROSPEO_URL + "/account-information", headers={"X-KEY": key}, timeout=30)
        if r.status_code != 200:
            return None
        v = ((r.json() or {}).get("response") or {}).get("remaining_credits")
        return None if v is None else int(v)
    except (requests.RequestException, ValueError, TypeError):
        return None


def _hunter_result(r, name, domain, key):
    """('email', 'confidence text', 'note') | ('none', 'score', '') | None when the lookup failed (nothing written)."""
    if name:  # a founder name exists: Email Finder
        email, score = hunter_lookup(name[0], name[1], domain, key)
        note = ""
    else:  # no usable founder name: Domain Search for a founder/CEO
        emails = hunter_domain_search(domain, key)
        if emails is None:
            return None
        hit = pick_person(emails, MIN_SCORE)
        email, score = (hit[0], hit[1]) if hit else (None, best_score(emails))
        note = f", {hit[3]}" if hit else ""
    if not email or score < MIN_SCORE:
        return "none", str(score), f"score {score}"
    return email, f"{score} (check before use)", f", score {score}{note}"


def _prospeo_result(r, name, domain, key):
    """Same shape as _hunter_result. Prospeo has no numeric score, so the sheet gets its email status instead."""
    if name:
        hit = prospeo_find(name[0], name[1], domain, key)
        note_extra = ""
    else:
        hit = prospeo_find_by_domain(domain, key)
        note_extra = f", {hit[3]}" if hit else ""
    if hit is None:
        return None
    if hit == "":
        return "none", "no match (Prospeo)", "Prospeo: no match"
    return hit[0], f"{hit[1]} via Prospeo (check before use)", f", Prospeo {hit[1]}{note_extra}"


def main():
    hkey, pkey = os.environ.get("HUNTER_API_KEY"), os.environ.get("PROSPEO_API_KEY")
    if not hkey and not pkey:
        log("Neither HUNTER_API_KEY nor PROSPEO_API_KEY is set, nothing to do")
        return
    q = Queue(credentials(), os.environ["SHEET_ID"], os.environ.get("QUEUE_TAB", "Outreach Queue"))
    rows = q.load()
    use_prospeo = not hkey  # Hunter first; Prospeo takes over for the rest of the run once Hunter cannot continue
    checked = suggested = none_found = failed = via_prospeo = 0  # checked = lookups made, against EMAIL_LOOKUP_LIMIT
    stopped = None

    def save(r, email, conf):
        if not DRY:
            q.update(r["_row"], **{"Suggested email": email, "Email confidence": conf})

    for r in rows:
        if checked >= LIMIT or stopped:
            break
        if r.get("Status") not in STATUSES or r.get("Verified email", "").strip() or r.get("Suggested email", "").strip():
            continue
        name, domain = founder_name(r.get(FOUNDER_COL, "")), domain_of(r.get("Website", ""))
        if not domain:
            continue
        while True:
            try:
                res = (_prospeo_result if use_prospeo else _hunter_result)(r, name, domain, pkey if use_prospeo else hkey)
                break
            except RuntimeError as e:
                if not use_prospeo and pkey:  # Hunter ran out (or refused): retry this row on Prospeo
                    use_prospeo = True
                    log(f"Hunter stopped ({e}); switching to Prospeo for the rest of this run")
                    continue
                stopped = str(e)
                log("stopping:", e)
                res = False
                break
        if res is False:
            break
        checked += 1
        via_prospeo += use_prospeo
        if res is None:
            failed += 1
            log(f"[{r['Company']}] lookup failed, skipped")
        elif res[0] == "none":
            none_found += 1
            log(f"[{r['Company']}] no confident match ({res[2]})")
            save(r, "none found", res[1])
        else:
            suggested += 1
            log(f"[{r['Company']}] suggested {res[0]} ({res[2].lstrip(', ')})")
            save(r, res[0], res[1])

    left = hunter_credits_left(hkey) if hkey else None
    summary = (f"Summary{' (dry run, nothing written)' if DRY else ''}: rows checked {checked}, suggested {suggested}, "
               f"none found {none_found}, failed {failed}, credits left {'unknown' if left is None else left}")
    if pkey:
        pleft = prospeo_credits_left(pkey)
        summary += f", via Prospeo {via_prospeo}, Prospeo credits left {'unknown' if pleft is None else pleft}"
    log(summary + (f". Stopped early: {stopped}" if stopped else ""))


if __name__ == "__main__":
    main()
