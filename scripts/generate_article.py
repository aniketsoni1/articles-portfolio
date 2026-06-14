#!/usr/bin/env python3
"""
Generate one article per run with the Google Gemini API and save it under
articles/ as Dev.to-ready Markdown.

Variety features (so the output doesn't read as templated/AI):
- One of several distinct article STRUCTURES is chosen at random each run.
- Title style, the optional "Why I chose this topic" note, and the footer are
  randomized.
- Cover/inline images come from Unsplash (if UNSPLASH_ACCESS_KEY is set) and are
  de-duplicated against a persisted history (articles/.image_history.json) AND
  picked at random from a larger result pool, so the same photo isn't reused.

A quality gate rejects obviously-broken output (junk title, too-short body) so
nothing junk is ever written or committed.
"""

import os
import re
import sys
import json
import time
import random
import datetime
import urllib.parse
import urllib.request
import urllib.error

MODEL = os.environ.get("GEMINI_MODEL", "gemini-3.1-flash-lite").strip() or "gemini-3.1-flash-lite"
NICHE = "data engineering, AI/ML, and cloud-native systems"
TOPICS_FILE = "topics.txt"
ARTICLES_DIR = "articles"
HISTORY_FILE = os.path.join(ARTICLES_DIR, ".image_history.json")

ENDPOINT = (
    f"https://generativelanguage.googleapis.com/v1beta/models/{MODEL}:generateContent"
)

# --- Article structures: one is chosen at random each run -------------------
STRUCTURES = [
    ("playbook", """Write it as a hands-on playbook:
- A hook opening (2-4 paragraphs) built on a concrete pain the reader has lived.
- A "## The real problem" section that reframes the issue.
- 3-5 "## Step N: ..." sections, each with a pinned, runnable code or config block.
- A "## Lessons learned from production" section with hard-won, specific bullets.
- A "## Conclusion" ending in a "**Try it:**" call to action."""),

    ("listicle", """Write it as a numbered field guide:
- A punchy 2-3 paragraph opening that promises specific, hard-won lessons.
- 5-8 numbered "## N. <pattern, mistake, or rule>" sections, each with a short
  rationale and a code/config snippet where it earns its place.
- A "## Conclusion" that ties the items together and ends with a direct question."""),

    ("deep-dive", """Write it as a conceptual deep-dive:
- Open by naming something engineers use daily but rarely understand deeply.
- "## How it actually works" — the mechanics, with code or a worked example.
- "## The tradeoffs nobody mentions" — honest downsides, with specifics.
- "## When to reach for it (and when not to)" — decision guidance.
- A short "## Conclusion"."""),

    ("post-mortem", """Write it as an incident post-mortem narrative:
- Open in the middle of the incident (timestamps, what broke, the pager going off).
- "## What we saw" — symptoms and the false leads.
- "## Root cause" — the real mechanism, with the offending code/config.
- "## The fix" — what changed, concretely.
- "## What we changed so it never happens again" — systemic lessons."""),

    ("comparison", """Write it as a head-to-head comparison:
- Open with the decision a reader is actually facing.
- "## The contenders" — briefly introduce the options.
- 3-4 "## <dimension>" sections comparing them on real criteria (cost, ops burden,
  failure modes), grounded in concrete numbers.
- "## What I'd pick, and why" — an opinionated recommendation with honest caveats."""),

    ("argument", """Write it as an opinionated argument:
- Open with a claim many of your peers would push back on.
- "## Why the common approach falls short" — with specifics.
- 2-3 sections building the case, each grounded in a real example or code block.
- "## The objections (and my answers)" — steelman the other side, then respond.
- A "## Conclusion" restating the position."""),
]

TITLE_STYLES = [
    "a specific claim with a colon and a subtitle",
    "a provocative question",
    "a bold, plain-spoken one-line opinion",
    'a "How I/we ..." or "What ... taught me" framing',
    "a concrete description of the outcome, no buzzwords",
]


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
            "generationConfig": {"maxOutputTokens": 16384, "temperature": 0.85},
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


# --- Image history (persisted so photos are never reused) -------------------
def load_image_history() -> set:
    try:
        with open(HISTORY_FILE) as f:
            return set(json.load(f))
    except Exception:
        return set()


