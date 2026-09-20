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

**A self-hosted dashboard** (`<milestone_folder>/dashboard.html`), regenerated on every run: four growth charts (words, lines, tokens, cost), a milestone table, a sources-consulted panel, and a live price panel — dropdown, then a number field and a slider for each of five prices (input, output, cache read, cache write 5 min, cache write 1 h), kept in sync — that recomputes cost, the cost chart, and every milestone's live-cost cell against any provider, model, or historical price already in the ledger, entirely client-side, with no network call from the published page ever. The panel reprices all tokens at the one price selected; a 1-hour cache write is priced at the 1-hour field, and when the selected entry has none, a milestone with 1-hour writes shows "-" rather than borrowing the 5-minute rate.

**A frozen historical cost per milestone.** Computed once, at the price in effect on that milestone's date, and never recalculated when the ledger later gains a newer entry. The past stays recorded at the moment it happened. Each model's tokens are priced with that model's own ledger series, and 5-minute and 1-hour cache writes at their own prices (see "Multiple models and cache TTLs"); whatever no ledger price covers is listed as unpriced, never guessed.

**An append-only price ledger** (`engine/prices.json`), dated and multi-sourced. A changed price becomes a new entry, never an edit to the one it replaces; a price that was recorded *wrong* is fixed by appending a `corrects` entry and then running `--reprice` (see "Correcting a price that was wrong").

## How to run it

```
python engine/generate_metrics.py --repo /path/to/your/project
```

First run bootstraps `<milestone_folder>/config.json` from `engine/config.example.json` if the folder does not already exist. Edit that file to declare which globs count as content (words) and which count as code (lines), which price series (`provider`/`model`) prices this project's token usage, and, only if you need it, `price_mode` (see "Multiple models and cache TTLs"; the default is `per_model`, and a config without the key behaves that way).

Only the Claude Code transcript reader ships complete. Running this against a project driven from another environment still counts words and lines from git history; token counts stay zero until a reader for that environment's transcript format is contributed, see `engine/readers/`.

A subagent dispatched with the Task tool gets its own transcript, nested under its parent session's own directory, not a top-level file: the engine finds these on its own for the project's own session history, no flag needed.

## When this project's own build history lives in another project's transcripts

A project can be built by subagents dispatched from a different top-level session, one whose own working directory is a different repository entirely (a common shape for a spec-driven, subagent-per-task build run from a sibling planning repo). When that happens, the subagents' transcripts are filed under that other project's own path, never this one's, and the ordinary lookup above correctly finds nothing.

```
python engine/generate_metrics.py --repo /path/to/your/project \
  --linked-project /path/to/the/other/repository
```

This never widens project isolation into a guess. It only ever reads a subagent transcript nested under the named other project, and only the ones whose own transcript content literally contains this project's own absolute path, proof the subagent was actually pointed at these files, not a same-day coincidence. It never reads the other project's own top-level session file: that file mixes together a whole session's unrelated work in that other project and cannot be safely scoped to just this project's share of it. Repeat `--linked-project` for more than one other project. See `engine/readers/claude_code.find_linked_subagent_jsonl` for the exact rule.

A milestone's recorded cost and the tokens it was computed from both freeze the first time they are written (see below), so a later run that omits a `--linked-project` flag given on an earlier run still shows the same historical numbers for milestones already priced; only a not-yet-priced milestone depends on the flag being repeated.

## Recording a decision alongside a milestone

Add a line to `<milestone_folder>/notes.json`, keyed by the commit's short hash:

```json
{"a1b2c3d": "Chose to ship without a team-aggregation feature, out of scope for v1."}
```

## Updating the price ledger

```
python engine/update_prices.py --provider <provider> --model <exact-model-id> \
  --currency USD --effective-date <YYYY-MM-DD> \
  --input-price <US$/MTok> --output-price <US$/MTok> --cache-read-price <US$/MTok> \
  --cache-creation-price <US$/MTok, 5-minute cache write> \
  --cache-creation-1h-price <US$/MTok, 1-hour cache write> \
  --source-name "<a page you read>" --source-url "<its URL>" \
  --source-name "<a second, independent page you read>" --source-url "<its URL>" \
  --checked-at <the date you read them>
```

