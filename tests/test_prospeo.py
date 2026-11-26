import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
sys.path.insert(0, os.path.dirname(__file__))

import find_emails  # noqa: E402
from test_find_emails import FakeQueue, lead, nofounder  # noqa: E402


@pytest.fixture(autouse=True)
def sleeps(monkeypatch):
    """Never really sleep in tests; record the waits instead."""
    waits = []
    monkeypatch.setattr(find_emails.time, "sleep", waits.append)
    return waits


def hunter_out(*a):
    raise RuntimeError("Hunter usage limit reached (credits used up)")


def _run(monkeypatch, rows, hunter=None, prospeo=None, prospeo_domain=None, dry=False, hkey="h", pkey="p"):
    q = FakeQueue(rows)
    for name, val in (("HUNTER_API_KEY", hkey), ("PROSPEO_API_KEY", pkey)):
        if val:
            monkeypatch.setenv(name, val)
        else:
            monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("SHEET_ID", "s")
    monkeypatch.setattr(find_emails, "Queue", lambda *a, **k: q)
    monkeypatch.setattr(find_emails, "credentials", lambda: None)
    never = lambda *a: (_ for _ in ()).throw(AssertionError("must not be called"))
    monkeypatch.setattr(find_emails, "hunter_lookup", hunter or never)
    monkeypatch.setattr(find_emails, "hunter_domain_search", hunter_out)  # no-name rows: Hunter is out too
    monkeypatch.setattr(find_emails, "prospeo_find", prospeo or never)
    monkeypatch.setattr(find_emails, "prospeo_find_by_domain", prospeo_domain or never)
    monkeypatch.setattr(find_emails, "hunter_credits_left", lambda k: 7)
    monkeypatch.setattr(find_emails, "prospeo_credits_left", lambda k: 99)
    monkeypatch.setattr(find_emails, "DRY", dry)
    find_emails.main()
    return q.updates


def test_switches_to_prospeo_when_hunter_runs_out_and_retries_that_row(monkeypatch, capsys):
    hunter_calls, prospeo_calls = [], []

    def hunter(f, l, d, k):
        hunter_calls.append(d)
        if len(hunter_calls) == 2:
            hunter_out()
        return "jo@a.com", 90

    def prospeo(f, l, d, k):
        prospeo_calls.append(d)
        return "pro@b.com", "VERIFIED"

    rows = [lead(_row=2), lead(_row=3, Website="https://b.com"), lead(_row=4, Website="https://c.com")]
    ups = _run(monkeypatch, rows, hunter, prospeo)
    assert hunter_calls == ["a.com", "b.com"]            # Hunter is not tried again after it ran out
    assert prospeo_calls == ["b.com", "c.com"]           # the row Hunter failed on is retried on Prospeo
    assert [(u[0], u[1]["Suggested email"]) for u in ups] == [(2, "jo@a.com"), (3, "pro@b.com"), (4, "pro@b.com")]
    assert ups[1][1]["Email confidence"] == "VERIFIED via Prospeo (check before use)"
    assert all(set(u[1]) == {"Suggested email", "Email confidence"} for u in ups)  # never Verified email / Status
    out = capsys.readouterr().out
    assert "switching to Prospeo" in out
    assert "rows checked 3, suggested 3, none found 0, failed 0, credits left 7, via Prospeo 2, Prospeo credits left 99" in out


def test_without_prospeo_key_hunter_running_out_still_stops(monkeypatch, capsys):
    ups = _run(monkeypatch, [lead(_row=2), lead(_row=3)], hunter_out, pkey=None)
    assert ups == []
    assert "Stopped early: Hunter usage limit reached" in capsys.readouterr().out


def test_prospeo_no_match_writes_none_found_so_row_is_not_repeated(monkeypatch):
    ups = _run(monkeypatch, [lead()], hunter_out, lambda *a: "")
    assert ups[0][1] == {"Suggested email": "none found", "Email confidence": "no match (Prospeo)"}


def test_prospeo_out_of_credits_stops_cleanly(monkeypatch, capsys):
    def boom(*a):
        raise RuntimeError("Prospeo credits used up")
    ups = _run(monkeypatch, [lead(_row=2), lead(_row=3)], hunter_out, boom)
    assert ups == []
    out = capsys.readouterr().out
    assert "stopping: Prospeo credits used up" in out and "Stopped early: Prospeo credits used up" in out


