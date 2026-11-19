import os
import sys
from datetime import date, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
os.environ.setdefault("DRY_RUN", "0")

import logic  # noqa: E402
import run_daily  # noqa: E402

SIG = "Yahya Ahmed\nAI Developer\nsite"


def test_followup_schedule():
    d = date(2026, 11, 20)
    assert not logic.followup_due(d, 0, d + timedelta(days=2))
    assert logic.followup_due(d, 0, d + timedelta(days=3))
    assert not logic.followup_due(d, 1, d + timedelta(days=6))
    assert logic.followup_due(d, 1, d + timedelta(days=7))
    assert logic.followup_due(d, 2, d + timedelta(days=14))
    assert not logic.followup_due(d, 3, d + timedelta(days=60))  # stops after three
    assert logic.next_followup_date(d, 3) is None


def test_validate_generation():
    good = {"observation": "x", "source_url": "https://a.com", "opener": "Short opener here.",
            "sample_question": "q?", "has_ai_assistant": False}
    assert logic.validate_generation(good, ["https://a.com"])[0]
    assert not logic.validate_generation({**good, "source_url": "https://b.com"}, ["https://a.com"])[0]
    assert not logic.validate_generation({**good, "opener": "word " * 60}, ["https://a.com"])[0]
    assert not logic.validate_generation({**good, "opener": "see https://x.com"}, ["https://a.com"])[0]
    bad = dict(good)
    del bad["sample_question"]
    assert not logic.validate_generation(bad, ["https://a.com"])[0]


def test_emails():
    subj, body = logic.build_initial_email("Finch", "Jo Smith", "Opener line.", SIG)
    assert subj == "A free assistant built on Finch's docs"
    assert body.startswith("Hi Jo,") and 'Reply "DEMO"' in body and logic.OPTOUT in body and body.endswith("site")
    assert logic.build_followup_email(1, "Finch", "", "", SIG).startswith("Hi there,")
    assert "should I close this out" in logic.build_followup_email(3, "Finch", "", "", SIG)
    assert "http" not in body


def test_helpers():
    assert logic.valid_email("a@b.co") and not logic.valid_email("a@b") and not logic.valid_email("")
    assert logic.chat_markers("<script src='https://widget.intercom.io/x'>") == ["Intercom"]
    assert logic.chat_markers("<html>nothing</html>") == []


class FakeQueue:
    def __init__(self):
        self.updates = []

    def update(self, row, **f):
        self.updates.append((row, f))

    def load(self):
        return []


class FakeMail:
    me = "me@x.com"

    def __init__(self, draft_alive=True, msgs=None, bounced=False):
        self.alive, self.msgs, self._b, self.created = draft_alive, msgs or [], bounced, []

    def draft_exists(self, _):
        return self.alive

    def thread(self, _):
        return self.msgs

    def has_reply(self, msgs):
        return any("SENT" not in m["labels"] and self.me not in m["from"] for m in msgs)

    sent_count = staticmethod(lambda msgs: sum(1 for m in msgs if "SENT" in m["labels"]))

    def bounced(self, _):
        return self._b

    def create_draft(self, to, subject, body, thread_id=None, in_reply_to=None):
        self.created.append((to, subject, body, thread_id, in_reply_to))
        return "d2", thread_id or "t1"


def row(**kw):
    base = {"_row": 2, "Company": "Finch", "Status": "Sent", "Draft ID": "", "Thread ID": "t1",
            "Verified email": "a@b.co", "Contact name": "Jo", "Sent date": "2026-11-20",
            "Follow-ups sent": "0", "Notes": "", "Sample question": "How do I connect Paycom?",
            "Website": "https://a.com", "Docs URL": "https://a.com/docs"}
    base.update(kw)
    return base


SENT = {"from": "me@x.com", "labels": ["SENT"], "message_id": "<1>", "subject": "Hello"}
THEM = {"from": "jo@b.co", "labels": ["INBOX"], "message_id": "<2>", "subject": "Re: Hello"}


def test_reply_stops_followups(monkeypatch):
    monkeypatch.setattr(run_daily, "TODAY", date(2026, 12, 30))
    q, m = FakeQueue(), FakeMail(msgs=[SENT, THEM])
    run_daily.sync_existing(q, m, [row()])
    assert q.updates[0][1]["Status"] == "Replied" and not m.created


def test_followup_drafted_when_due_and_threaded(monkeypatch):
    monkeypatch.setattr(run_daily, "TODAY", date(2026, 11, 23))
    q, m = FakeQueue(), FakeMail(msgs=[SENT])
    run_daily.sync_existing(q, m, [row()])
    assert len(m.created) == 1
    to, subject, body, tid, irt = m.created[0]
    assert subject == "Re: Hello" and tid == "t1" and irt == "<1>" and "DEMO" in body
    assert q.updates[-1][1]["Status"] == "Draft ready" and q.updates[-1][1]["Notes"].startswith("followup:1")