Every placeholder above is a number or page you read that day, never one you remembered. `--cache-creation-1h-price` is optional, but without it 1-hour cache writes under that entry are reported unpriced (see below). `--model` is the exact model id a transcript records (`claude-sonnet-5`, not "Sonnet"). Never edits an existing entry. Always appends.

An entry carries these fields:

| Field | Meaning |
|---|---|
| `effective_date`, `input_price`, `output_price`, `cache_read_price`, `cache_creation_price`, `sources` | As before. `cache_creation_price` is the **5-minute** cache write. Prices are US$ per million tokens. |
| `cache_creation_1h_price` | Optional. The **1-hour** cache write price. An entry without it stays valid. |
| `corrects` | Optional. The `effective_date` of an earlier entry in the same series that recorded a **wrong** price (not a changed one). It must equal this entry's own `effective_date`. |
| `note` | Optional. What was wrong, or a caveat about the sources. Label an inferred cause as "probable". |

Of two entries with the same `effective_date`, the one appended later wins (`price_at`), which is how a correcting entry takes over from the wrong one it leaves in the file as a record.

## Multiple models and cache TTLs

A Claude Code transcript can mix models (a main session on one model, subagents on others), and its cache writes can use either TTL. The reader records both per usage event: the model id (`message.model`) and how many cache-write tokens were 1-hour (`usage.cache_creation.ephemeral_1h_input_tokens`). Each milestone's `tokens` in `data.json` then carries the original four counters, `cache_creation_1h` (the 1-hour part of `cache_creation`), and `by_model`, the same tokens split by model id.