def test_prospeo_failure_is_skipped_without_writing(monkeypatch):
    assert _run(monkeypatch, [lead()], hunter_out, lambda *a: None) == []


def test_only_prospeo_key_starts_on_prospeo(monkeypatch):
    ups = _run(monkeypatch, [lead()], prospeo=lambda *a: ("x@a.com", "VERIFIED"), hkey=None)
    assert ups[0][1]["Suggested email"] == "x@a.com"


def test_no_name_rows_use_prospeo_domain_lookup(monkeypatch):
    ups = _run(monkeypatch, [nofounder()], hunter_out, prospeo_domain=lambda d, k: ("al@a.com", "VERIFIED", "Al Lee", "CEO"))
    assert ups[0][1] == {"Suggested email": "al@a.com", "Email confidence": "VERIFIED via Prospeo (check before use)"}


def test_limit_is_shared_and_dry_run_writes_nothing(monkeypatch):
    monkeypatch.setattr(find_emails, "LIMIT", 2)
    calls = []
    rows = [lead(_row=i) for i in range(2, 7)]
    ups = _run(monkeypatch, rows, hunter_out, lambda *a: calls.append(a) or ("x@a.com", "VERIFIED"), dry=True)
    assert len(calls) == 2 and ups == []


# ---- the HTTP layer, with a fake requests module (no network) ----

class Resp:
    def __init__(self, status=200, body=None, headers=None):
        self.status_code, self._b = status, body if body is not None else {}
        self.headers = headers or {}

    def json(self):
        return self._b


class FakePost:
    def __init__(self, *responses):
        self.responses, self.calls = list(responses), []

    def __call__(self, url, headers=None, json=None, timeout=None):
        self.calls.append((url, headers, json))
        return self.responses.pop(0)


def ok_email(addr="jo@a.com", status="VERIFIED"):
    return Resp(200, {"error": False, "person": {"email": {"status": status, "revealed": True, "email": addr}}})


def test_prospeo_find_request_shape_and_result(monkeypatch):
    post = FakePost(ok_email())
    monkeypatch.setattr(find_emails.requests, "post", post)
    monkeypatch.setattr(find_emails, "PROSPEO_ONLY_VERIFIED", True)
    assert find_emails.prospeo_find("Jo", "Smith", "docs.a.com", "SECRET") == ("jo@a.com", "VERIFIED")
    url, headers, body = post.calls[0]
    assert url == "https://api.prospeo.io/enrich-person" and headers["X-KEY"] == "SECRET"
    assert body == {"only_verified_email": True,
                    "data": {"first_name": "Jo", "last_name": "Smith", "company_website": "a.com"}}


def test_prospeo_find_no_match_masked_and_errors(monkeypatch):
    def run(resp):
        monkeypatch.setattr(find_emails.requests, "post", FakePost(resp))
        return find_emails.prospeo_find("Jo", "Smith", "a.com", "k")
    assert run(Resp(400, {"error": True, "error_code": "NO_MATCH"})) == ""
    assert run(ok_email("jo.*****@a.com")) == ""                       # masked address is never suggested
    assert run(Resp(400, {"error": True, "error_code": "INVALID_DATAPOINTS"})) is None
    for resp, msg in ((Resp(400, {"error": True, "error_code": "INSUFFICIENT_CREDITS"}), "credits used up"),
                      (Resp(400, {"error": True, "error_code": "INVALID_API_KEY"}), "rejected the API key"),
                      (Resp(429, {}), "rate limit")):
        monkeypatch.setattr(find_emails.requests, "post", FakePost(*[resp] * (find_emails.PROSPEO_RETRIES + 1)))
        with pytest.raises(RuntimeError, match=msg) as exc:
            find_emails.prospeo_find("Jo", "Smith", "a.com", "SECRET")
        assert "SECRET" not in str(exc.value)


def search_resp(*people):
    return Resp(200, {"error": False, "results": [{"person": p} for p in people]})


def pp(pid, title, name="Al Lee"):
    return {"person_id": pid, "full_name": name, "current_job_title": title}