def save_image_history(ids: set) -> None:
    try:
        os.makedirs(ARTICLES_DIR, exist_ok=True)
        with open(HISTORY_FILE, "w") as f:
            json.dump(sorted(ids), f, indent=0)
    except Exception:
        pass


def fetch_images(keywords: list[str]) -> list[dict]:
    """One landscape Unsplash photo per keyword, picked at RANDOM from a large
    result pool and de-duplicated against history. Best-effort: any failure just
    yields fewer images and never sinks the run."""
    access_key = os.environ.get("UNSPLASH_ACCESS_KEY", "").strip()
    if not access_key:
        return []

    history = load_image_history()
    utm = "utm_source=articles_pipeline&utm_medium=referral"
    images, seen = [], set()
    for kw in keywords:
        kw = kw.strip().strip('"').replace("-", " ")
        if not kw:
            continue
        try:
            url = "https://api.unsplash.com/search/photos?" + urllib.parse.urlencode(
                {"query": kw, "per_page": 30, "orientation": "landscape"}
            )
            req = urllib.request.Request(
                url, headers={"Authorization": f"Client-ID {access_key}"}
            )
            with urllib.request.urlopen(req, timeout=15) as r:
                results = json.loads(r.read().decode()).get("results", [])
            random.shuffle(results)
            # prefer a photo we've never used; fall back to any unseen-this-run
            photo = next(
                (p for p in results if p["id"] not in seen and p["id"] not in history),
                None,
            ) or next((p for p in results if p["id"] not in seen), None)
            if not photo:
                continue
            seen.add(photo["id"])
            history.add(photo["id"])
            images.append(
                {
                    "url": photo["urls"]["regular"],
                    "author": photo["user"]["name"],
                    "author_url": f"{photo['user']['links']['html']}?{utm}",
                    "unsplash_url": f"https://unsplash.com/?{utm}",
                }
            )
            try:
                dl = photo.get("links", {}).get("download_location")
                if dl:
                    dreq = urllib.request.Request(
                        dl, headers={"Authorization": f"Client-ID {access_key}"}
                    )
                    urllib.request.urlopen(dreq, timeout=10).read()
            except Exception:
                pass
        except Exception as e:  # noqa: BLE001
            print(f"NOTE: Unsplash lookup failed for '{kw}' ({e}); skipping.")
    save_image_history(history)
    return images


def image_markdown(img: dict) -> str:
    return (
        f"![Photo by {img['author']} on Unsplash]({img['url']})\n"
        f"*Photo by [{img['author']}]({img['author_url']}) on "
        f"[Unsplash]({img['unsplash_url']})*\n"
    )


def insert_inline_images(body: str, images: list[dict]) -> str:
    if not images:
        return body
    lines = body.split("\n")
    heading_idxs = [i for i, l in enumerate(lines) if l.startswith("## ")]
    insertions = []
    if len(images) >= 1 and len(heading_idxs) >= 2:
        insertions.append((heading_idxs[1], images[0]))
    if len(images) >= 2 and len(heading_idxs) >= 4:
        anchor = heading_idxs[-2]
        if all(idx != anchor for idx, _ in insertions):
            insertions.append((anchor, images[1]))
    for idx, img in sorted(insertions, key=lambda x: -x[0]):
        lines[idx:idx] = [image_markdown(img), ""]
    return "\n".join(lines)


def parse_article(raw: str) -> dict | None:
    raw = raw.strip()
    raw = re.sub(r"^```[a-z]*\n|\n```$", "", raw).strip()
    if "===BODY===" not in raw:
        return None
    header, body = raw.split("===BODY===", 1)
    fields = {}
    for line in header.splitlines():
        m = re.match(r"^(TITLE|DESCRIPTION|TAGS|IMAGE_PROMPTS?)\s*:\s*(.+)$", line.strip())
        if m:
            fields[m.group(1)] = m.group(2).strip()
    if {"TITLE", "DESCRIPTION", "TAGS"} - fields.keys():
        return None
    tags = [t.strip().lower().lstrip("#") for t in fields["TAGS"].split(",") if t.strip()]
    raw_prompts = fields.get("IMAGE_PROMPTS") or fields.get("IMAGE_PROMPT") or ""
    sep = "|" if "|" in raw_prompts else ","
    image_prompts = [p.strip().strip('"') for p in raw_prompts.split(sep) if p.strip()][:3]
    return {
        "title": fields["TITLE"].strip('"'),
        "description": fields["DESCRIPTION"].strip('"'),
        "tags": tags[:4],
        "image_prompts": image_prompts,
        "body_markdown": body.strip(),
    }


