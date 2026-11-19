import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import discover_leads  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(__file__))


class FakeQueue:
    def __init__(self, rows):
        self.rows, self.appended = rows, []

    def load(self):
        return self.rows

    def append(self, rows):
        self.appended.extend(rows)


def co(domain, name=None, personal=5):
    return {"domain": domain, "organization": name or domain, "emails_count": {"personal": personal, "total": personal}}


def _run(monkeypatch, sheet_rows, found, dry=False, limit=10):
    q = FakeQueue(sheet_rows)
    monkeypatch.setenv("HUNTER_API_KEY", "k")
    monkeypatch.setenv("SHEET_ID", "s")
    monkeypatch.setattr(discover_leads, "Queue", lambda *a, **k: q)
    monkeypatch.setattr(discover_leads, "credentials", lambda: None)
    monkeypatch.setattr(discover_leads, "DRY", dry)
    monkeypatch.setattr(discover_leads, "LIMIT", limit)

    def fake(filters, key):
        if isinstance(found, Exception):
            raise found
        return found
    monkeypatch.setattr(discover_leads, "hunter_discover", fake)
    discover_leads.main()
    return q.appended


def test_skips_domains_already_in_sheet_ignoring_www_case_and_scheme(monkeypatch):
    sheet = [{"Website": "https://www.Acme.com/"}, {"Website": "beta.io"}, {"Website": ""}]
    added = _run(monkeypatch, sheet, [co("acme.com"), co("WWW.BETA.IO"), co("new.dev"), co("new.dev")])
    assert [r["Website"] for r in added] == ["https://new.dev"]  # also deduped inside the batch


def test_limit_caps_new_rows(monkeypatch):
    added = _run(monkeypatch, [], [co(f"c{i}.com") for i in range(30)], limit=3)
    assert len(added) == 3
    assert len(_run(monkeypatch, [], [co(f"c{i}.com") for i in range(30)])) == 10  # default-sized limit


def test_status_and_fields_never_verified_email(monkeypatch):
    added = _run(monkeypatch, [], [co("a.com", "Acme"), co("b.com")])
    assert added[0] == {"Company": "Acme", "Website": "https://a.com", "Status": "Needs review"}
    assert all(set(r) == {"Company", "Website", "Status"} for r in added)
    assert added[1]["Company"] == "b.com"  # falls back to the domain when Hunter has no name


def test_skips_companies_with_no_known_people(monkeypatch):
    assert _run(monkeypatch, [], [co("a.com", personal=0), co("b.com", personal=2)])[0]["Website"] == "https://b.com"


def test_dry_run_writes_nothing(monkeypatch):
    assert _run(monkeypatch, [], [co("a.com")], dry=True) == []


@pytest.mark.parametrize("msg", ["Hunter usage limit reached", "Hunter refused the request (403)"])
def test_limit_or_access_errors_stop_cleanly(monkeypatch, msg):
    assert _run(monkeypatch, [], RuntimeError(msg)) == []  # no exception, nothing written


def test_filters_have_defaults_and_empty_env_uses_them(monkeypatch):
    for v in ("DISCOVER_INDUSTRIES", "DISCOVER_HEADCOUNT", "DISCOVER_COUNTRIES", "DISCOVER_KEYWORDS"):
        monkeypatch.setenv(v, "")  # GitHub passes unset variables as empty strings
    f = discover_leads.build_filters()
    assert f["industry"]["include"] == ["Software Development"]
    assert f["headcount"] == ["1-10", "11-50"]
    assert f["headquarters_location"]["include"] == [{"country": "US"}, {"country": "GB"}]
    assert f["keywords"] == {"include": ["documentation", "API", "developer"], "match": "any"}
    monkeypatch.setenv("DISCOVER_COUNTRIES", "DE, FR")
    assert discover_leads.build_filters()["headquarters_location"]["include"] == [{"country": "DE"}, {"country": "FR"}]


def test_workflow_runs_only_find_emails_with_no_llm_or_discover():
    text = open(os.path.join(ROOT, ".github", "workflows", "daily.yml"), encoding="utf-8").read()
    for banned in ("run_daily", "discover_leads", "DISCOVER_", "GEMINI", "ANTHROPIC", "LLM_"):
        assert banned not in text
    assert text.startswith("name: daily-email-finder")
    assert text.count("python find_emails.py") == 1
    assert "HUNTER_API_KEY" in text and "GOOGLE_REFRESH_TOKEN" in text