def test_prospeo_domain_search_picks_founder_then_reveals_only_that_person(monkeypatch):
    post = FakePost(search_resp(pp("p1", "Head of Product"), pp("p2", "Co-Founder & CEO", "Fay Wu"), pp("p3", "Engineer")),
                    ok_email("fay@a.com"))
    monkeypatch.setattr(find_emails.requests, "post", post)
    got = find_emails.prospeo_find_by_domain("www.a.com", "k")
    assert got == ("fay@a.com", "VERIFIED", "Fay Wu", "Co-Founder & CEO")
    assert post.calls[0][0].endswith("/search-person")
    assert post.calls[0][2]["filters"]["company"] == {"websites": {"include": ["a.com"]}}
    assert post.calls[1][2]["data"] == {"person_id": "p2"}              # exactly one reveal


def test_prospeo_domain_search_no_results_plan_error_and_no_role(monkeypatch):
    def run(*resps):
        monkeypatch.setattr(find_emails.requests, "post", FakePost(*resps))
        return find_emails.prospeo_find_by_domain("a.com", "k")
    assert run(Resp(400, {"error": True, "error_code": "NO_RESULTS"})) == ""
    assert run(Resp(400, {"error": True, "error_code": "PLAN_REQUIRED", "filter_error": "x (PRO+)"})) is None
    assert run(search_resp(pp("p1", "Engineer"))) == ""                  # nobody matching: no reveal credit spent
    assert run(search_resp(pp("p1", "CEO")), Resp(400, {"error": True, "error_code": "NO_MATCH"})) == ""


def test_root_domain():
    r = find_emails.root_domain
    assert r("docs.acme.io") == "acme.io" and r("acme.io") == "acme.io"
    assert r("shop.acme.co.uk") == "acme.co.uk" and r("acme.co.uk") == "acme.co.uk"


def test_prospeo_credits_left(monkeypatch):
    def get(resp):
        monkeypatch.setattr(find_emails.requests, "get", lambda *a, **k: resp)
    get(Resp(200, {"error": False, "response": {"remaining_credits": 250, "used_credits": 50}}))
    assert find_emails.prospeo_credits_left("k") == 250
    get(Resp(400, {"error": True}))
    assert find_emails.prospeo_credits_left("k") is None
    get(Resp(200, {"response": {}}))
    assert find_emails.prospeo_credits_left("k") is None


def test_requests_are_paced_and_429_is_retried_with_a_wait(monkeypatch, sleeps):
    post = FakePost(Resp(429, {}), Resp(429, {}), ok_email())
    monkeypatch.setattr(find_emails.requests, "post", post)
    assert find_emails.prospeo_find("Jo", "Smith", "a.com", "k") == ("jo@a.com", "VERIFIED")
    assert len(post.calls) == 3                                   # two 429s, then success: the run carries on
    assert sleeps == [find_emails.PROSPEO_DELAY, 2, find_emails.PROSPEO_DELAY, 4, find_emails.PROSPEO_DELAY]


def test_429_waits_for_the_minute_reset_header(monkeypatch, sleeps):
    limited = Resp(429, {}, {"x-minute-request-left": "0", "x-minute-reset-seconds": "17"})
    monkeypatch.setattr(find_emails.requests, "post", FakePost(limited, ok_email()))
    find_emails.prospeo_find("Jo", "Smith", "a.com", "k")
    assert 18 in sleeps


def test_429_gives_up_after_the_retries_and_stops_the_run_cleanly(monkeypatch):
    post = FakePost(*[Resp(429, {}) for _ in range(find_emails.PROSPEO_RETRIES + 1)])
    monkeypatch.setattr(find_emails.requests, "post", post)
    with pytest.raises(RuntimeError, match="rate limit reached"):
        find_emails.prospeo_find("Jo", "Smith", "a.com", "k")
    assert len(post.calls) == find_emails.PROSPEO_RETRIES + 1


def test_429_with_no_daily_requests_left_stops_without_waiting(monkeypatch, sleeps):
    post = FakePost(Resp(429, {}, {"x-daily-request-left": "0"}))
    monkeypatch.setattr(find_emails.requests, "post", post)
    with pytest.raises(RuntimeError, match="daily request limit"):
        find_emails.prospeo_find("Jo", "Smith", "a.com", "k")
    assert len(post.calls) == 1 and sleeps == [find_emails.PROSPEO_DELAY]
