# AGENTS.md

Operating instructions for an AI agent or assistant reading, installing, or running this repository, `milestone-loc-tokens-ai-ledger`, on someone's behalf. This file is not a summary of the project, see `README.md` for that. This file is a protocol.

## Scope

This applies whenever you are asked to install this skill, run its engine, or generate a dashboard from it, and especially whenever your next action would write a word, line, or token count, or a price, without a source that was actually read.

## The non-negotiable rule

No number enters the ledger without a source that was actually counted. No price entry is accepted with zero sources. No string reaches a published file without passing through `engine/scrub.py` first.

## Red flags

"The token count is probably close enough." "I can estimate the price for a model not in the ledger." "This local path in the commit message is harmless, it can stay." "The Haiku tokens can be priced at the Sonnet rate for now." "1-hour cache writes are close enough to 5-minute ones." "The older milestones' cache writes were probably all 1-hour, I'll just assume the split." "I'll delete data.json so the costs recompute." None of these is this skill's practice. Each defeats the reason it exists.

## Installing this skill

This project's own `README.md` carries the installation matrix, verified against each vendor's own documentation at the date stated there. Before trusting that matrix as current, or before trusting `engine/prices.json` as reflecting today's price, check the date next to each and reconfirm at the source if it looks old. The matrix is a snapshot, not live state, exactly the discipline `harness-medir`'s own `AGENTS.md` already applies to its curation, the project this skill originated from.

## Running it on someone's behalf

Run `python engine/generate_metrics.py --repo <path>`. Read `SKILL.md` in full first, it is the operating instructions, not this file. Never invent a `notes.json` entry on the user's behalf: a decision note is either something the user actually said, or it stays absent.

A transcript with several models, or with 1-hour cache writes, needs one sourced price series per model id in `engine/prices.json`; a model without one is reported unpriced, and you add the series (read the vendor's pricing page yourself, use `engine/update_prices.py`), you do not borrow another model's price. A price found to be **wrong** is corrected by appending an entry with `--corrects` and then running `generate_metrics.py --reprice`; both procedures are in `SKILL.md` ("Multiple models and cache TTLs", "Correcting a price that was wrong"). `--reprice` is for a wrong price, never for a normal price change. A milestone frozen before the per-model and per-TTL split has no such split in its tokens, and `--reprice` cannot invent it: `python engine/generate_metrics.py --repo <path> --enrich --dry-run` re-derives it from the transcripts, and only where they prove it (the four original counters must match exactly). Read the dry-run report first, let its refusals stand, and follow `SKILL.md` ("Enriching milestones recorded before a schema change"), including its deadline: the transcripts are deleted after `cleanupPeriodDays`. A project that prices everything with one cost basis of its own (the shipped `custom` / `on-premise` series) needs `"price_mode": "flat"` in its config; that is the user's explicit choice, so ask rather than switch it on because the provider is `custom`.

## Never

- Never present the price ledger or the installation matrix as live state. Both carry a verification date.
- Never write a note to `notes.json` that the user did not actually provide.
- Never claim a transcript reader works for an environment `engine/readers/` does not actually contain a module for.
- Never let the published `dashboard.html` or `dashboard.js` make a network call.
- Never edit or delete an entry in `engine/prices.json`. A changed price is a new entry with a later date; a wrong price is a new entry with `--corrects` and the same date.
- Never price a model's tokens with another model's series, or a 1-hour cache write at the 5-minute rate. Report them unpriced instead.
- Never run `--reprice` for a normal price change, and never delete `data.json` to unfreeze costs: that also discards frozen tokens whose transcripts may be gone. When it is the token schema that changed, not the price, the route is `--enrich`.
- Never run `--enrich` for real before reading its `--dry-run` report, never assume a cache TTL or a model for a milestone it refused, and never edit a frozen counter to make one pass. A refusal means the transcripts do not prove the split: leave the milestone as it is, or fix the cause (a `--linked-project` that was not repeated, a transcript that still exists) and run again.
- Never pass `--linked-project` because two projects were active the same day. Open the candidate subagent transcript yourself and confirm it names this project's own path before trusting it; `engine/readers/claude_code.find_linked_subagent_jsonl` already enforces this at the code level, but do not treat that as a reason to skip checking the actual evidence yourself when a user asks you to invoke it.

## Honest limits

This file cannot make a tool without a real Claude Code transcript produce a non-zero token count. What it can do is make the absence visible: reporting zero tokens without saying why is the failure this protocol treats as unacceptable, not the absence of a reader itself.

## Orienting yourself in this repository

| File | What it is | Read it before |
|---|---|---|
| `README.md` | What this skill delivers, installation matrix, honest limits | Deciding whether to install it |
| `SKILL.md` | The skill itself, the non-negotiable rule, how to run it | Running the engine |
| `engine/config.example.json` | The config shape a bootstrap run copies | Editing a project's own config.json |
| `engine/prices.json` | The shipped, dated, multi-source price ledger | Trusting any cost figure this skill produces |
| `logbook/` | This repository's own generated dashboard | Wanting proof this works, not just a description |

`llms.txt`, at the root of this repository, indexes the same map for an agent that only fetched a URL and needs to find this file first.

---

Last updated 20 September 2026.
