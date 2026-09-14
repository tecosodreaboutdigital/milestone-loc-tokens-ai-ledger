---
name: milestone-loc-tokens-ai-ledger
description: Generates a self-hosted, static HTML dashboard tracking a project's lines of code, words, and LLM token cost per git milestone, with a dated, multi-source, editable price ledger. Use when someone wants a public, self-updating record of how much a project cost to build in tokens and money, per milestone, without a backend.
---

*Part of the [Harness series and the MEDIR cycle](https://github.com/tecosodreaboutdigital/harness-medir): Map, Equip, Delegate, Inspect, Reinforce. This skill generalises harness-medir's own diary-of-record engine into a reusable, installable form.*

# Milestone LOC Tokens AI Ledger

This skill does not track a team's velocity or a project's roadmap. It answers a narrower question, at the granularity of one git commit: how many words or lines changed, how many LLM tokens that took, and what that cost, at the price that was true on that day.

## Non-negotiable rule

**No number enters the ledger without a source that was actually counted, and no detail about the machine that produced it ever leaves the local repository.**

Every word, line, and token count comes from reading git history and a real session transcript, never an estimate. Every string written to the published dashboard is scrubbed of local file paths, the operating system username, and the hostname before it is written. A price entry with fewer than two independent sources is accepted but flagged; a price entry with zero sources is refused.

## What it delivers

**A self-hosted dashboard** (`<milestone_folder>/dashboard.html`), regenerated on every run: two growth charts (words, tokens), a milestone table, and a live price panel that recomputes cost against any provider, model, or historical price already in the ledger, entirely client-side, with no network call from the published page ever.

**A frozen historical cost per milestone.** Computed once, at the price in effect on that milestone's date, and never recalculated when the ledger later gains a newer entry. The past stays recorded at the moment it happened.

**An append-only price ledger** (`engine/prices.json`), dated and multi-sourced. A changed price becomes a new entry, never an edit to the one it replaces.

## How to run it

```
python engine/generate_metrics.py --repo /path/to/your/project
```

First run bootstraps `<milestone_folder>/config.json` from `engine/config.example.json` if the folder does not already exist. Edit that file to declare which globs count as content (words) and which count as code (lines), and which price series (`provider`/`model`) prices this project's token usage.

Only the Claude Code transcript reader ships complete. Running this against a project driven from another environment still counts words and lines from git history; token counts stay zero until a reader for that environment's transcript format is contributed, see `engine/readers/`.

## Recording a decision alongside a milestone

Add a line to `<milestone_folder>/notes.json`, keyed by the commit's short hash:

```json
{"a1b2c3d": "Chose to ship without a team-aggregation feature, out of scope for v1."}
```

## Updating the price ledger

```
python engine/update_prices.py --provider anthropic --model claude-sonnet-5 \
  --currency USD --effective-date 2026-10-01 \
  --input-price 3.00 --output-price 15.00 --cache-read-price 0.30 --cache-creation-price 3.75 \
  --source-name "Anthropic pricing page" --source-url "https://www.anthropic.com/pricing" \
  --source-name "litellm" --source-url "https://github.com/BerriAI/litellm" \
  --checked-at 2026-10-01
```

Never edits an existing entry. Always appends.

## Never

- Never estimate a word, line, or token count. Read it from git history or a real transcript, or leave it at zero.
- Never let a local file path, username, or hostname reach a published file.
- Never recalculate a milestone's recorded cost after the fact, even when the ledger gains a newer price.
- Never overwrite an existing price ledger entry. Append a new one.
- Never claim a transcript reader exists for an environment this repository has not actually tested.
- Never call out to the network from `dashboard.html` or `dashboard.js`.

## Files in this skill

```
milestone-loc-tokens-ai-ledger/
├── SKILL.md                  this file
├── AGENTS.md                 operating protocol for an AI agent installing or running this skill
├── llms.txt                  discovery index for an agent that only fetched a URL
├── README.md                 project overview and the installation matrix
├── .claude-plugin/           plugin.json and marketplace.json, for installing this as a Claude Code plugin
├── engine/                   the Python engine, standard library only
├── template/                 the dashboard skeleton and its price panel
└── logbook/                  this repository's own generated dashboard, see README
```

## Origin

Part of the Harness series and the MEDIR playbook, the entry point of the Inspect step generalised into its own skill.

MIT licence.
