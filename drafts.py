"""Turn fetched page text into an observation and opener, with guardrails."""
import llm
from logic import AI_MARKER_NAMES, validate_generation

PROMPT = """You help a freelance developer write ONE short cold-email opener to a software company.
He builds AI assistants that answer questions from a company's own docs and cite the source page.

Company: {company}
Chat or help widgets detected in the pages' HTML: {markers}

Pages read (text only; JavaScript widgets are invisible in text):
{pages}

Rules:
- Use ONLY facts visible in the page text above. Never invent numbers, features or quotes.
- Prefer a concrete, checkable observation: a count, an inconsistency between pages, or a place where
  an answer is spread across several pages. Avoid claiming something is "missing" unless the text clearly
  shows its absence; if you must, say "I couldn't find".
- The opener is 1-2 sentences, at most 40 words, no flattery, no links, no exclamation marks.
- source_url must be exactly one of these URLs: {urls}
- has_ai_assistant is true if the text or markers show the company already has an AI or chat assistant for help.
- sample_question is a realistic question a visitor would ask that these docs could answer.

Return JSON only with keys: observation (string, 1-3 sentences for the developer to verify),
source_url, opener, sample_question, has_ai_assistant (boolean), confidence ("low"|"medium"|"high")."""


def generate(company: str, pages: dict, markers: list[str]):
    """Returns (result_dict | None, note). result None means do not draft."""
    if not pages:
        return None, "could not read any page (blocked, down or disallowed by robots.txt)"
    if AI_MARKER_NAMES & set(markers):
        return None, f"already has an AI help tool ({', '.join(sorted(AI_MARKER_NAMES & set(markers)))})"
    body = "\n\n".join(f"URL: {u}\nTEXT: {t}" for u, t in pages.items())
    data = llm.generate_json(PROMPT.format(company=company, markers=", ".join(markers) or "none",
                                           pages=body, urls=", ".join(pages)))
    ok, reasons = validate_generation(data, list(pages))
    if not ok:
        return None, "rejected: " + "; ".join(reasons)
    if data.get("has_ai_assistant") is True:
        return None, "the page text suggests it already has an AI assistant"
    return data, "verify on the site before sending (page text only; confidence: %s)" % data.get("confidence", "?")
