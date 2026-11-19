"""Pure logic: no network, easy to test. The bot only ever creates DRAFTS; nothing here sends mail."""
import re
from datetime import date, timedelta

FOLLOWUP_DAYS = (3, 7, 14)  # days after the first send; three follow-ups, then stop
PITCH = "I build AI assistants that answer questions from a company's own docs and cite the source page."
CTA = 'I can build a free prototype on your public docs. Reply "DEMO" and I\'ll send it over.'
OPTOUT = "If this isn't relevant, tell me and I won't email again."
MAX_OPENER_WORDS = 45
EMAIL_RE = re.compile(r"^[^@\s,;]+@[^@\s,;]+\.[^@\s,;]+$")

# Strings in a page's HTML that reveal a chat or AI help widget which text-only reading cannot see.
CHAT_MARKERS = {
    "intercom": "Intercom", "driftt.com": "Drift", "crisp.chat": "Crisp", "tidio": "Tidio",
    "zdassets.com": "Zendesk", "usemessages.com": "HubSpot chat", "chatbase": "Chatbase",
    "freshchat": "Freshchat", "livechatinc": "LiveChat", "tawk.to": "Tawk.to",
    "helpscout": "Help Scout", "usepylon.com": "Pylon", "kapa.ai": "kapa.ai",
    "inkeep": "Inkeep", "mendable": "Mendable", "ask ai": "Ask AI button",
}
AI_MARKER_NAMES = {"Chatbase", "kapa.ai", "Inkeep", "Mendable", "Ask AI button"}


def chat_markers(html: str) -> list[str]:
    low = html.lower()
    return sorted({name for key, name in CHAT_MARKERS.items() if key in low})


def valid_email(addr: str) -> bool:
    return bool(addr) and bool(EMAIL_RE.match(addr.strip()))


def first_name(contact: str) -> str:
    contact = (contact or "").strip()
    return contact.split()[0] if contact else "there"


def parse_date(s: str):
    try:
        return date.fromisoformat((s or "").strip())
    except ValueError:
        return None


def followup_due(first_sent: date, followups_sent: int, today: date) -> bool:
    if followups_sent >= len(FOLLOWUP_DAYS):
        return False
    return today >= first_sent + timedelta(days=FOLLOWUP_DAYS[followups_sent])


def next_followup_date(first_sent: date, followups_sent: int):
    if followups_sent >= len(FOLLOWUP_DAYS):
        return None
    return first_sent + timedelta(days=FOLLOWUP_DAYS[followups_sent])


def validate_generation(d: dict, fetched_urls: list[str]):
    """Check the model's JSON before anything reaches a draft. Returns (ok, reasons)."""
    reasons = []
    for key in ("observation", "source_url", "opener", "sample_question", "has_ai_assistant"):
        if key not in d:
            reasons.append(f"missing {key}")
    if reasons:
        return False, reasons
    opener = str(d["opener"]).strip()
    if not opener:
        reasons.append("empty opener")
    if len(opener.split()) > MAX_OPENER_WORDS:
        reasons.append(f"opener over {MAX_OPENER_WORDS} words")
    if "http" in opener.lower():
        reasons.append("opener contains a link")
    if d["source_url"] not in fetched_urls:
        reasons.append("source_url was not one of the pages read")
    if not str(d["observation"]).strip():
        reasons.append("empty observation")
    return not reasons, reasons


def initial_subject(company: str) -> str:
    return f"A free assistant built on {company}'s docs"


def build_initial_email(company, contact, opener, signature):
    body = f"Hi {first_name(contact)},\n\n{opener.strip()}\n\n{PITCH}\n\n{CTA}\n\n{OPTOUT}\n\n{signature.strip()}"
    return initial_subject(company), body


def build_followup_email(n: int, company, contact, sample_question, signature):
    """n is 1, 2 or 3. Short, different each time, and the last one closes the loop."""
    hi = f"Hi {first_name(contact)},"
    if n == 1:
        core = (f"Floating this back up in case it got buried. I can build the prototype on {company}'s docs "
                'if useful. Reply "DEMO" and I\'ll share it.')
    elif n == 2:
        q = (sample_question or "").strip()
        core = (f"One example of what it handles: a visitor asks \"{q}\" and gets the answer with the page it came from."
                if q else "It answers a visitor's question straight from your docs and shows the page it used.")
        core += ' Reply "DEMO" if you want to see it on your own docs.'
    elif n == 3:
        core = ("Last note from me: should I close this out? If now isn't the time, no worries, "
                "and I won't follow up again.")
    else:
        raise ValueError("n must be 1, 2 or 3")
    return f"{hi}\n\n{core}\n\n{OPTOUT}\n\n{signature.strip()}"
