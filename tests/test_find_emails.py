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


def _run(monkeypatch, rows, lookup):
    q = FakeQueue(rows)
    monkeypatch.setenv("HUNTER_API_KEY", "k")
    monkeypatch.setenv("SHEET_ID", "s")
    monkeypatch.setattr(find_emails, "Queue", lambda *a, **k: q)
    monkeypatch.setattr(find_emails, "credentials", lambda: None)
    monkeypatch.setattr(find_emails, "hunter_lookup", lookup)
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
            lead(**{find_emails.FOUNDER_COL: "Not found"})]
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
