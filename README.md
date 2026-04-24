# media-researcher

A research skill that takes a structured brief and produces a prioritised,
enriched list of media targets (podcasts, journalists, publications).

> **This skill produces research only. It does not send outreach.**

---

## Table of Contents

1. [Quick Start](#quick-start)
2. [Installation](#installation)
3. [CLI Reference](#cli-reference)
4. [Brief Format](#brief-format)
5. [Data Sources](#data-sources)
6. [Scoring Formula](#scoring-formula)
7. [Output Formats](#output-formats)
8. [Caching](#caching)
9. [Privacy & Ethics Policy](#privacy--ethics-policy)
10. [Environment Variables](#environment-variables)
11. [Development](#development)

---

## Quick Start

```bash
# Install
pip install -e media-researcher-core/

# Set at least one API key (everything degrades gracefully without keys)
export LISTEN_NOTES_API_KEY=your_key
export ANTHROPIC_API_KEY=your_key

# Run from a brief file
media-researcher run --brief briefs/ai_infrastructure.yaml --format markdown --depth deep

# Or interactively
media-researcher run --interactive
```

---

## Installation

```bash
cd media-researcher-core
pip install -e .
```

Python ≥ 3.11 required.

---

## CLI Reference

```
media-researcher run [OPTIONS]

Options:
  -b, --brief PATH          Path to YAML/JSON brief file
  -i, --interactive         Prompt for brief fields interactively
  -o, --out PATH            Output file path (auto-generated if omitted)
  -f, --format [markdown|json|csv|notion]
                            Output format (default: markdown)
  -d, --depth [light|medium|deep]
                            Personalization depth (overrides brief)
  -v, --verbose             Enable debug logging
```

### Examples

```bash
# Basic run from brief
media-researcher run --brief briefs/ai_infrastructure.yaml

# Deep personalization, JSON output
media-researcher run --brief briefs/fintech_startup.yaml --format json --depth deep

# Interactive with CSV output
media-researcher run --interactive --format csv

# Push results to Notion
media-researcher run --brief briefs/climate_tech.yaml --format notion
```

---

## Brief Format

Briefs are YAML (or JSON) files. All fields except `target_type` and `topic`
are optional.

```yaml
target_type: podcasts          # podcasts | journalists | publications | mixed
topic: "AI infrastructure, developer tools, Postgres"
audience_constraints:
  min_downloads: 5000          # minimum monthly downloads (podcasts)
  max_downloads: 500000        # maximum monthly downloads
  min_readers: null            # minimum monthly readers (publications)
  max_readers: null
recency_days: 90               # only targets active in the last N days
geo_filter: "US"               # optional: country/region filter
language: "en"                 # optional: language code
num_results: 20                # 1–100
depth: medium                  # light | medium | deep
extra_notes: "Focus on independent shows over major networks."
```

See `briefs/` for worked examples.

---

## Data Sources

### xAI / Grok (Primary — all target types)

| Source | API | Cost | Notes |
|--------|-----|------|-------|
| **xAI Grok** | OpenAI-compatible REST | Pay-per-token (see x.ai/api) | **Primary discovery + enrichment.** Set `XAI_API_KEY`. |

Grok runs for **all** target types (podcasts, journalists, publications) when
`XAI_API_KEY` is set. Its live web search access means it can find targets that
covered your topic in the last 24 hours — not just what was indexed weeks ago.

Grok is also used as the **preferred enricher** for medium/deep depth, providing
real-time recent work lookups and grounded pitch angles. Claude is the fallback
if `XAI_API_KEY` is absent.

Set `XAI_MODEL` to select the Grok model (default: `grok-3-latest`).

### Podcasts (Specialist API)

| Source | API | Cost | Notes |
|--------|-----|------|-------|
| **Listen Notes** | REST API | Free tier: 10 req/day; Paid from $10/mo | Supplements xAI. Set `LISTEN_NOTES_API_KEY`. |
| PodMatch / MatchMaker.fm | Web (no API) | Free | Not scraped (robots.txt); manual lookup only. |

### Journalists (Specialist APIs)

| Source | API | Cost | Notes |
|--------|-----|------|-------|
| **Muck Rack** | REST API | Enterprise pricing (contact Muck Rack) | Returns byline history. Set `MUCK_RACK_API_KEY`. |
| **Apollo.io** | REST API | Free tier: 50 exports/mo; Paid from $49/mo | Fallback if no Muck Rack key. Contact data only, no bylines. |

### Publications

| Source | Method | Cost |
|--------|--------|------|
| Curated lists | Built-in by topic keyword | Free |
| Claude enrichment | Identifies editors/beat writers | `ANTHROPIC_API_KEY` required |

Curated lists cover ~50 tech, developer, business, and AI/ML publications.
Extend them in `discovery/publications.py`.

### Enrichment (all target types)

| Source | Used for | Cost |
|--------|----------|------|
| **Claude** (`claude-sonnet-4-6`) | Recent work summaries, pitch angles | Billed per token via Anthropic API |

Without `ANTHROPIC_API_KEY`, enrichment runs at `light` depth (contact info
only from discovery APIs).

---

## Scoring Formula

Targets are ranked by a weighted composite score:

```
composite = w_topical_fit       × topical_fit_score
          + w_audience          × audience_score
          + w_recency           × recency_score
          + w_response_likelihood × response_likelihood_score
```

### Default Weights

| Component | Default | Env var override |
|-----------|---------|-----------------|
| `topical_fit` | 0.35 | `SCORER_WEIGHT_TOPICAL_FIT` |
| `audience` | 0.25 | `SCORER_WEIGHT_AUDIENCE` |
| `recency` | 0.20 | `SCORER_WEIGHT_RECENCY` |
| `response_likelihood` | 0.20 | `SCORER_WEIGHT_RESPONSE_LIKELIHOOD` |

Weights must sum to 1.0. Validation runs on startup.

### Sub-score Details

**topical_fit_score (0–1)**
Keyword overlap between the brief topic and the target's name, role, outlet,
and recent work titles. Tokenized, stop-word filtered.

**audience_score (0–1)**
Log-normalised audience size within the range of discovered candidates.
Unknown audience → 0.3 (neutral).

**recency_score (0–1)**
Based on the most recent relevant work found:
- ≤14 days → 1.0
- ≤30 days → 0.8
- ≤60 days → 0.6
- ≤90 days → 0.4
- Older → 0.1
- No dated work → 0.0

**response_likelihood_score (0–1)**
Heuristic based on target type and audience:
- Podcasts <10k audience: 0.85 (very approachable)
- Podcasts 10k–50k: 0.75
- Podcasts 50k–200k: 0.60
- Podcasts >200k: 0.40
- Journalists: 0.50 base + bonus for recent article on exact topic (up to 0.90)
- Publications: 0.30 (editorial pitches are slower to get responses)

---

## Output Formats

| Format | Description |
|--------|-------------|
| `markdown` | Summary table + one section per target. Default. |
| `json` | Full `ResearchReport` serialised to JSON. |
| `csv` | Flat table, one row per target. |
| `notion` | Pushes each target as a page to a Notion database. |

Output files go to `/mnt/user-data/outputs/` by default. Override with
`MEDIA_RESEARCHER_OUTPUT_DIR` or `--out`.

### Notion Setup

1. Create a Notion integration at https://www.notion.so/my-integrations
2. Share your target database with the integration
3. Ensure the database has these properties:
   `Name` (title), `Type` (select), `Outlet` (text), `Score` (number),
   `Email` (email), `Twitter` (url), `LinkedIn` (url), `Website` (url),
   `Source` (text), `PitchAngle` (text), `Notes` (text)
4. Set `NOTION_API_KEY` and `NOTION_DATABASE_ID`

---

## Caching

Enrichment results are cached for **7 days** in `~/.cache/media-researcher/`
using `diskcache`. This prevents redundant API calls when re-running similar
briefs.

Discovery results are also cached by brief hash (same TTL).

To change the cache directory: `MEDIA_RESEARCHER_CACHE_DIR=/path/to/cache`
To change the TTL: `MEDIA_RESEARCHER_CACHE_TTL=604800` (seconds; default = 7 days)

To clear the cache manually:
```bash
rm -rf ~/.cache/media-researcher/
```

---

## Privacy & Ethics Policy

### Email Address Policy

> **Only publicly listed emails are returned.**
>
> This tool only includes email addresses that appear on the target's own
> website or in their publicly available bio. It never guesses, infers, or
> pattern-derives email addresses (e.g., `firstname@outlet.com`).
>
> Apollo.io-sourced emails are explicitly excluded from output because they
> come from a contact database rather than the target's own publication.

### Scraping Policy

- We respect `robots.txt` on all domains.
- Sites that prohibit scraping are skipped; API or curated data is used instead.
- Rate limits on all APIs are respected via `tenacity` exponential backoff.

### Data Retention

- Enrichment results are cached locally for 7 days only.
- No contact data is sent to any external service beyond the APIs listed above.
- The user must explicitly opt in to a persistent contact database (not
  implemented by default).

---

## Environment Variables

| Variable | Required | Description |
|----------|----------|-------------|
| `XAI_API_KEY` | **Recommended** | xAI (Grok) key — primary discovery + enrichment for all target types |
| `XAI_MODEL` | Optional | Grok model ID (default: `grok-3-latest`) |
| `LISTEN_NOTES_API_KEY` | Podcast discovery | Listen Notes API key (supplements Grok) |
| `MUCK_RACK_API_KEY` | Journalist discovery | Muck Rack API key (supplements Grok) |
| `APOLLO_API_KEY` | Journalist discovery (fallback) | Apollo.io API key |
| `ANTHROPIC_API_KEY` | Enrichment fallback | Claude API key — used only if `XAI_API_KEY` is absent |
| `NOTION_API_KEY` | Notion output | Notion integration token |
| `NOTION_DATABASE_ID` | Notion output | Target database ID |
| `MEDIA_RESEARCHER_OUTPUT_DIR` | Optional | Output directory (default: `/mnt/user-data/outputs`) |
| `MEDIA_RESEARCHER_CACHE_DIR` | Optional | Cache directory (default: `~/.cache/media-researcher`) |
| `MEDIA_RESEARCHER_CACHE_TTL` | Optional | Cache TTL in seconds (default: 604800 = 7 days) |
| `MEDIA_RESEARCHER_CLAUDE_MODEL` | Optional | Claude model ID (default: `claude-sonnet-4-6`) |
| `SCORER_WEIGHT_TOPICAL_FIT` | Optional | Scoring weight (default: 0.35) |
| `SCORER_WEIGHT_AUDIENCE` | Optional | Scoring weight (default: 0.25) |
| `SCORER_WEIGHT_RECENCY` | Optional | Scoring weight (default: 0.20) |
| `SCORER_WEIGHT_RESPONSE_LIKELIHOOD` | Optional | Scoring weight (default: 0.20) |

---

## Development

```bash
cd media-researcher-core
pip install -e ".[dev]"

# Lint
ruff check .

# Tests
pytest tests/ -v
```

### Adding a new discovery source

1. Create `media_researcher_core/discovery/mysoure.py` extending `BaseDiscoverer`
2. Implement `async def discover(self, brief) -> list[MediaTarget]`
3. Register it in `runner.py` under the appropriate `TargetType` branch
4. Document the source in this README
