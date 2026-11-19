import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import find_emails  # noqa: E402


def test_founder_name_parsing():
    f = find_emails.founder_name
    assert f("Dima Goncharov, co-founder & CEO (GitHub bio, Crunchbase)") == ("Dima", "Goncharov")
    assert f("Matt Aitken, CEO (Crunchbase); Eric Allam, co-founder") == ("Matt", "Aitken")
    assert f("Julie MacDonell appears as founder in articles, confirm") == ("Julie", "MacDonell")
    assert f("Ben Milne founded it; a 2026-era article names Brady Harris CEO") == ("Ben", "Milne")
    assert f("Not found") is None
    assert f("Not confirmed (search suggests Andrew Berman)") is None
    assert f("Skipped (already has AI help)") is None
    assert f("Prince") is None
    assert f("") is None


def test_domain():
    d = find_emails.domain_of
    assert d("https://www.metriport.com") == "metriport.com"
    assert d("https://docs.example.io/x") == "docs.example.io"
    assert d("polygon.io") == "polygon.io"
    assert d("") is None


class FakeQueue:
    def __init__(self, rows):
        self.rows, self.updates = rows, []

    def load(self):
        return self.rows

    def update(self, row, **f):
        self.updates.append((row, f))


def _no_domain_search(*a):
    raise AssertionError("domain search must not be called here")


def _run(monkeypatch, rows, lookup, domain_search=_no_domain_search):
    q = FakeQueue(rows)
    monkeypatch.setenv("HUNTER_API_KEY", "k")
    monkeypatch.setenv("SHEET_ID", "s")
    monkeypatch.setattr(find_emails, "Queue", lambda *a, **k: q)
    monkeypatch.setattr(find_emails, "credentials", lambda: None)
    monkeypatch.setattr(find_emails, "hunter_lookup", lookup)
    monkeypatch.setattr(find_emails, "hunter_domain_search", domain_search)
    monkeypatch.setattr(find_emails, "hunter_credits_left", lambda key: 42)
    monkeypatch.setattr(find_emails, "DRY", False)
    find_emails.main()
    return q.updates


def lead(**kw):
    base = {"_row": 2, "Company": "A", "Status": "Needs email", "Verified email": "", "Suggested email": "",
            "Website": "https://www.a.com", find_emails.FOUNDER_COL: "Jo Smith, CEO"}
    base.update(kw)
    return base


def test_writes_suggestion_but_never_verified_or_status(monkeypatch):
    ups = _run(monkeypatch, [lead()], lambda f, l, d, k: ("jo@a.com", 91))
    assert ups[0][0] == 2
    fields = ups[0][1]
    assert fields["Suggested email"] == "jo@a.com"
    assert "Verified email" not in fields and "Status" not in fields


def test_low_score_is_not_suggested(monkeypatch):
    ups = _run(monkeypatch, [lead()], lambda f, l, d, k: ("jo@a.com", 40))
    assert ups[0][1]["Suggested email"] == "none found"


def test_skips_rows_that_already_have_email_or_wrong_status(monkeypatch):
    calls = []
    rows = [lead(**{"Verified email": "x@a.com"}), lead(Status="Sent"), lead(**{"Suggested email": "y@a.com"}),
            lead(Website="")]
    ups = _run(monkeypatch, rows, lambda *a: calls.append(a) or (None, 0))
    assert not calls and not ups


def test_limit_and_credit_exhaustion(monkeypatch):
    monkeypatch.setattr(find_emails, "LIMIT", 1)
    rows = [lead(_row=2), lead(_row=3)]
    ups = _run(monkeypatch, rows, lambda *a: ("a@a.com", 90))
    assert len(ups) == 1

    monkeypatch.setattr(find_emails, "LIMIT", 10)

    def boom(*a):
        raise RuntimeError("out of credits")
    assert _run(monkeypatch, [lead()], boom) == []


# ---- Domain Search fallback (no founder name) ----

