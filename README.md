# articles-html-portfolio

Automated article pipeline. Articles are written as Markdown in `posts/`, and a
GitHub Action publishes them to [Dev.to](https://dev.to) automatically on every push.

## How it works

```
posts/*.md  ──push──▶  GitHub Action  ──▶  scripts/post_to_devto.py  ──▶  Dev.to
```

1. Each article lives in `posts/` as a Markdown file with front matter (title, tags, cover image, etc.).
2. On every push to `main` that touches `posts/`, the workflow in `.github/workflows/publish.yml` runs.
3. `scripts/post_to_devto.py` reads each post and creates or updates the matching article on Dev.to.
   It matches by title, so re-running never creates duplicates.

## Repository layout

```
.
├── .github/workflows/publish.yml   # the automation (runs on push, daily, or manually)
├── assets/                         # images referenced by articles (served via raw.githubusercontent)
├── posts/                          # one Markdown file per article
├── scripts/post_to_devto.py        # the publisher
└── requirements.txt                # Python dependencies
```

## Setup (one time)

1. Add your Dev.to API key as a repository secret named **`DEVTO_API_KEY`**
   (Settings → Secrets and variables → Actions → New repository secret).
   Get the key at https://dev.to/settings/extensions.
2. That's it. Push an article and the Action handles the rest.

## Adding a new article

1. Create `posts/your-slug.md` with front matter:

   ```markdown
   ---
   title: "Your Article Title"
   published: false        # false = draft on Dev.to, true = live
   description: "One-line summary."
   tags: tag1, tag2, tag3  # up to 4, lowercase
   cover_image: https://raw.githubusercontent.com/aniketsoni1/articles-html-portfolio/main/assets/your-cover.png
   canonical_url:
   ---

   Your article body in Markdown...
   ```

2. Put any images in `assets/` and reference them with their raw GitHub URL.
3. Commit and push. The Action publishes it as a draft.
4. Review on Dev.to, then flip `published: false` → `true` and push again to go live.

## Running locally (optional)

```bash
pip install -r requirements.txt
DEVTO_API_KEY=your_key_here python scripts/post_to_devto.py
```
