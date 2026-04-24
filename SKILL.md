# media-researcher Skill

## Purpose
Produce a prioritised, enriched list of media targets (podcasts, journalists,
publications) from a research brief. Output is a research artifact for **human
review only** — this skill does NOT draft or send outreach.

---

## When to Invoke

Invoke this skill when the user says things like:

- "Find me podcasts about X"
- "Who covers Y at major tech publications?"
- "I need a media list for our launch around Z"
- "Which journalists write about [topic]?"
- "Build me a press list for [company/product/topic]"
- "What podcasts should we pitch for [topic]?"

---

## Eliciting a Complete Brief

If the user provides only a partial request (e.g., "find me AI podcasts"), ask
for missing fields using the ask_user_input tool before running. Required fields:

| Field | Question to ask |
|-------|-----------------|
| `target_type` | "Are you looking for podcasts, journalists, publications, or a mix of all three?" |
| `topic` | "What topic or beat are you researching? (Be specific, e.g. 'AI infrastructure, developer tools, Postgres')" |
| `num_results` | "How many targets would you like? (default: 20)" |
| `depth` | "How deep should the personalization go? `light` = contact info only, `medium` = recent work summaries, `deep` = tailored pitch angles for each target" |

Optional fields (ask only if likely relevant):

| Field | Question |
|-------|----------|
| `recency_days` | "How recently should targets have been active? (default: last 90 days)" |
| `audience_constraints` | "Any audience size requirements? (e.g. min 10k monthly downloads)" |
| `geo_filter` | "Any geographic or language filters? (e.g. US only, English-language)" |

### Minimum viable brief
If the user is in a hurry, you can run with just `target_type`, `topic`, and
defaults for everything else. Mention that defaults are being used.

---

## Running the Skill

Once you have a brief, run:

```bash
media-researcher run --brief brief.yaml --format markdown --depth deep
```

Or interactively:

```bash
media-researcher run --interactive
```

The tool writes the report to `/mnt/user-data/outputs/` and prints a preview
of the top 5 targets to the terminal.

---

## Presenting Results

1. **Lead with the top 5.** Summarise each in 1-2 sentences: name, outlet,
   why they're a fit, their score.
2. **Mention the full report path** so the user can open it.
3. **Summarise the rest** briefly: "The remaining 15 targets cover [range of
   outlets/beats]; see the full report for details."
4. If limitations were logged (missing API keys, degraded sources), surface
   them clearly with suggestions for improvement.

Example summary format:

```
Here are the top 5 targets for your "[topic]" brief:

1. **[Name]** — [Outlet] ([type], score: 0.82)
   Covers [beat]. Recent episode/article: "[title]". [Pitch angle if deep.]

2. …

Full report: /mnt/user-data/outputs/media-research_20260424_120000.md
+ 15 more targets in the report.

⚠️ Limitations: LISTEN_NOTES_API_KEY not set — podcast results unavailable.
```

---

## Follow-up: "Expand on target #3"

If the user asks to expand on a specific target, re-run enrichment at `deep`
depth for that single target:

```python
from media_researcher_core import run_research
from media_researcher_core.models import PersonalizationDepth

# Modify the brief: 1 result, deep depth
brief.num_results = 1
brief.depth = PersonalizationDepth.DEEP
# Run with topic narrowed to the specific target name if helpful
```

Then present the expanded pitch angle, all recent work found, and contact
details in full.

---

## Hard Rules

> **This skill produces research only.** The agent MUST NOT:
> - Draft outreach emails to anyone in the output list.
> - Send messages on the user's behalf.
> - Store contact data beyond the current session unless the user explicitly
>   opts into a persistent contact database.
>
> If the user asks to "send this to all of them" or "draft an email to #3",
> redirect them: *"This skill is for research only. To draft or send outreach,
> please invoke the outreach skill explicitly."*

---

## Data Quality Notes

- **Emails:** Only publicly listed emails from a target's own website or public
  bio are included. Guessed or pattern-derived emails are never returned.
- **Audience figures:** Approximate; sourced from APIs or public estimates.
  Treat as directional, not exact.
- **Recency:** Based on API data; very new content may not yet be indexed.
- **Cache:** Enrichment results are cached for 7 days. Pass `--no-cache` (not
  yet implemented) or clear `~/.cache/media-researcher/` to force a refresh.