def test_not_due_yet_does_nothing(monkeypatch):
    monkeypatch.setattr(run_daily, "TODAY", date(2026, 11, 21))
    q, m = FakeQueue(), FakeMail(msgs=[SENT])
    run_daily.sync_existing(q, m, [row()])
    assert not m.created and not q.updates


def test_bounce_stops(monkeypatch):
    monkeypatch.setattr(run_daily, "TODAY", date(2026, 11, 21))
    q, m = FakeQueue(), FakeMail(msgs=[SENT], bounced=True)
    run_daily.sync_existing(q, m, [row()])
    assert q.updates[0][1]["Status"] == "Bounced"


def test_first_draft_sent_is_detected(monkeypatch):
    monkeypatch.setattr(run_daily, "TODAY", date(2026, 11, 20))
    q, m = FakeQueue(), FakeMail(draft_alive=False, msgs=[SENT])
    run_daily.sync_existing(q, m, [row(Status="Draft ready", **{"Draft ID": "d1", "Sent date": ""})])
    f = q.updates[0][1]
    assert f["Status"] == "Sent" and f["Sent date"] == "2026-11-20" and f["Next follow-up"] == "2026-11-23"


def test_deleted_first_draft_returns_to_ready(monkeypatch):
    q, m = FakeQueue(), FakeMail(draft_alive=False, msgs=[])
    run_daily.sync_existing(q, m, [row(Status="Draft ready", **{"Draft ID": "d1"})])
    assert q.updates[0][1]["Status"] == "Ready"


def test_deleted_followup_draft_does_not_count_as_sent(monkeypatch):
    # only the original email was ever sent; the follow-up draft was deleted
    q, m = FakeQueue(), FakeMail(draft_alive=False, msgs=[SENT])
    run_daily.sync_existing(q, m, [row(Status="Draft ready", Notes="followup:1 2026-11-23", **{"Draft ID": "d2"})])
    f = q.updates[0][1]
    assert f["Status"] == "Sent" and "Follow-ups sent" not in f


def test_followup_sent_is_detected_and_final_one_finishes(monkeypatch):
    monkeypatch.setattr(run_daily, "TODAY", date(2026, 12, 4))
    q, m = FakeQueue(), FakeMail(draft_alive=False, msgs=[SENT, SENT, SENT, SENT])
    run_daily.sync_existing(q, m, [row(Status="Draft ready", Notes="followup:3 2026-12-04",
                                       **{"Draft ID": "d4", "Follow-ups sent": "2"})])
    assert q.updates[0][1]["Status"] == "Done"


def test_new_drafts_respect_start_date_and_limit(monkeypatch):
    import research
    import drafts
    monkeypatch.setattr(run_daily, "TODAY", date(2026, 11, 20))
    monkeypatch.setattr(run_daily, "OUTREACH_START", date(2026, 11, 24))
    q, m = FakeQueue(), FakeMail()
    run_daily.draft_new(q, m, [row(Status="Ready")], 0)
    assert not m.created  # still warming up

    monkeypatch.setattr(run_daily, "OUTREACH_START", None)
    monkeypatch.setattr(run_daily, "DAILY_LIMIT", 1)
    monkeypatch.setattr(research, "gather", lambda urls: ({"https://a.com": "text"}, []))
    res = {"observation": "obs", "source_url": "https://a.com", "opener": "Short opener.",
           "sample_question": "q?", "has_ai_assistant": False, "confidence": "high"}
    monkeypatch.setattr(drafts.llm, "generate_json", lambda p: res)
    rows = [row(Status="Ready", _row=2, Company="A"), row(Status="Ready", _row=3, Company="B")]
    run_daily.draft_new(q, m, rows, 0)
    assert len(m.created) == 1  # daily limit
    assert m.created[0][1].startswith("A free assistant built on A")


def test_ai_marker_skips_lead(monkeypatch):
    import research
    monkeypatch.setattr(run_daily, "OUTREACH_START", None)
    monkeypatch.setattr(research, "gather", lambda urls: ({"https://a.com": "text"}, ["Chatbase"]))
    q, m = FakeQueue(), FakeMail()
    run_daily.draft_new(q, m, [row(Status="Ready")], 0)
    assert q.updates[0][1]["Status"] == "Skip" and not m.created


def test_invalid_email_is_not_drafted(monkeypatch):
    monkeypatch.setattr(run_daily, "OUTREACH_START", None)
    q, m = FakeQueue(), FakeMail()
    run_daily.draft_new(q, m, [row(Status="Ready", **{"Verified email": "nope"})], 0)
    assert not m.created
