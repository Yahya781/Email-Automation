"""Tiny LLM client. Pick the provider with LLM_PROVIDER=gemini|anthropic. Models come from env on purpose:
free-tier model names change, so set the current one yourself."""
import json
import os
import re

import requests


def _post(url, headers, payload):
    r = requests.post(url, headers=headers, json=payload, timeout=60)
    r.raise_for_status()
    return r.json()


def generate_json(prompt: str) -> dict:
    provider = os.environ.get("LLM_PROVIDER", "gemini").lower()
    if provider == "gemini":
        model = os.environ["GEMINI_MODEL"]
        data = _post(
            f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
            {"x-goog-api-key": os.environ["GEMINI_API_KEY"]},
            {"contents": [{"parts": [{"text": prompt}]}],
             "generationConfig": {"responseMimeType": "application/json", "temperature": 0.3}},
        )
        text = data["candidates"][0]["content"]["parts"][0]["text"]
    elif provider == "anthropic":
        data = _post(
            "https://api.anthropic.com/v1/messages",
            {"x-api-key": os.environ["ANTHROPIC_API_KEY"], "anthropic-version": "2023-06-01"},
            {"model": os.environ["ANTHROPIC_MODEL"], "max_tokens": 900, "temperature": 0.3,
             "messages": [{"role": "user", "content": prompt}]},
        )
        text = data["content"][0]["text"]
    else:
        raise ValueError(f"unknown LLM_PROVIDER {provider}")
    return parse_json(text)


def parse_json(text: str) -> dict:
    text = text.strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        m = re.search(r"\{.*\}", text, re.S)
        if not m:
            raise
        return json.loads(m.group(0))
