#!/usr/bin/env python3
"""
Generate one article per run using the Google Gemini API (free tier) and save
it into articles/ as a Markdown file with Dev.to front matter and an optional
dynamic cover image.

Cover images: the old source.unsplash.com keyword endpoint is DEAD (shut down
in 2023), so dynamic keyword images require the official Unsplash API. If the
UNSPLASH_ACCESS_KEY env var is set (free at https://unsplash.com/developers),
the script searches Unsplash for the model's IMAGE_PROMPT keyword and uses the
top result. If not set, cover_image is left blank and Dev.to renders fine
without it.
"""

import os
import re
import sys
import json
import time
import datetime
import urllib.parse
import urllib.request
import urllib.error

MODEL = os.environ.get("GEMINI_MODEL", "gemini-3.1-flash-lite").strip() or "gemini-3.1-flash-lite"
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
    env = os.environ.get("ARTICLE_TOPICS")
    if env and env.strip():
        return [l.strip() for l in env.splitlines() if l.strip()]
    if os.path.exists(TOPICS_FILE):
        return [l.strip() for l in open(TOPICS_FILE) if l.strip()]
    return []


def pick_topic(topics: list[str]) -> str | None:
    if not topics:
        return None
    idx = datetime.date.today().toordinal() % len(topics)
    return topics[idx]