def salvage_article(raw: str) -> dict:
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
        "image_prompts": [],
        "body_markdown": body,
    }


def validate_article(art: dict) -> str | None:
    """Return a reason string if the article is unusable, else None."""
    title = (art.get("title") or "").strip()
    body = art.get("body_markdown") or ""
    if len(title) < 12 or set(title) <= set("*-_# ."):
        return f"bad title {title!r}"
    wc = len(body.split())
    if wc < 400:
        return f"body too short ({wc} words)"
    return None


def build_prompt(topic_line: str) -> tuple[str, str]:
    label, structure = random.choice(STRUCTURES)
    title_hint = random.choice(TITLE_STYLES)
    include_why = random.random() < 0.5
    include_footer = random.random() < 0.35

    why_block = (
        '- A blockquote near the top starting with "> **Why I chose this topic:**" '
        "(2-3 personal sentences).\n"
        if include_why else ""
    )
    footer_block = (
        'End the article with a horizontal rule, then a single line '
        '"**Tags:** #tag1 #tag2 #tag3".\n'
        if include_footer else ""
    )

    prompt = f"""You are a senior data/platform engineer with 6+ years of production experience in
financial services and healthcare, writing under your own byline on Dev.to.

{topic_line}

VOICE: first person, opinionated, direct. Concrete over abstract everywhere — real
version numbers, real config keys, real failure modes. Short paragraphs. Occasional
dry humor. Zero corporate filler.

TITLE STYLE: make the title {title_hint}. It must not sound templated or generic.

STRUCTURE for THIS article (follow it, but write naturally — don't echo these labels):
{why_block}{structure}
{footer_block}
Do NOT include any image syntax in the body. Do NOT repeat the title as an H1 in the body.
LENGTH: 1400-2200 words.

CRITICAL OUTPUT FORMAT — your response MUST start with the literal characters "TITLE:".
Do not write anything before it. Use this exact template, with ===BODY=== on its own line:

TITLE: <title>
DESCRIPTION: <one-sentence summary under 150 characters>
TAGS: <3 to 4 lowercase single-word tags, comma-separated, no hyphens>
IMAGE_PROMPTS: <three photo search keywords separated by " | ", each 1-3 words>
===BODY===
<the full article markdown following the structure above>
"""
    return prompt, label


def main() -> None:
    print(f"Using model: {MODEL}")
    topic = pick_topic(get_topics())
    topic_line = (
        f'The topic is: "{topic}".'
        if topic
        else f"Pick a fresh, specific, currently-relevant topic in {NICHE}."
    )

    prompt, structure_label = build_prompt(topic_line)
    print(f"Structure: {structure_label}")

    raw = call_gemini(prompt)
    art = parse_article(raw) or salvage_article(raw)

    problem = validate_article(art)
    if problem:
        sys.exit(f"Generated article rejected ({problem}); nothing written.")

    images = fetch_images(art.get("image_prompts", []))
    cover_url = images[0]["url"] if images else ""
    body = insert_inline_images(art["body_markdown"], images[1:3])
    if images:
        c = images[0]
        body += (
            f"\n\n*Cover photo by [{c['author']}]({c['author_url']}) on "
            f"[Unsplash]({c['unsplash_url']}).*"
        )

    # sanitize tags to Dev.to's alphanumeric-only rule
    tags = [re.sub(r"[^a-z0-9]", "", t) for t in art["tags"]]
    tags = [t for t in tags if t][:4] or ["dataengineering"]

    ts = datetime.datetime.now().strftime("%m%d%Y_%H%M%S")
    folder = os.path.join(ARTICLES_DIR, f"article{ts}")
    os.makedirs(folder, exist_ok=True)
    path = os.path.join(folder, "article.md")

    q, sq = chr(34), chr(39)
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
        f.write(front_matter + body.strip() + "\n")
    print(f"Wrote {path} (structure: {structure_label}, cover: {'yes' if cover_url else 'no'}, "
          f"inline images: {max(0, len(images) - 1)})")
    if topic:
        print(f"Topic used: {topic}")


if __name__ == "__main__":
    main()