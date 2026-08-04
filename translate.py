#!/usr/bin/env python3
"""English translation of reel metadata for YouTube localizations.

Uses OpenRouter (OPENROUTER_API_KEY secret). The channel's default metadata
stays Turkish; the translation is attached as an "en" localization so viewers
with an English UI see English title + description.

Translation is strictly best-effort: any failure returns None and the caller
uploads without a localization. A missing translation must never block or
delay an upload.
"""

import json
import os

import requests

OPENROUTER_MODEL = os.environ.get("OPENROUTER_MODEL", "openai/gpt-5.5")

SYSTEM_PROMPT = """\
You translate YouTube Shorts metadata for "Chia the Frenchton", a funny French
Bulldog channel, from Turkish into natural, casual English that lands with US
viewers (not word-for-word literal).

You receive JSON: {"title": ..., "description": ...}.
Return ONLY JSON: {"title": ..., "description": ...}.

Rules:
- Keep all emoji.
- Title: at most 90 characters; keep a "#Shorts" hashtag at the end.
- Description: translate the Turkish prose. Keyword lists and hashtags should
  come out English-only: translate Turkish keywords/hashtags into their natural
  English equivalents and drop duplicates.
"""


def translate_metadata(title, description):
    """Return {"title": ..., "description": ...} in English, or None on any failure."""
    key = os.environ.get("OPENROUTER_API_KEY")
    if not key:
        print("OPENROUTER_API_KEY not set; uploading without English localization.")
        return None
    try:
        resp = requests.post(
            "https://openrouter.ai/api/v1/chat/completions",
            headers={"Authorization": f"Bearer {key}"},
            json={
                "model": OPENROUTER_MODEL,
                # Without max_tokens OpenRouter reserves the model's full 65k
                # output window against the key's credit limit and 402s.
                "max_tokens": 2000,
                "response_format": {"type": "json_object"},
                "messages": [
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": json.dumps(
                        {"title": title, "description": description},
                        ensure_ascii=False)},
                ],
            },
            timeout=120,
        )
        if resp.status_code != 200:
            raise RuntimeError(f"OpenRouter {resp.status_code}: {resp.text[:300]}")
        content = resp.json()["choices"][0]["message"]["content"]
        out = json.loads(content)
        en_title = (out.get("title") or "").strip()
        en_desc = (out.get("description") or "").strip()
        if not en_title or not en_desc:
            raise RuntimeError(f"translation missing fields: {content[:300]}")
        if "#shorts" not in en_title.lower():
            en_title = en_title[:92].rstrip() + " #Shorts"
        return {"title": en_title[:100], "description": en_desc}
    except Exception as e:
        print(f"Translation failed ({e}); uploading without English localization.")
        return None
