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
    body = json.dumps(
        {
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {
                "maxOutputTokens": 16384,      # long-form article needs headroom
                "temperature": 0.8,
            },
        }
    ).encode()
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
            cand = data["candidates"][0]
            if cand.get("finishReason") == "MAX_TOKENS":
                print("WARNING: output hit maxOutputTokens and may be truncated.")
            return cand["content"]["parts"][0]["text"]
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


def parse_article(raw: str) -> dict | None:
    """Parse the delimiter-based response format into title/description/tags/body.

    Expected format:
        TITLE: ...
        DESCRIPTION: ...
        TAGS: tag1, tag2, tag3
        ===BODY===
        <markdown>

    Returns None if the response doesn't follow the format (caller may retry).
    """
    raw = raw.strip()
    # tolerate the model wrapping everything in a markdown fence anyway
    raw = re.sub(r"^```[a-z]*\n|\n```$", "", raw).strip()

    if "===BODY===" not in raw:
        return None
    header, body = raw.split("===BODY===", 1)

    fields = {}
    for line in header.splitlines():
        m = re.match(r"^(TITLE|DESCRIPTION|TAGS)\s*:\s*(.+)$", line.strip())
        if m:
            fields[m.group(1)] = m.group(2).strip()

    if {"TITLE", "DESCRIPTION", "TAGS"} - fields.keys():
        return None

    tags = [t.strip().lower().lstrip("#") for t in fields["TAGS"].split(",") if t.strip()]
    return {
        "title": fields["TITLE"].strip('"'),
        "description": fields["DESCRIPTION"].strip('"'),
        "tags": tags[:4],
        "body_markdown": body.strip(),
    }


def salvage_article(raw: str) -> dict:
    """Last-resort recovery when the model ignores the output format entirely:
    treat the first non-empty line as the title and the rest as the body."""
    raw = raw.strip()
    raw = re.sub(r"^```[a-z]*\n|\n```$", "", raw).strip()
    lines = raw.splitlines()
    title = lines[0].lstrip("# ").strip().strip('"') if lines else "Untitled"
    body = "\n".join(lines[1:]).strip()
    # pull description from the first real paragraph, trimmed to 150 chars
    para = next((l.strip() for l in lines[1:] if l.strip() and not l.startswith(("#", ">"))), "")
    description = (para[:147] + "...") if len(para) > 150 else para
    print("WARNING: model ignored the output format; salvaged title/body heuristically.")
    return {
        "title": title,
        "description": description,
        "tags": ["dataengineering"],
        "body_markdown": body,
    }


def main() -> None:
    print(f"Using model: {MODEL}")

    topic = pick_topic(get_topics())
    topic_line = (
        f'The topic is: "{topic}".'
        if topic
        else f"Pick a fresh, specific, currently-relevant topic in {NICHE}."
    )

    prompt = f"""You are a senior data/platform engineer with 6+ years of production experience in
financial services and healthcare, writing under your own byline on Dev.to. You write like a
practitioner sharing hard-won lessons, not like a textbook or a content marketer.

{topic_line}

VOICE AND STYLE (non-negotiable):
- First person, opinionated, direct. Take positions ("Don't develop against `latest`.").
- Open with a relatable war story or pain the reader has lived, NOT with "In today's
  fast-paced world" or "change is the only constant" style filler.
- Concrete over abstract everywhere: real version numbers, real config keys, real
  failure modes, real trade-offs. Name specific tools.
- Short paragraphs. Occasional dry humor is fine. Zero corporate filler phrases.

REQUIRED ARTICLE STRUCTURE (in the body, in this order):
1. A blockquote starting with "> **Why I chose this topic:**" — 2-4 sentences on the gap
   in existing content this article fills and the production experience behind it.
2. A hook opening (2-4 paragraphs): a specific failure scenario the reader recognizes,
   then a one-paragraph promise of what the article delivers.
3. A "## The real problem: ..." section that reframes the topic — ideally as a numbered
   breakdown of the underlying layers/causes most people miss.
4. 3-5 "## Step N: ..." sections walking through the solution. Each step MUST include at
   least one realistic, runnable code block (dockerfile/yaml/python/sql/bash as fits the
   topic) with pinned versions, followed by 2-3 bullets explaining the non-obvious
   details ("Three details that matter more than they look:" style).
5. A "## Lessons learned from production" section: 4-6 bullets, each a specific,
   experience-backed gotcha with the reason it matters.
6. A "## Production considerations" section: a short paragraph covering secrets,
   security/compliance, and operational hygiene relevant to the topic.
7. A "## Conclusion" with a "**Try it:**" call to action, an invitation to comment, and
   one sentence teasing a follow-up article in the series.
8. A horizontal rule, then "**SEO keywords:** ..." (8-12 comma-separated long-tail
   keyword phrases) and "**Tags:** #tag1 #tag2 ..." on the next line.

Do NOT include any images, image placeholders, diagram descriptions, cover-image notes,
or references to figures. Text and code blocks only.

LENGTH: 1500-2200 words. Do NOT include the title as an H1 in the body.

CRITICAL OUTPUT FORMAT — your response MUST start with the literal characters "TITLE:".
Do not write anything before it. No preamble, no JSON, no markdown fence around the
whole output. Use this exact template, including the ===BODY=== line on its own line:

TITLE: <compelling, specific title; a colon construction with a hook phrase is good, e.g. "It Works on My Cluster: ...">
DESCRIPTION: <one-sentence summary under 150 characters, concrete about the payoff>
TAGS: <3 to 4 lowercase single-word tags, comma-separated>
===BODY===
<the full article markdown following the structure above>

If your response does not begin with "TITLE:" and contain the line "===BODY===", it is
wrong and will be rejected.
"""

    raw = call_gemini(prompt)
    art = parse_article(raw)
    if art is None:
        print("Response missing the required format; retrying once with a reminder...")
        reminder = (
            prompt
            + "\n\nREMINDER: Your previous attempt failed because it did not start with"
            ' "TITLE:" and did not contain the "===BODY===" line. Follow the OUTPUT'
            " FORMAT exactly this time."
        )
        raw = call_gemini(reminder)
        art = parse_article(raw) or salvage_article(raw)

    tags = art["tags"]
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
