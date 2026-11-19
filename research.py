"""Fetch a company's public pages and prepare them for the model. Respects robots.txt."""
import re
import urllib.robotparser
from urllib.parse import urlparse

import requests
from bs4 import BeautifulSoup

from logic import chat_markers

UA = "Mozilla/5.0 (compatible; yahya-outreach-research; +https://yahyaahmed.netlify.app)"
MAX_CHARS = 7000


def allowed(url: str) -> bool:
    p = urlparse(url)
    rp = urllib.robotparser.RobotFileParser()
    try:
        r = requests.get(f"{p.scheme}://{p.netloc}/robots.txt", headers={"User-Agent": UA}, timeout=10)
        if r.status_code >= 400:
            return True
        rp.parse(r.text.splitlines())
        return rp.can_fetch(UA, url)
    except requests.RequestException:
        return True


def fetch_page(url: str):
    """Return (text, markers) or None if the page can't or shouldn't be read."""
    if not url.startswith("http") or not allowed(url):
        return None
    try:
        r = requests.get(url, headers={"User-Agent": UA}, timeout=20)
        r.raise_for_status()
    except requests.RequestException:
        return None
    html = r.text
    markers = chat_markers(html)
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "noscript", "svg"]):
        tag.decompose()
    text = re.sub(r"\s+", " ", soup.get_text(" ")).strip()
    return text[:MAX_CHARS], markers


def gather(urls: list[str]):
    """Fetch each URL once. Returns (pages dict url->text, sorted marker list)."""
    pages, markers = {}, set()
    for u in dict.fromkeys(u for u in urls if u):
        got = fetch_page(u)
        if got:
            pages[u] = got[0]
            markers.update(got[1])
    return pages, sorted(markers)
