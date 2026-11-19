# Outreach bot (leads and emails only, never sends)

**Current flow.** You add companies to the `Outreach Queue` sheet by hand (Company, Website, optional founder name, Status `Needs email` or `Needs review`). Every morning the GitHub workflow `daily-email-finder` runs `find_emails.py` only, with no LLM key:
- Rows with a Website, no Verified email and no Suggested email get one Hunter lookup: Email Finder if the founder column has a name, otherwise Domain Search for a founder/CEO.
- It writes only `Suggested email` and `Email confidence` (`none found` plus the score when nothing clears `EMAIL_MIN_SCORE`, default 70, so the row is not looked up again). It never writes Verified email or Status.
- At most `EMAIL_LOOKUP_LIMIT` lookups per run (default 10, both types combined). On HTTP 429, a rate limit or used-up credits it stops cleanly. `DRY_RUN=1` prints only.
- The run ends with a summary: rows checked, suggested, none found, failed, and credits left (read from Hunter's free account call, `unknown` if it fails).

You then check each suggestion, copy it to **Verified email** and set Status to `Ready`. Drafts are written elsewhere. Nothing in this project sends email.

**Disabled: `discover_leads.py`.** It used Hunter Discover to add companies automatically, but the results were open-source projects and non-profits (PHP, Linux, OpenStack), so the workflow no longer calls it. The file and its tests stay in case you want to re-enable it. `run_daily.py`, `llm.py` and `drafts.py` are also kept but not called (they need an LLM key if you run them yourself).

**Old draft flow (`run_daily.py`, not run by the workflow).** Every morning it:
1. Checks Gmail for what happened: first email sent, reply, bounce.
2. Writes follow-up **drafts** (day 3, 7, 14, then stops) for people who haven't replied.
3. Researches up to 5 new leads (rows with Status = `Ready`) and writes a **draft** for each.

It never sends. You open Gmail, check each draft against the company's site, and press Send.
The Google Sheet tab `Outreach Queue` is the control panel.

## Your daily routine (about 15 minutes)
1. In the sheet, fill **Verified email** (and Contact name) for a lead and set Status to `Ready`.
2. Next morning, open Gmail drafts. Check the Observation (column G) on the company's site. Edit or delete the draft.
3. Press Send. The bot notices on its next run and starts the follow-up clock.
4. If someone replies, the bot sets Status to `Replied` and stops. You take over.

Status meanings: Needs review and Needs email (waiting for an email suggestion), Ready (bot will draft), Draft ready (waiting for you), Sent, Replied, Bounced, Done (all follow-ups sent), Skip (bot rejected it, reason in Notes), Stopped (set it yourself to pause a lead).

## One-time setup
1. **Share the sheet** with the outreach Gmail (`yahyaahmedd781@gmail.com`) as Editor.
2. **Google Cloud** (free): create a project, enable the **Gmail API** and **Google Sheets API**, create an OAuth client of type *Desktop app*, download it as `client_secret.json` into this folder.
   - On the OAuth consent screen, set the app to **In production** (it stays unverified, which is fine for your own account). If you leave it in *Testing*, Google expires the token after 7 days and the bot stops.
3. Run `pip install -r requirements.txt` then `python auth_setup.py`. Log in as the outreach Gmail. Copy the three values it prints.
4. **Hunter key** (free hunter.io account).
5. **GitHub**: create a **private** repo and push this folder (the `.gitignore` keeps secrets out). Then in Settings > Secrets and variables > Actions add:
   - Secrets: `SHEET_ID`, `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET`, `GOOGLE_REFRESH_TOKEN`, `HUNTER_API_KEY`.
6. **First run:** Actions tab > daily-email-finder > Run workflow with `dry_run = 1`. Read the log and the summary line, then run with `dry_run = 0`.

The schedule is 09:30 Pakistan time. Keep the repo private and push something now and then: GitHub pauses schedules on repos with no activity for 60 days.

## Guardrails built in
- Drafts only. No send call exists in the code. Gmail scopes: create drafts and read.
- No new drafts before `OUTREACH_START_DATE`, at most `DAILY_LIMIT` per day, and never more than twice that waiting in your drafts.
- Skips a lead if its HTML contains a known chat or AI help widget (Intercom, Pylon, Chatbase, kapa.ai and others) or the model says it already has an AI assistant.
- The model may only cite one of the pages it was given. Openers over 45 words, with links, or with an unknown source are rejected and the lead is marked Skip with the reason.
- Every email ends with an opt-out line. Three follow-ups maximum.
- Reads only public pages and respects robots.txt.
- Sheet writes are plain text, so model output can't turn into a spreadsheet formula.

## Limits to know about
- The research reads page text. A widget that loads after the page may not show, so always check the site yourself before pressing Send.
- Email finding is semi-automated: `find_emails.py` asks Hunter for an address for each 'Needs email' or 'Needs review' row (founder name + website) and writes it to 'Suggested email' with a confidence score, max 10 lookups per run. It never fills 'Verified email' and never changes Status. If a row has a website but no usable founder name, it falls back to Hunter Domain Search, picks one founder/CEO (else owner/head of product) from the results, ignores addresses like info@ and anything under the score threshold,. Both lookup types share the same per-run limit (`EMAIL_LOOKUP_LIMIT`, default 10), because Domain Search also uses Hunter credits. You check the suggestion, copy it to 'Verified email' and set Status to Ready.
- Lead discovery is manual: you add companies to the sheet yourself.
- Tested here: logic and the Gmail/sheet state machine (15 tests with fakes) and the page reader on a local page. **Not tested live:** the Google APIs and the LLM call, because those need your credentials. Use the dry run first.

Run tests: `python -m pytest -q`
