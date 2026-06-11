#!/usr/bin/env python3
"""
Generate one article per run using the Google Gemini API (free tier) and save
it into posts/ as a Markdown file with Dev.to front matter (published: false,
so you review and hit Publish on Dev.to yourself).

Topic source: the first non-empty line of topics.txt is used and then removed.
If topics.txt is empty/missing, the model picks a fresh topic in NICHE below.

Requires the GEMINI_API_KEY environment variable (a GitHub Actions secret).
Get a free key at https://aistudio.google.com/app/apikey

The model is read from the GEMINI_MODEL environment variable (a GitHub Actions
repository variable). If unset, it falls back to a model that currently has
free-tier quota. NOTE: Google changes model names and free-tier limits over
time. If you get a 404/model error, check the current free model name in
AI Studio and update the GEMINI_MODEL repo variable (or the fallback below).
"""

import os
import re
import sys
import json
import time
import datetime
import urllib.request
import urllib.error

MODEL = os.environ.get("GEMINI_MODEL", "gemini-2.5-flash-lite").strip() or "gemini-2.5-flash-lite"
NICHE = "data engineering, AI/ML, and cloud-native systems"
TOPICS_FILE = "topics.txt"
ARTICLES_DIR = "articles"

ENDPOINT = (
    f"https://generativelanguage.googleapis.com/v1beta/models/{MODEL}:generateContent"
)


def api_key() -> str:
    k = os.environ.get("GEMINI_API_KEY")
    if not k:
        sys.exit("ERROR: GEMINI_API_KEY is not set.")
    return k


def get_topics() -> list[str]:
    """Topics from the ARTICLE_TOPICS secret (private), or topics.txt as fallback."""
    env = os.environ.get("ARTICLE_TOPICS")
    if env and env.strip():
        return [l.strip() for l in env.splitlines() if l.strip()]
    if os.path.exists(TOPICS_FILE):
        return [l.strip() for l in open(TOPICS_FILE) if l.strip()]
    return []


def pick_topic(topics: list[str]) -> str | None:
    """Rotate through the list by date so it cycles without storing any state."""
    if not topics:
        return None
    idx = datetime.date.today().toordinal() % len(topics)
    return topics[idx]


def call_gemini(prompt: str, retries: int = 3) -> str:
    """Call the Gemini API, retrying transient 429 rate limits with backoff."""
    body = json.dumps({"contents": [{"parts": [{"text": prompt}]}]}).encode()
    for attempt in range(retries):
        req = urllib.request.Request(
            f"{ENDPOINT}?key={api_key()}",
            data=body,
            method="POST",
            headers={"Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(req) as r:
                data = json.loads(r.read().decode())
            return data["candidates"][0]["content"]["parts"][0]["text"]
        except urllib.error.HTTPError as e:
            err = e.read().decode()
            if e.code == 429 and attempt < retries - 1:
                wait = 30 * (attempt + 1)  # 30s, 60s
                print(f"429 rate limit hit, retrying in {wait}s "
                      f"(attempt {attempt + 1}/{retries - 1})...")
                time.sleep(wait)
                continue
            sys.exit(f"Gemini API error {e.code}: {err}")
    sys.exit("Gemini API: exhausted all retries.")


def main() -> None:
    print(f"Using model: {MODEL}")

    topic = pick_topic(get_topics())
    topic_line = (
        f'The topic is: "{topic}".'
        if topic
        else f"Pick a fresh, specific, currently-relevant topic in {NICHE}."
    )

    prompt = f"""You are a professional technical writer. Write one original, engaging article for Dev.to.

{topic_line}

Return ONLY valid JSON (no markdown fences, no preamble) with exactly these keys:
- "title": a compelling title (string)
- "description": a one-sentence summary under 150 characters (string)
- "tags": 2 to 4 lowercase single-word tags, as a JSON array of strings
- "body_markdown": the full article in Markdown, 700-1100 words, using ## headings,
  short paragraphs, and a brief conclusion. Do NOT include the title as an H1 in the body.
"""

    raw = call_gemini(prompt).strip()
    raw = re.sub(r"^```(json)?|```$", "", raw, flags=re.MULTILINE).strip()
    art = json.loads(raw, strict=False)

    tags = [str(t).lower() for t in art.get("tags", [])][:4]
    # one self-contained folder per article: articles/article<MMDDYYYY>_<HHMMSS>/
    ts = datetime.datetime.now().strftime("%m%d%Y_%H%M%S")
    folder = os.path.join(ARTICLES_DIR, f"article{ts}")
    os.makedirs(folder, exist_ok=True)
    path = os.path.join(folder, "article.md")

    q = chr(34)  # double quote
    sq = chr(39)  # single quote
    front_matter = (
        "---\n"
        f"title: {q}{art['title'].replace(q, sq)}{q}\n"
        "published: false\n"
        f"description: {q}{art.get('description', '').replace(q, sq)}{q}\n"
        f"tags: {', '.join(tags)}\n"
        "canonical_url:\n"
        "---\n\n"
    )

    with open(path, "w") as f:
        f.write(front_matter + art["body_markdown"].strip() + "\n")
    print(f"Wrote {path}")
    if topic:
        print(f"Topic used: {topic}")


if __name__ == "__main__":
    main()