The cost prices each model's tokens with **that model's own series** under the configured `price_provider` (exact model id, the entry in effect on the milestone's date), 5-minute writes at `cache_creation_price` and 1-hour writes at `cache_creation_1h_price`. Nothing is priced at another model's or another TTL's rate. Whatever cannot be priced is listed, with the reason, in the milestone's `unpriced` list in `data.json`: a model with no series, a model whose series starts after the milestone's date, 1-hour writes under an entry with no `cache_creation_1h_price`, and usage rows that name no model. The dashboard marks such a milestone "(partial)" (its recorded cost is then what could be priced, a floor) and lists every gap under "Unpriced usage", and the "Unpriced milestones" KPI counts it.

**What to do when you identify this kind of usage** (a transcript with several models, or 1-hour cache writes, or an "Unpriced usage" list on the dashboard):

1. Read which model ids are involved: the keys of `by_model` in `data.json`, or the "Unpriced usage" list.
2. For each model that has no series, read the vendor's own pricing page and models page for that exact model id (a second source such as litellm is recommended), then add the series with `engine/update_prices.py`, including `--cache-creation-1h-price`. Use the date the price applies from as `--effective-date`; if the sources show only today's price and no dated history, say so in `--note` rather than implying more.
3. Run `python engine/generate_metrics.py --repo <path> --reprice` to fill the gap in milestones already frozen (a normal run leaves a recorded milestone as it is). Milestones that have not been recorded yet pick the new series up on the next normal run.
4. Never copy another model's price to make an unpriced line go away.

Milestones frozen by an older version of the engine have no `by_model` and no `cache_creation_1h`. They keep being priced entirely with the one configured series, all cache writes as 5-minute writes, and so the same goes for a milestone whose transcript rows name no model. That is the honest limit of data recorded before the split existed: do not present those milestones' cache-write cost as exact: where the session really wrote its cache with the 1-hour TTL, the recorded figure is lower than what was charged.

**A project with its own single cost basis** (electricity and hardware amortisation for a local model, say, priced with the shipped `custom` / `on-premise` series) sets `"price_mode": "flat"` in `logbook/config.json`. Under `flat`, every token of a milestone is priced with the one series named by `price_provider` / `price_model`, whatever model wrote it, and nothing is reported unpriced for lack of a per-model series. It still splits 5-minute from 1-hour cache writes with that series' `cache_creation_1h_price`, and a milestone with 1-hour writes under an entry that has no such price still lists those writes as unpriced. `flat` is never inferred from the provider: `custom` needs it set explicitly, and without it (the default `per_model`) each model id is looked up under `price_provider` and a model with no series there is unpriced. The value is validated; anything other than `"per_model"` or `"flat"` stops the run with an error. A normal run and `--reprice` honour it identically, so after switching an existing project to `flat`, run `--reprice` to re-cost milestones already recorded (a normal run leaves them frozen). The live price panel reprices every token at any one selected series, whatever the mode.

## Correcting a price that was wrong

A price that **changed** and a price that was recorded **wrong** are different, and only the second may touch the past.

1. Re-read the sources and confirm the right number.
2. Append a correcting entry: the **same** `effective_date` as the wrong entry, `--corrects <that date>`, a `--note` saying what was wrong (label an inferred cause "probable"), and the sources. The wrong entry stays in the file as a record.

```
python engine/update_prices.py --provider <provider> --model <exact-model-id> \
  --effective-date <the wrong entry's date> --corrects <the wrong entry's date> \
  --note "<what was wrong; probable cause, if inferred>" \
  --input-price ... --output-price ... --cache-read-price ... \
  --cache-creation-price ... --cache-creation-1h-price ... \
  --source-name ... --source-url ... --checked-at <date read>
```

3. Run `python engine/generate_metrics.py --repo <path> --reprice`.

`--reprice` recomputes **only the cost** of milestones already frozen in `data.json`, from their already-frozen tokens, against the ledger as it is now. It never reads git history or a transcript, never touches tokens, words, lines or notes, and never runs with `--linked-project`. Every milestone whose cost changes gets an entry in its `repricings` list holding the moment and the cost (and unpriced list) it had before, so the old number is never lost; `data.json` gains `last_repriced_at`, and `generated_at` keeps saying when the milestones were last read from git. It rewrites the dashboard and thumbnails from the corrected numbers. Running it again with nothing new in the ledger changes nothing.

**Use it** after appending a `corrects` entry, or after adding a series for a model that had none, to fill a gap in milestones already recorded.

**Do not use it** for a normal price change. A new entry with a later `effective_date` already leaves every earlier milestone at the price that was true then (it would change nothing anyway), and to see what the past would cost today the live price panel does that without touching a record. Do not use it to make a number match a hunch either.

**Do not delete `data.json` to "unfreeze" costs.** Besides discarding the audit trail, it discards every frozen token count, and a transcript that gave those tokens may no longer exist (a subagent transcript filed under another project, a cleaned-up session), so they cannot be regenerated. `--reprice` exists so that nobody has to.

## Never

- Never estimate a word, line, or token count. Read it from git history or a real transcript, or leave it at zero.
- Never let a local file path, username, or hostname reach a published file.
- Never recalculate a milestone's recorded cost after the fact, even when the ledger gains a newer price. The one exception is `--reprice`, the explicit, audited correction of a price that was **wrong** (see "Correcting a price that was wrong"), never a route for a normal price change.
- Never overwrite an existing price ledger entry. Append a new one; a wrong price is corrected by appending an entry with `--corrects`.
- Never delete `data.json` to unfreeze costs: it also discards the frozen tokens, which may not be regenerable.
- Never price a model's tokens with another model's series, or a 1-hour cache write at the 5-minute rate. A model or a TTL with no sourced price is reported unpriced.
- Never claim a transcript reader exists for an environment this repository has not actually tested.
- Never call out to the network from `dashboard.html` or `dashboard.js`.
- Never pass `--linked-project` on a same-day-timing hunch. Confirm, transcript by transcript, that its content actually names this project's own path before trusting what it backfills.

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