def person(value, position, conf=90, first="Al", last="Lee", **kw):
    d = {"value": value, "type": "personal", "confidence": conf, "first_name": first, "last_name": last,
         "position": position}
    d.update(kw)
    return d


def nofounder(**kw):
    return lead(**{find_emails.FOUNDER_COL: "Not found", "Notes": "", **kw})


def test_pick_person_prefers_founder_over_other_roles():
    emails = [person("cto@a.com", "CTO"), person("owner@a.com", "Owner", 95),
              person("ceo@a.com", "CEO", 80, first="Cy"), person("f@a.com", "Co-Founder", 75, first="Fay")]
    got = find_emails.pick_person(emails, 70)
    assert got[0] in ("ceo@a.com", "f@a.com")          # rank 0 beats the higher-scored owner
    assert got[0] == "ceo@a.com"                        # same rank: higher confidence wins
    only_lower = [person("cto@a.com", "CTO"), person("h@a.com", "Head of Product", 85)]
    assert find_emails.pick_person(only_lower, 70)[0] == "h@a.com"
    assert find_emails.pick_person([person("p@a.com", "Product Owner")], 70) is None
    assert find_emails.pick_person([person("e@a.com", "Engineer")], 70) is None


def test_pick_person_ignores_generic_addresses():
    emails = [person("info@a.com", "CEO"), person("hello@a.com", "Founder"),
              person("x@a.com", "Founder", type="generic")]
    assert find_emails.pick_person(emails, 70) is None


def test_pick_person_enforces_score_threshold():
    assert find_emails.pick_person([person("f@a.com", "Founder", 69)], 70) is None
    assert find_emails.pick_person([person("f@a.com", "Founder", 70)], 70)[0] == "f@a.com"


def test_domain_search_writes_only_suggested_email_and_confidence(monkeypatch):
    ds = lambda d, k: [person("al@a.com", "Co-founder & CEO", 88)]
    ups = _run(monkeypatch, [nofounder()], lambda *a: (None, 0), ds)
    assert ups[0][1] == {"Suggested email": "al@a.com", "Email confidence": "88 (check before use)"}


def test_domain_search_no_match_marks_none_found_with_best_score(monkeypatch):
    ds = lambda d, k: [person("info@a.com", "CEO", 99), person("dev@a.com", "Engineer", 95),
                       person("f@a.com", "Founder", 55)]
    ups = _run(monkeypatch, [nofounder()], lambda *a: (None, 0), ds)
    assert ups[0][1] == {"Suggested email": "none found", "Email confidence": "55"}
    ups = _run(monkeypatch, [nofounder()], lambda *a: (None, 0), lambda d, k: [])
    assert ups[0][1] == {"Suggested email": "none found", "Email confidence": "0"}


def test_none_found_rows_are_not_looked_up_again(monkeypatch):
    rows = [nofounder(**{"Suggested email": "none found"}), lead(**{"Suggested email": "none found"})]
    assert _run(monkeypatch, rows, lambda *a: ("x@a.com", 90)) == []  # default fakes raise if called


def test_row_without_website_is_skipped(monkeypatch):
    assert _run(monkeypatch, [lead(Website=""), nofounder(Website="")], lambda *a: ("x@a.com", 90)) == []


def test_summary_line(monkeypatch, capsys):
    rows = [lead(_row=2), lead(_row=3), nofounder(_row=4), nofounder(_row=5)]
    results = iter([("jo@a.com", 90), (None, 0)])
    ds = iter([[person("al@a.com", "CEO")], None])
    _run(monkeypatch, rows, lambda *a: next(results), lambda d, k: next(ds))
    out = capsys.readouterr().out
    assert "Summary: rows checked 4, suggested 2, none found 1, failed 1, credits left 42" in out


