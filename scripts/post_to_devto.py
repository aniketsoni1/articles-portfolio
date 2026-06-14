#!/usr/bin/env python3
"""
Sync articles under articles/*/article.md to Dev.to.

- Reads the Dev.to API key from the DEVTO_API_KEY environment variable
  (a GitHub Actions secret; never hard-code it).
- Idempotent: matches existing Dev.to articles by title. If a match exists it
  UPDATES that article; otherwise it CREATES one. Re-running never duplicates.
- The `published:` front-matter field controls draft vs. live.

By default ONLY today's article is synced (folder dated like article<MMDDYYYY>_*).
Older articles are left untouched on Dev.to. To sync everything (e.g. a manual
backfill), set the environment variable PUBLISH_ALL=true.

Run locally:   DEVTO_API_KEY=xxxx python scripts/post_to_devto.py
"""

import os
import re
import sys
import glob
import json
import time
import datetime
import urllib.request
import urllib.error

import frontmatter  # pip install python-frontmatter

API_BASE = "https://dev.to/api"
POSTS_GLOB = "articles/*/article.md"
DATE_RE = re.compile(r"article(\d{2})(\d{2})(\d{4})_")


def api_key() -> str:
    key = os.environ.get("DEVTO_API_KEY")
    if not key:
        sys.exit("ERROR: DEVTO_API_KEY is not set.")
    return key


def request(method: str, path: str, body: dict | None = None) -> dict | list:
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(
        f"{API_BASE}{path}",
        data=data,
        method=method,
        headers={
            "api-key": api_key(),
            "Content-Type": "application/json",
            "User-Agent": "Mozilla/5.0 (compatible; devto-publisher/1.0; +https://github.com/aniketsoni1/articles-html-portfolio)",
        },
    )
    try:
        with urllib.request.urlopen(req) as resp:
            return json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        raise RuntimeError(
            f"Dev.to API error {e.code} on {method} {path}: {e.read().decode()}"
        )


def article_date(path: str) -> datetime.date | None:
    """Extract the date from a folder named article<MMDDYYYY>_<HHMMSS>."""
    m = DATE_RE.search(os.path.basename(os.path.dirname(path)))
    if not m:
        return None
    mm, dd, yyyy = map(int, m.groups())
    try:
        return datetime.date(yyyy, mm, dd)
    except ValueError:
        return None


def existing_articles_by_title() -> dict[str, int]:
    """Map of title -> article id for all your Dev.to articles (published + drafts)."""
    index, page = {}, 1
    while True:
        batch = request("GET", f"/articles/me/all?per_page=100&page={page}")
        if not batch:
            break
        for art in batch:
            index[art["title"].strip()] = art["id"]
        page += 1
    return index


def build_payload(post: "frontmatter.Post") -> dict:
    meta = post.metadata
    article = {
        "title": meta["title"],
        "body_markdown": post.content,
        "published": bool(meta.get("published", False)),
    }
    if meta.get("description"):
        article["description"] = meta["description"]
    if meta.get("tags"):
        tags = meta["tags"]
        if isinstance(tags, str):
            tags = [t.strip() for t in tags.split(",")]
        # Dev.to tags must be lowercase and strictly alphanumeric (no hyphens,
        # spaces, or symbols). Strip anything else; drop any that become empty.
        clean = []
        for t in tags:
            t = re.sub(r"[^a-z0-9]", "", str(t).lower())
            if t:
                clean.append(t)
        if clean:
            article["tags"] = clean[:4]  # Dev.to allows up to 4
    if meta.get("cover_image"):
        article["main_image"] = meta["cover_image"]
    if meta.get("canonical_url"):
        article["canonical_url"] = meta["canonical_url"]
    return {"article": article}


def select_files() -> list[str]:
    files = sorted(glob.glob(POSTS_GLOB))
    publish_all = os.environ.get("PUBLISH_ALL", "").strip().lower() in ("1", "true", "yes")
    if publish_all:
        print(f"PUBLISH_ALL set — syncing all {len(files)} article(s).")
        return files
    today = datetime.date.today()
    todays = [f for f in files if article_date(f) == today]
    print(
        f"Today is {today} (UTC). Publishing {len(todays)} article(s) dated today; "
        f"leaving {len(files) - len(todays)} older one(s) untouched."
    )
    return todays


def main() -> None:
    files = select_files()
    if not files:
        print("Nothing to publish.")
        return

    existing = existing_articles_by_title()

    failures = 0
    for path in files:
        post = frontmatter.load(path)
        title = post.metadata.get("title", "").strip()
        if not title or set(title) <= set("*-_# ."):
            print(f"SKIP {path}: missing or junk title ({title!r})")
            continue
        try:
            payload = build_payload(post)
            if title in existing:
                request("PUT", f"/articles/{existing[title]}", payload)
                print(f"UPDATED  '{title}'")
            else:
                request("POST", "/articles", payload)
                print(f"CREATED  '{title}'")
        except RuntimeError as e:
            failures += 1
            print(f"FAILED   '{title}': {e}")
        time.sleep(2)  # be polite to the rate limiter

    if failures:
        sys.exit(f"{failures} article(s) failed to sync.")


if __name__ == "__main__":
    main()