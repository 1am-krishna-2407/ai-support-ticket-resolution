# Licensing and attribution

## Knowledge source

| Item | Value |
|---|---|
| Source | GitLab Docs, https://docs.gitlab.com |
| Licence | Creative Commons Attribution-ShareAlike 4.0 International (**CC BY-SA 4.0**) |
| Licence URL | https://creativecommons.org/licenses/by-sa/4.0/ |
| Terms of use | https://about.gitlab.com/terms/ |
| Verified | 2026-09-29 |

**How it was verified.** Every docs page footer contains a link
`<a href=https://creativecommons.org/licenses/by-sa/4.0/ rel="license noopener noreferrer">`
with the CC BY-SA 4.0 badge (checked in the raw HTML of `https://docs.gitlab.com/ci/runners/`).
Each manifest entry and each stored document records `license = "CC BY-SA 4.0"`.

**robots.txt (verified 2026-09-29).** For `User-agent: *` it disallows `/review-mr`,
`/upstream-review-mr`, and versioned archives (`/1`, `/2`, `/3` prefixes). None of the
curated pages fall under those paths. The fetcher downloads robots.txt before the first
request to a host and refuses disallowed URLs. If robots.txt cannot be retrieved (5xx or
network error), it treats the whole host as disallowed (RFC 9309).

**Crawl etiquette.** One request per second per host (`FETCH_MIN_INTERVAL_SECONDS`), an
identifying User-Agent (`FETCH_USER_AGENT`), and unchanged pages cause no re-processing.

## Attribution shown to users

Wherever retrieved documentation text is shown (sources in ticket drafts, retrieval
explorer, knowledge base views), the UI shows the page title linked to its canonical URL
together with:

> Content from GitLab Docs (https://docs.gitlab.com), licensed under CC BY-SA 4.0.
> This project is not affiliated with or endorsed by GitLab Inc.

The same string is exposed by the API (Phase 5) so every client can display it.

## Derived material

Chunks and embeddings are derived from CC BY-SA 4.0 content. Chunk text shown to users
keeps its source link. Anyone redistributing the stored knowledge base (the database
contents or `knowledge/raw/`) must do so under CC BY-SA 4.0 with attribution. The raw
HTML under `knowledge/raw/` is git-ignored and not redistributed with this repository.
The HTML fixtures under `backend/tests/fixtures/html/` are small excerpts kept for
testing, with attribution in that directory's README.

## Project code

The platform's own source code is separate from the documentation content, and its
licence is chosen by the project owner.
