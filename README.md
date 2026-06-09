# articles-html-portfolio

Automated article pipeline. Each article lives in its own timestamped folder under
`articles/`, and a GitHub Action publishes it to [Dev.to](https://dev.to). A second
Action can also generate a fresh article every day for free.

## How it works

```
articles/<folder>/article.md  ──▶  GitHub Action  ──▶  scripts/post_to_devto.py  ──▶  Dev.to
```

1. Each article is a self-contained folder: `articles/articleMMDDYYYY_HHMMSS/` containing
   `article.md` (with front matter) plus any images that article uses.
2. On every push to `main` that touches `articles/`, `.github/workflows/publish.yml` runs.
3. `scripts/post_to_devto.py` reads every `articles/*/article.md` and creates or updates
   the matching article on Dev.to. It matches by title, so re-running never duplicates.

Timestamped folders mean two articles never overwrite each other, and an article's images
always travel with it.

## Repository layout

```
.
├── .github/workflows/
│   ├── publish.yml                 # posts articles to Dev.to (on push, daily, manual)
│   └── generate.yml                # writes a new article daily via Gemini (free)
├── articles/
│   └── article06082026_180000/     # one folder per article
│       ├── article.md
│       ├── cover.png
│       └── fig1.png ...
├── scripts/
│   ├── post_to_devto.py            # the publisher
│   └── generate_article.py         # the daily writer
├── requirements.txt
└── README.md
```

## One-time setup

Add two repository secrets (Settings → Secrets and variables → Actions):

- `DEVTO_API_KEY` — from https://dev.to/settings/extensions (publishing).
- `GEMINI_API_KEY` — from https://aistudio.google.com/app/apikey (daily generation).
- `ARTICLE_TOPICS` — *(optional)* your private topic list, one idea per line. Kept in a
  secret so it stays hidden from the public repo while the workflow can still read it.
  The generator rotates through the list by date; leave it unset to let Gemini pick its
  own topic in the configured niche.

## Adding an article by hand

1. Create a folder `articles/article<MMDDYYYY>_<HHMMSS>/` (any unique name works).
2. Inside it, create `article.md`:

   ```markdown
   ---
   title: "Your Article Title"
   published: false        # false = draft on Dev.to, true = live
   description: "One-line summary."
   tags: tag1, tag2, tag3  # up to 4, lowercase
   cover_image: https://raw.githubusercontent.com/aniketsoni1/articles-html-portfolio/main/articles/<folder>/cover.png
   canonical_url:
   ---

   Your article body in Markdown...
   ```

3. Drop images in the same folder; reference them with their raw GitHub URL.
4. Commit and push. The Action publishes it (as a draft if `published: false`).
   Review on Dev.to, then flip to `published: true` and push again to go live.

## Daily auto-generation (free, via Google Gemini)

`.github/workflows/generate.yml` runs daily at 08:00 UTC. It:

1. Picks a topic from the `ARTICLE_TOPICS` secret (rotated by date), or invents one if unset,
2. Generates an article with the Gemini free-tier API (`scripts/generate_article.py`),
3. Writes it to a new `articles/article<timestamp>/article.md` as a draft (`published: false`),
4. Commits it back and pushes the draft to Dev.to.

You review it on Dev.to and hit **Publish** when ready. To change topics, edit the
`ARTICLE_TOPICS` secret (Settings → Secrets and variables → Actions) — one idea per line.

> Gemini model names and free-tier limits change over time. If a run fails with a
> model/404 error, update `MODEL` in `scripts/generate_article.py` to a current free
> model listed in AI Studio.

## Running locally (optional)

```bash
pip install -r requirements.txt
DEVTO_API_KEY=your_key  python scripts/post_to_devto.py
ARTICLE_TOPICS=$"topic one\ntopic two" GEMINI_API_KEY=your_key python scripts/generate_article.py
```
