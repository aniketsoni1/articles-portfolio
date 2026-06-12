#!/usr/bin/env python3
"""
Generate one article per run using the Google Gemini API (free tier) and save
it into articles/ as a Markdown file with Dev.to front matter and a dynamic cover image.
"""

import os
import re
import sys
import json
import time
import datetime
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
                "maxOutputTokens": 8192,  
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


def parse_article(raw: str) -> dict | None:
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

    # Require IMAGE_PROMPT as well now
    if {"TITLE", "DESCRIPTION", "TAGS", "IMAGE_PROMPT"} - fields.keys():
        return None

    tags = [t.strip().lower().lstrip("#") for t in fields["TAGS"].split(",") if t.strip()]
    
    # Generate a clean public image URL using Unsplash's source API based on Gemini's keyword
    clean_keyword = urllib.parse.quote(fields["IMAGE_PROMPT"].strip('"'))
    cover_url = f"https://images.unsplash.com/photo-1518770660439-4636190af475?auto=format&fit=crop&w=1000&q=80&sig={int(time.time())}"
    if clean_keyword:
        # Fallback to source endpoint using keyword search
        cover_url = f"https://source.unsplash.com/featured/1000x500/?{clean_keyword}"

    return {
        "title": fields["TITLE"].strip('"'),
        "description": fields["DESCRIPTION"].strip('"'),
        "tags": tags[:4],
        "cover_image": cover_url,
        "body_markdown": body.strip(),
    }


def main() -> None:
    print(f"Using model: {MODEL}")

    topic = pick_topic(get_topics())
    topic_line = (
        f'The topic is: "{topic}".'
        if topic
        else f"Pick a fresh, specific, currently-relevant topic in {NICHE}."
    )

    # Added IMAGE_PROMPT guidance to the strict output block
    prompt = f"""You are a senior data/platform engineer with 6+ years of production experience in
financial services and healthcare, writing under your own byline on Dev.to.

{topic_line}

VOICE AND STYLE (non-negotiable):
- First person, opinionated, direct. Take positions.
- Concrete over abstract everywhere: real version numbers, real config keys.
- Short paragraphs. Occasional dry humor.

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
Use this exact template, including the ===BODY=== line on its own line:

TITLE: <compelling, specific title>
DESCRIPTION: <one-sentence summary under 150 characters>
TAGS: <3 to 4 lowercase single-word tags, comma-separated>
IMAGE_PROMPT: <1 to 3 technical search keywords for photography/diagrams, e.g., "server-rack", "cyberpunk-coding", "database-cluster">
===BODY===
<the full article markdown following the structure above>
"""

    raw = call_gemini(prompt)
    
    # For simplicity in image generation parsing, if it fails format matching, 
    # we enforce a standard default rather than breaking.
    art = parse_article(raw)
    if not art:
        sys.exit("API Output failed to match strict format metadata. Skipping to protect image workflow.")

    tags = art["tags"]
    ts = datetime.datetime.now().strftime("%m%d%Y_%H%M%S")
    folder = os.path.join(ARTICLES_DIR, f"article{ts}")
    os.makedirs(folder, exist_ok=True)
    path = os.path.join(folder, "article.md")

    q = chr(34)  
    sq = chr(39)  
    
    # Dev.to natively parses 'cover_image' from the front matter to display it beautifully!
    front_matter = (
        "---\n"
        f"title: {q}{art['title'].replace(q, sq)}{q}\n"
        "published: false\n"
        f"description: {q}{art['description'].replace(q, sq)}{q}\n"
        f"tags: {', '.join(tags)}\n"
        f"cover_image: {art['cover_image']}\n"
        "canonical_url:\n"
        "---\n\n"
    )

    with open(path, "w") as f:
        f.write(front_matter + art["body_markdown"].strip() + "\n")
    print(f"Wrote {path} with dynamic cover image linked!")


if __name__ == "__main__":
    main()
