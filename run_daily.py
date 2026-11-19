"""Daily run. Order matters: sync what happened in Gmail, queue due follow-ups, then draft new leads.
Creates DRAFTS only. You open Gmail, check each one, and press Send.
Set DRY_RUN=1 to print what would happen without touching the sheet or Gmail."""
import os
import re
import sys
from datetime import date

import drafts
import logic
import research
from google_io import Mail, Queue, credentials

TODAY = date.today()
DRY = os.environ.get("DRY_RUN") == "1"
DAILY_LIMIT = int(os.environ.get("DAILY_LIMIT", "5"))
MAX_PENDING_DRAFTS = DAILY_LIMIT * 2
SIGNATURE = os.environ.get("SIGNATURE", "Yahya Ahmed\nAI Developer | Docs & Support Assistants\nyahyaahmed.netlify.app")
# First day new outreach drafts are allowed. Keep it after the warm-up period (default: none set = allowed).
OUTREACH_START = logic.parse_date(os.environ.get("OUTREACH_START_DATE", ""))
log = lambda *a: print(*a, flush=True)


def save(q, row, **fields):
    if DRY:
        log("   [dry run] would update", row, fields)
    else:
        q.update(row, **fields)


def note(row, text):
    return {"Notes": f"{TODAY}: {text}"}


def sync_existing(q, mail, rows):
    """Detect sends, replies, bounces and queue follow-up drafts."""
    pending = 0
    for r in rows:
        status, n = r["Status"], r["_row"]
        try:
            if status == "Draft ready":
                pending += 1
                if mail.draft_exists(r["Draft ID"]):
                    continue
                msgs = mail.thread(r["Thread ID"])
                m = re.match(r"followup:(\d)", r["Notes"])
                k = int(m.group(1)) if m else 0  # 0 = this draft is the first email
                if mail.sent_count(msgs) < k + 1:  # draft is gone but nothing new was sent
                    save(q, n, Status="Sent" if k else "Ready", **{"Draft ID": ""}, **note(n, "draft was deleted"))
                    continue
                first = logic.parse_date(r["Sent date"]) or TODAY
                if k:  # a follow-up was sent
                    nxt = logic.next_followup_date(first, k)
                    save(q, n, Status="Sent" if nxt else "Done", **{"Draft ID": "", "Follow-ups sent": k,
                         "Next follow-up": nxt.isoformat() if nxt else ""}, **note(n, f"follow-up {k} sent"))
                else:  # the first email was sent
                    nxt = logic.next_followup_date(TODAY, 0)
                    save(q, n, Status="Sent", **{"Draft ID": "", "Sent date": TODAY.isoformat(),
                         "Follow-ups sent": 0, "Next follow-up": nxt.isoformat()}, **note(n, "first email sent"))
                log(f"[{r['Company']}] marked sent")
            elif status == "Sent":
                msgs = mail.thread(r["Thread ID"])
                if mail.has_reply(msgs):
                    save(q, n, Status="Replied", **{"Next follow-up": ""}, **note(n, "reply received, you take over"))
                    log(f"[{r['Company']}] REPLIED, check your inbox")
                    continue
                if mail.bounced(r["Verified email"]):
                    save(q, n, Status="Bounced", **{"Next follow-up": ""}, **note(n, "bounce detected"))
                    log(f"[{r['Company']}] bounced")
                    continue
                first = logic.parse_date(r["Sent date"])
                done = int(r["Follow-ups sent"] or 0)
                if first and logic.followup_due(first, done, TODAY) and pending < MAX_PENDING_DRAFTS:
                    k = done + 1
                    body = logic.build_followup_email(k, r["Company"], r["Contact name"], r["Sample question"], SIGNATURE)
                    last = msgs[-1]
                    subj = msgs[0]["subject"]
                    subj = subj if subj.lower().startswith("re:") else "Re: " + subj
                    if DRY:
                        log(f"   [dry run] would draft follow-up {k} for {r['Company']}")
                    else:
                        did, tid = mail.create_draft(r["Verified email"], subj, body, r["Thread ID"], last["message_id"])
                        q.update(n, Status="Draft ready", **{"Draft ID": did}, **{"Notes": f"followup:{k} {TODAY}"})
                    pending += 1
                    log(f"[{r['Company']}] follow-up {k} draft ready")
        except Exception as e:  # one bad row must not stop the run
            log(f"[{r['Company']}] error: {e}")
            if not DRY:
                q.update(n, **{"Last error": f"{TODAY}: {str(e)[:150]}"})
    return pending


def draft_new(q, mail, rows, pending):
    if OUTREACH_START and TODAY < OUTREACH_START:
        log(f"New drafts start on {OUTREACH_START} (warm-up). Skipping.")
        return
    made = 0
    for r in rows:
        if made >= DAILY_LIMIT or pending >= MAX_PENDING_DRAFTS:
            break
        if r["Status"] != "Ready":
            continue
        n = r["_row"]
        if not logic.valid_email(r["Verified email"]):
            save(q, n, **note(n, "Verified email is missing or invalid"))
            continue
        try:
            pages, markers = research.gather([r["Website"], r["Docs URL"]])
            res, why = drafts.generate(r["Company"], pages, markers)
            if res is None:
                save(q, n, Status="Skip", **note(n, why))
                log(f"[{r['Company']}] skipped: {why}")
                continue
            subject, body = logic.build_initial_email(r["Company"], r["Contact name"], res["opener"], SIGNATURE)
            if DRY:
                log(f"[{r['Company']}] DRY RUN draft:\nSubject: {subject}\n{body}\n")
            else:
                did, tid = mail.create_draft(r["Verified email"].strip(), subject, body)
                q.update(n, Status="Draft ready", Observation=res["observation"], **{
                    "Source URL": res["source_url"], "Opener": res["opener"], "Verify note": why,
                    "Draft ID": did, "Thread ID": tid, "Follow-ups sent": 0, "Notes": "",
                    "Sample question": res["sample_question"], "Last error": ""})
            made += 1
            pending += 1
            log(f"[{r['Company']}] draft created")
        except Exception as e:
            log(f"[{r['Company']}] error: {e}")
            if not DRY:
                q.update(n, **{"Last error": f"{TODAY}: {str(e)[:150]}"})
    log(f"{made} new draft(s) today.")


def main():
    creds = credentials()
    q = Queue(creds, os.environ["SHEET_ID"], os.environ.get("SHEET_TAB", "Outreach Queue"))
    mail = Mail(creds)
    rows = q.load()
    log(f"{len(rows)} lead(s) in the queue. Sending account: {mail.me}. Dry run: {DRY}")
    pending = sync_existing(q, mail, rows)
    draft_new(q, mail, q.load() if not DRY else rows, pending)


if __name__ == "__main__":
    sys.exit(main())
