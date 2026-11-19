# Outreach bot (leads and emails only, never sends)

**Current flow.** Every morning the GitHub workflow runs two steps and needs no LLM key:
1. `discover_leads.py` asks Hunter's Discover API for new companies (defaults: Software Development, 1-50 people, US and GB, keywords documentation/API/developer) and appends up to `DISCOVER_LIMIT` (default 10) to the `Outreach Queue` tab with Status `Needs review`. Domains already in the Website column are skipped. It never fills Verified email.
2. `find_emails.py` suggests an address for `Needs review` and `Needs email` rows (see below).

Then you check the suggestion, copy it to **Verified email** and set Status to `Ready`. Drafts are written elsewhere. `run_daily.py`, `llm.py` and `drafts.py` are kept but the workflow no longer calls them (they are the old draft-writing step and need an LLM key if you run them yourself). Nothing in this project sends email.


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

Status meanings: Needs review (found by Discover, waiting for an email suggestion), Needs email, Ready (bot will draft), Draft ready (waiting for you), Sent, Replied, Bounced, Done (all follow-ups sent), Skip (bot rejected it, reason in Notes), Stopped (set it yourself to pause a lead).

## One-time setup
1. **Share the sheet** with the outreach Gmail (`yahyaahmedd781@gmail.com`) as Editor.
2. **Google Cloud** (free): create a project, enable the **Gmail API** and **Google Sheets API**, create an OAuth client of type *Desktop app*, download it as `client_secret.json` into this folder.
   - On the OAuth consent screen, set the app to **In production** (it stays unverified, which is fine for your own account). If you leave it in *Testing*, Google expires the token after 7 days and the bot stops.
3. Run `pip install -r requirements.txt` then `python auth_setup.py`. Log in as the outreach Gmail. Copy the three values it prints.
4. **Hunter key** (free hunter.io account). Discover needs a plan that includes it; if not, the run logs `stopping:` and `find_emails.py` still runs.
5. **GitHub**: create a **private** repo and push this folder (the `.gitignore` keeps secrets out). Then in Settings > Secrets and variables > Actions add:
   - Secrets: `SHEET_ID`, `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET`, `GOOGLE_REFRESH_TOKEN`, `HUNTER_API_KEY`.
   - Optional variables: `DISCOVER_LIMIT` (default 10), `DISCOVER_INDUSTRIES` (Hunter industry names, default `Software Development`), `DISCOVER_HEADCOUNT` (default `1-10,11-50`), `DISCOVER_COUNTRIES` (default `US,GB`), `DISCOVER_KEYWORDS` (default `documentation,API,developer`).
6. **First run:** Actions tab > daily-outreach-leads > Run workflow with `dry_run = 1`. Read the log, then run with `dry_run = 0`. If the Status column has a dropdown, add `Needs review` to it.

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
- Email finding is semi-automated: `find_emails.py` asks Hunter for an address for each 'Needs email' row (founder name + website) and writes it to 'Suggested email' with a confidence score, max 10 lookups per run. It never fills 'Verified email' and never changes Status. If a row has a website but no usable founder name, it falls back to Hunter Domain Search, picks one founder/CEO (else owner/head of product) from the results, ignores addresses like info@ and anything under the score threshold, and also notes "Hunter: Name, Position" in Notes if that cell is empty. Both lookup types share the same per-run limit (`EMAIL_LOOKUP_LIMIT`, default 10), because Domain Search also uses Hunter credits. You check the suggestion, copy it to 'Verified email' and set Status to Ready.
- Lead discovery is a weekly job you do with Claude, not a daily bot.
- Tested here: logic and the Gmail/sheet state machine (15 tests with fakes) and the page reader on a local page. **Not tested live:** the Google APIs and the LLM call, because those need your credentials. Use the dry run first.

Run tests: `python -m pytest -q`