def test_summary_reports_stop_and_unknown_credits(monkeypatch, capsys):
    def boom(d, k):
        raise RuntimeError("Hunter usage limit reached (credits used up)")
    monkeypatch.setattr(find_emails, "hunter_credits_left", lambda key: None)
    q = FakeQueue([nofounder()])
    monkeypatch.setenv("HUNTER_API_KEY", "k")
    monkeypatch.setenv("SHEET_ID", "s")
    monkeypatch.setattr(find_emails, "Queue", lambda *a, **k: q)
    monkeypatch.setattr(find_emails, "credentials", lambda: None)
    monkeypatch.setattr(find_emails, "hunter_domain_search", boom)
    monkeypatch.setattr(find_emails, "DRY", False)
    find_emails.main()
    out = capsys.readouterr().out
    assert "credits left unknown" in out and "Stopped early: Hunter usage limit reached" in out and q.updates == []


class _Resp:
    def __init__(self, status, body):
        self.status_code, self._b = status, body

    def json(self):
        return self._b


def test_credits_left_parsing_and_failures(monkeypatch):
    def get(body, status=200):
        monkeypatch.setattr(find_emails.requests, "get", lambda *a, **k: _Resp(status, body))
    get({"data": {"requests": {"credits": {"used": 120, "available": 500}}}})
    assert find_emails.hunter_credits_left("k") == 380
    get({"data": {"requests": {"searches": {"used": 5, "available": 25}}}})
    assert find_emails.hunter_credits_left("k") == 20
    get({}, 401)
    assert find_emails.hunter_credits_left("k") is None
    get({"data": {}})
    assert find_emails.hunter_credits_left("k") is None


def test_domain_search_not_used_when_founder_name_exists(monkeypatch):
    ups = _run(monkeypatch, [lead()], lambda f, l, d, k: ("jo@a.com", 91))  # default fake raises if called
    assert set(ups[0][1]) == {"Suggested email", "Email confidence"}


def test_limit_counts_both_lookup_types_together(monkeypatch):
    monkeypatch.setattr(find_emails, "LIMIT", 2)
    calls = []
    ds = lambda d, k: calls.append("ds") or [person("al@a.com", "CEO")]
    lk = lambda *a: calls.append("lk") or ("jo@a.com", 90)
    rows = [lead(_row=2), nofounder(_row=3), nofounder(_row=4), lead(_row=5)]
    ups = _run(monkeypatch, rows, lk, ds)
    assert calls == ["lk", "ds"] and len(ups) == 2


def test_domain_search_credit_exhaustion_stops_cleanly(monkeypatch):
    def boom(d, k):
        raise RuntimeError("Hunter usage limit reached (credits used up)")
    rows = [nofounder(_row=2), lead(_row=3)]
    assert _run(monkeypatch, rows, lambda *a: ("x@a.com", 90), boom) == []


def test_domain_search_error_writes_nothing(monkeypatch):
    assert _run(monkeypatch, [nofounder()], lambda *a: (None, 0), lambda d, k: None) == []


def test_dry_run_writes_nothing(monkeypatch):
    q = FakeQueue([nofounder()])
    monkeypatch.setenv("HUNTER_API_KEY", "k")
    monkeypatch.setenv("SHEET_ID", "s")
    monkeypatch.setattr(find_emails, "Queue", lambda *a, **k: q)
    monkeypatch.setattr(find_emails, "credentials", lambda: None)
    monkeypatch.setattr(find_emails, "hunter_domain_search", lambda d, k: [person("al@a.com", "CEO")])
    monkeypatch.setattr(find_emails, "hunter_credits_left", lambda key: 42)
    monkeypatch.setattr(find_emails, "DRY", True)
    find_emails.main()
    assert q.updates == []


def test_processes_needs_review_and_needs_email_only(monkeypatch):
    rows = [lead(Status="Needs review", _row=2), lead(Status="Needs email", _row=3), lead(Status="Ready", _row=4)]
    ups = _run(monkeypatch, rows, lambda f, l, d, k: ("jo@a.com", 90))
    assert [u[0] for u in ups] == [2, 3]
    assert all("Status" not in u[1] and "Verified email" not in u[1] for u in ups)