def call_gemini(prompt: str, retries: int = 3) -> str:
    body = json.dumps(
        {
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {
                # Gemini 3 models think by default and thinking tokens count
                # against this budget, so leave generous headroom.
                "maxOutputTokens": 16384,
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
                wait = 60 * (attempt + 1)
                print(f"429 rate limit hit. Waiting {wait}s...")
                time.sleep(wait)
                continue
            sys.exit(f"Gemini API error {e.code}: {err}")
    sys.exit("Gemini API: exhausted all retries.")


def fetch_cover_image(keyword: str) -> str:
    """Search Unsplash for a cover photo. Returns '' if no API key, no result,
    or any error — a missing cover must never fail the run."""
    access_key = os.environ.get("UNSPLASH_ACCESS_KEY", "").strip()
    if not access_key or not keyword:
        return ""
    try:
        url = (
            "https://api.unsplash.com/search/photos?"
            + urllib.parse.urlencode(
                {"query": keyword.replace("-", " "), "per_page": 1,
                 "orientation": "landscape"}
            )
        )
        req = urllib.request.Request(
            url, headers={"Authorization": f"Client-ID {access_key}"}
        )
        with urllib.request.urlopen(req, timeout=15) as r:
            results = json.loads(r.read().decode()).get("results", [])
        if results:
            return results[0]["urls"]["regular"]
    except Exception as e:  # noqa: BLE001 — cover image is best-effort only
        print(f"NOTE: Unsplash lookup failed ({e}); continuing without cover.")
    return ""


def parse_article(raw: str) -> dict | None:
    """Parse the delimiter format. IMAGE_PROMPT is optional — its absence
    should never sink an otherwise good article. Returns None only if the
    core format (TITLE/DESCRIPTION/TAGS + ===BODY===) is missing."""
    raw = raw.strip()
    raw = re.sub(r"^```[a-z]*\n|\n```$", "", raw).strip()

    if "===BODY===" not in raw:
        return None
    header, body = raw.split("===BODY===", 1)

    fields = {}
    for line in header.splitlines():
        m = re.match(r"^(TITLE|DESCRIPTION|TAGS|IMAGE_PROMPT)\s*:\s*(.+)$", line.strip())
        if m:
            fields[m.group(1)] = m.group(2).strip()

    if {"TITLE", "DESCRIPTION", "TAGS"} - fields.keys():
        return None

    tags = [t.strip().lower().lstrip("#") for t in fields["TAGS"].split(",") if t.strip()]
    return {
        "title": fields["TITLE"].strip('"'),
        "description": fields["DESCRIPTION"].strip('"'),
        "tags": tags[:4],
        "image_prompt": fields.get("IMAGE_PROMPT", "").strip('"'),
        "body_markdown": body.strip(),
    }


def salvage_article(raw: str) -> dict:
    """Recovery when the model ignores the output format: first non-empty line
    becomes the title, a short plain second line becomes the description, and
    tags are harvested from a trailing '**Tags:** #...' footer if present."""
    raw = raw.strip()
    raw = re.sub(r"^```[a-z]*\n|\n```$", "", raw).strip()
    lines = raw.splitlines()

    idx = 0
    while idx < len(lines) and not lines[idx].strip():
        idx += 1
    title = lines[idx].lstrip("# ").strip().strip('"') if idx < len(lines) else "Untitled"
    idx += 1

    while idx < len(lines) and not lines[idx].strip():
        idx += 1
    description = ""
    if idx < len(lines):
        cand = lines[idx].strip()
        if cand and not cand.startswith(("#", ">", "-", "*", "`")) and len(cand) <= 200:
            description = cand
            idx += 1

    body = "\n".join(lines[idx:]).strip()

    if not description:
        para = next(
            (l.strip() for l in body.splitlines()
             if l.strip() and not l.strip().startswith(("#", ">", "-", "*", "`"))),
            "",
        )
        description = (para[:147] + "...") if len(para) > 150 else para

    tags = []
    m = re.search(r"\*\*Tags:\*\*\s*(.+)", body)
    if m:
        tags = [t.lstrip("#").strip().lower() for t in m.group(1).split() if t.strip("#").strip()]
    if not tags:
        tags = ["dataengineering"]

    print("NOTE: model skipped the output format; salvaged metadata heuristically.")
    return {
        "title": title,
        "description": description[:150],
        "tags": tags[:4],
        "image_prompt": "",
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
financial services and healthcare, writing under your own byline on Dev.to.

{topic_line}

VOICE AND STYLE (non-negotiable):
- First person, opinionated, direct. Take positions.
- Open with a relatable war story or pain the reader has lived, never generic filler.
- Concrete over abstract everywhere: real version numbers, real config keys.
- Short paragraphs. Occasional dry humor. Zero corporate filler phrases.

REQUIRED ARTICLE STRUCTURE (in the body, in this order):
1. A blockquote starting with "> **Why I chose this topic:**"
2. A hook opening (2-4 paragraphs).
3. A "## The real problem: ..." section.
4. 3-5 "## Step N: ..." sections walking through the solution with pinned code blocks.
5. A "## Lessons learned from production" section.
6. A "## Production considerations" section.
7. A "## Conclusion" with a "**Try it:**" call to action.
8. A horizontal rule, then "**SEO keywords:** ..." and "**Tags:** #tag1 #tag2 ..."

Do NOT include image syntax inside the body markdown. Just write text and code blocks.

LENGTH: 1500-2200 words. Do NOT include the title as an H1 in the body.

CRITICAL OUTPUT FORMAT — your response MUST start with the literal characters "TITLE:".
Do not write anything before it. Use this exact template, including the ===BODY=== line
on its own line:

TITLE: <compelling, specific title>
DESCRIPTION: <one-sentence summary under 150 characters>
TAGS: <3 to 4 lowercase single-word tags, comma-separated>
IMAGE_PROMPT: <1 to 3 search keywords for a cover photo, e.g. "server rack", "data center">
===BODY===
<the full article markdown following the structure above>
"""

    raw = call_gemini(prompt)
    art = parse_article(raw) or salvage_article(raw)

    cover_url = fetch_cover_image(art.get("image_prompt", ""))

    tags = art["tags"]
    ts = datetime.datetime.now().strftime("%m%d%Y_%H%M%S")
    folder = os.path.join(ARTICLES_DIR, f"article{ts}")
    os.makedirs(folder, exist_ok=True)
    path = os.path.join(folder, "article.md")

    q = chr(34)
    sq = chr(39)

    cover_line = f"cover_image: {cover_url}\n" if cover_url else ""
    front_matter = (
        "---\n"
        f"title: {q}{art['title'].replace(q, sq)}{q}\n"
        "published: false\n"
        f"description: {q}{art['description'].replace(q, sq)}{q}\n"
        f"tags: {', '.join(tags)}\n"
        f"{cover_line}"
        "canonical_url:\n"
        "---\n\n"
    )

    with open(path, "w") as f:
        f.write(front_matter + art["body_markdown"].strip() + "\n")
    print(f"Wrote {path}" + (" with cover image" if cover_url else " (no cover image)"))
    if topic:
        print(f"Topic used: {topic}")


if __name__ == "__main__":
    main()
