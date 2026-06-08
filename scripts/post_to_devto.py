#!/usr/bin/env python3
"""
Sync every Markdown file in posts/ to Dev.to.

- Reads the Dev.to API key from the DEVTO_API_KEY environment variable
  (set as a GitHub Actions secret; never hard-code it).
- Idempotent: matches existing Dev.to articles by title. If a match exists
  it UPDATES that article; otherwise it CREATES a new one. Re-running is safe
  and will not create duplicates.
- The `published:` field in each file's front matter controls draft vs. live.
  Leave it `false` to land as a draft you review, flip to `true` to go live.

Run locally:   DEVTO_API_KEY=xxxx python scripts/post_to_devto.py
"""

import os
import sys
import glob
import json
import time
import urllib.request
import urllib.error

import frontmatter  # pip install python-frontmatter

API_BASE = "https://dev.to/api"
POSTS_DIR = "posts"


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
        headers={"api-key": api_key(), "Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req) as resp:
            return json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        sys.exit(f"Dev.to API error {e.code} on {method} {path}: {e.read().decode()}")


def existing_articles_by_title() -> dict[str, int]:
    """Map of title -> article id for all of your Dev.to articles (published + drafts)."""
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
        article["tags"] = tags[:4]  # Dev.to allows up to 4
    if meta.get("cover_image"):
        article["main_image"] = meta["cover_image"]
    if meta.get("canonical_url"):
        article["canonical_url"] = meta["canonical_url"]
    return {"article": article}


def main() -> None:
    files = sorted(glob.glob(os.path.join(POSTS_DIR, "*.md")))
    if not files:
        print("No markdown files found in posts/. Nothing to do.")
        return

    existing = existing_articles_by_title()

    for path in files:
        post = frontmatter.load(path)
        title = post.metadata.get("title", "").strip()
        if not title:
            print(f"SKIP {path}: no title in front matter")
            continue

        payload = build_payload(post)
        if title in existing:
            request("PUT", f"/articles/{existing[title]}", payload)
            print(f"UPDATED  '{title}'")
        else:
            request("POST", "/articles", payload)
            print(f"CREATED  '{title}'")
        time.sleep(2)  # be polite to the rate limiter


if __name__ == "__main__":
    main()
