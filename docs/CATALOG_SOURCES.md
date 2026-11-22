# Remote catalog sources: the plan

The catalog today is local: a curated file, or a fetch from a host the owner
explicitly allows. This document plans the next increment — **approved remote
sources** — and names the real sources it targets, so the plan is a decision
rather than a wish.

## The sources this targets

| Source | What it serves | How it is reached |
| --- | --- | --- |
| Hugging Face Hub (`huggingface.co`) | STT, LLM and TTS models, incl. the `ggml-format` Whisper models used by whisper.cpp (`ggerganov/whisper.cpp`) and the Piper voice repos | Public `GET /api/models?search=…&filter=…`, plus `GET /api/models/{repo_id}` for files and digests. No token: gated repositories are out of scope by design, not by accident. |
| `ggml-org/whisper.cpp` models | the same GGML weights, published as repository files | Same Hub API; the digest list in `models/README.md` is the reference for sizes and SHAs. |
| Local Jan inventory | whatever the owner's own Jan endpoint actually serves | `GET /v1/models` on the numeric loopback endpoint — already implemented. |
| Owner-managed curated catalog | a local file the owner signs | Already implemented (owner-managed HMAC keys gate the install). |

A source is **data**: a host, a kind (`hf` or `http`), and the filters the owner
chose. A source is never a credentials holder, and the client never authenticates
to one.

## What the increment adds

1. **`sources` in settings**, validated on read: a list of `{host, kind, filter,
   submit}` entries, bounded in count and length. Registering a source is an
   owner action on the *Approved sites* page; nothing is fetched at startup.
2. **A `catalog_sources.py` module** with `search(source, query)`,
   `entries(source)` and `card(entry)` — the Hub-shaped JSON mapped onto the
   existing truthful card (id, display name, category, engine, format, languages,
   license, size, digest, publisher). Unknown fields are skipped rather than
   guessed at, and a card without a digest or size is **not installable**.
3. **Search in the Market page** against a chosen source, then the existing
   verified path: HMAC verification, approval, resume, cancel, discard. The
   download transport stays bounded and allowlisted; a source that is not
   registered cannot be fetched at all.
4. **Tests**: a fixture Hub response (injected opener, exactly as
   `catalog_fetch` already does), plus cards from a real-shaped payload, refusal
   of a digest-less card, and a GTK case that a source must be registered before
   a search is offered.

## What it deliberately does not do

- No authentication, no token, no gated repository — a private model is not a
  public source, and pretending otherwise would be a security claim.
- No following redirects, no credentials, no cookies, no script execution; the
  same posture as `catalog_fetch`.
- No license decision on the owner's behalf: the license is shown on the card and
  the owner's registration is the consent.
- No automatic retry across mirrors: a source is the one the owner named.

## The larger gate this is part of

Remote catalog trust is already a separate gate in the release process
(`docs/RELEASE.md`). This plan adds *sources*; it does not close the transport
gate, which is closed by physical acceptance of a real fetch on the owner's
machine.

> Note: "pirateface.co" in the request is not a known model source and has been
> left out deliberately; `huggingface.co` is the source referred to. If a
> specific site was meant, the host is added as a registered `http` source with
> its own allowlist entry — the mechanism is the same, whatever the host.

## Where this sits in the remaining work

| # | Remaining gate | Blocking | Automated? |
| --- | --- | --- | --- |
| 1 | Approved remote sources (this plan) | nothing — next increment | yes, with injected fixtures |
| 2 | Remote transport accepted on a real network | a real fetch on the owner's machine | no (physical) |
| 3 | Physical microphone/output/model quality | real devices and models | no (physical) |
| 4 | OpenCode sessions, provider sign-in, real browser | integrations | no (not implemented) |

## Implemented: approved sources, metadata only

The plan above is now partly built (`feat/approved-sources`):

- **`src/tihulu_jarvis/catalog_sources.py`**: a `Source` (host, kind, filter) with
  `register`/`forget`/`entries`/`store`, `slug(repo_id)` — an *injective*
  escaping of a repository id into the registry's slug space, so `a/b` and
  `a-b` can never collide — and `cards(payload, source)` mapping a
  Hugging Face-shaped listing onto truthful cards: category from the pipeline
  tag, format and languages from the tags, license from the `license:` tag, and
  size plus a **64-hex digest** from the file list. A row without a size, a valid
  digest or a categorisable task yields **no card**; rows beyond the bound are
  counted as refused, so a truncated listing says so.
- **`safe_url`/`fetch`**: the one URL a source may be read from — HTTPS with the
  `search`/`limit` query this client builds, no fragment, no credentials, no
  non-ASCII, and a filter that is a search term rather than a URL fragment. The
  opener verifies TLS against the system store; a body is bounded and must parse
  as JSON before any use.
- **Client**: an *Approved sources* page — register, list, search and forget. A
  search appends cards and states how many rows were refused plus the honest
  limit: **nothing is installed**, because this build's approved transport is a
  numeric loopback endpoint; a remote listing is inspected, not fetched.
- **Settings**: `sources` stored and re-validated on read.

Still open from the plan: an approved *remote* transport for artifacts (its own
gate), and the richer filters (author, format) once a source needs them.
