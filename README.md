# articles-html-portfolio

Automated article pipeline. Each article lives in its own timestamped folder under
`articles/`, and a GitHub Action publishes it to [Dev.to](https://dev.to). A second
Action can generate a fresh article every day for free using the Google Gemini API.

## How it works

```
articles/<folder>/article.md  ──▶  GitHub Action  ──▶  scripts/post_to_devto.py  ──▶  Dev.to
```

1. Each article is a self-contained folder: `articles/articleMMDDYYYY_HHMMSS/` containing
   `article.md` (with front matter) plus any images that article uses.
2. The generator (`generate.yml`) writes a new article daily; you can also add articles by hand.
3. The publisher (`publish.yml` / `scripts/post_to_devto.py`) creates or updates the matching
   article on Dev.to. It matches by **title**, so re-running never duplicates.

Articles are pushed as **drafts** (`published: false`). You review each one on Dev.to and hit
**Publish** when you're happy with it.

## Repository layout

```
.
├── .github/workflows/
│   ├── generate.yml                # writes a new article daily via Gemini (18:00 UTC)
│   └── publish.yml                 # posts articles to Dev.to (18:05 UTC, on push, manual)
├── articles/
│   ├── article06082026_180000/     # one self-contained folder per article
│   │   ├── article.md
│   │   ├── cover.png
│   │   └── fig1.png ...
│   ├── .image_history.json         # photo IDs already used (so images never repeat)
│   └── .topic_history.json         # hashes of recent topics (so topics don't repeat)
├── scripts/
│   ├── generate_article.py         # the daily writer
│   └── post_to_devto.py            # the publisher
├── requirements.txt
└── README.md
```

## One-time setup

In **Settings → Secrets and variables → Actions**:

**Secrets** (tab: *Secrets*)

- `DEVTO_API_KEY` — **required**. From https://dev.to/settings/extensions (publishing).
- `GEMINI_API_KEY` — required for daily generation. From https://aistudio.google.com/app/apikey
- `ARTICLE_TOPICS` — *optional*. Your private topic list, one idea per line. Kept in a secret
  so it stays hidden from the public repo while the workflow can still read it. Leave unset to
  let Gemini pick its own topic in the configured niche.
- `UNSPLASH_ACCESS_KEY` — *optional*. From https://unsplash.com/developers. Enables a cover
  photo and inline images. If unset, articles are published without photos (Dev.to renders fine).

**Variables** (tab: *Variables*)

- `GEMINI_MODEL` — *optional*. Overrides the model without editing code (e.g. if the default
  is congested or renamed). Defaults to `gemini-3.1-flash-lite` when unset.

## Publishing behaviour

By default the publisher syncs **only today's article** — it reads the date from each folder
name (`articleMMDDYYYY_*`) and leaves older articles on Dev.to untouched. This keeps a daily
run from re-pushing your entire back catalogue.

To sync **everything** (e.g. a one-off backfill), run the **Publish to Dev.to** workflow
manually from the Actions tab and tick the **publish_all** checkbox. (Locally, set the env var
`PUBLISH_ALL=true`.)

Other publisher behaviour worth knowing:

- **Tags are sanitized** to Dev.to's rule (lowercase, alphanumeric only) — e.g.
  `cost-optimization` becomes `costoptimization`.
- **Resilient**: if one article fails to sync, the rest still go; the run reports the failure
  at the end.
- **Junk-title guard**: articles with empty or broken titles (e.g. `***`) are skipped.

## Adding an article by hand

1. Create a folder `articles/article<MMDDYYYY>_<HHMMSS>/`. Use **today's** date so the
   default publisher picks it up (otherwise publish manually with *publish_all*).
2. Inside it, create `article.md`:

   ```markdown
   ---
   title: "Your Article Title"
   published: false        # false = draft on Dev.to, true = live
   description: "One-line summary."
   tags: tag1, tag2, tag3  # up to 4, lowercase, alphanumeric
   cover_image: https://raw.githubusercontent.com/aniketsoni1/articles-html-portfolio/main/articles/<folder>/cover.png
   canonical_url:
   ---

   Your article body in Markdown...
   ```

3. Drop images in the same folder; reference them with their raw GitHub URL.
4. Commit and push. The Action publishes it as a draft. Review on Dev.to, then flip to
   `published: true` and push again to go live.

## Daily auto-generation (free, via Google Gemini)

`generate.yml` runs daily at **18:00 UTC**, and `publish.yml` runs at **18:05 UTC** (five
minutes later, to publish whatever was just generated). Each generation run:

1. Picks a topic at **random** from `ARTICLE_TOPICS` (avoiding recently-used ones; history is
   stored as SHA hashes so the private list never leaks), or invents one if the secret is unset.
2. Chooses one of several **article structures at random** — playbook, numbered field guide,
   conceptual deep-dive, incident post-mortem, head-to-head comparison, or opinionated argument —
   and randomizes the opening style and title style, so articles don't all read the same.
3. Generates the article with Gemini, retrying automatically on transient errors (429 / 5xx).
4. Runs a **quality gate**: obviously-broken output (junk title, too-short body) is rejected and
   nothing is written, so junk never reaches the repo or Dev.to.
5. If `UNSPLASH_ACCESS_KEY` is set, adds a cover and inline photos, picked at random and
   de-duplicated against `.image_history.json` so the same photo is never reused.
6. Writes it to a new `articles/article<timestamp>/article.md` as a draft, commits it, and
   pushes the draft to Dev.to.

To change topics, edit the `ARTICLE_TOPICS` secret — one idea per line.

> **Note:** Gemini model names and free-tier limits change over time. If a run fails with a
> model/404 error, set the `GEMINI_MODEL` variable (or edit `MODEL` in
> `scripts/generate_article.py`) to a current free model listed in AI Studio.

## A note on quality

Auto-generated drafts are a starting point, not finished work. Always read each one before
publishing — generated articles can state specific commands, version numbers, or claims that are
subtly wrong, and everything goes out under your byline. The draft-then-manual-publish step is
the safeguard; keep it.

## Running locally (optional)

```bash
pip install -r requirements.txt

# publish (today's article only)
DEVTO_API_KEY=your_key python scripts/post_to_devto.py

# publish everything
PUBLISH_ALL=true DEVTO_API_KEY=your_key python scripts/post_to_devto.py

# generate one article (bash $'...' preserves the newlines between topics)
ARTICLE_TOPICS=$'topic one\ntopic two' GEMINI_API_KEY=your_key python scripts/generate_article.py
```