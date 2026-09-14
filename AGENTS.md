# AGENTS.md

Operating instructions for an AI agent or assistant reading, installing, or running this repository, `milestone-loc-tokens-ai-ledger`, on someone's behalf. This file is not a summary of the project, see `README.md` for that. This file is a protocol.

## Scope

This applies whenever you are asked to install this skill, run its engine, or generate a dashboard from it, and especially whenever your next action would write a word, line, or token count, or a price, without a source that was actually read.

## The non-negotiable rule

No number enters the ledger without a source that was actually counted. No price entry is accepted with zero sources. No string reaches a published file without passing through `engine/scrub.py` first.

## Red flags

"The token count is probably close enough." "I can estimate the price for a model not in the ledger." "This local path in the commit message is harmless, it can stay." None of these three is this skill's practice. All three defeat the reason it exists.

## Installing this skill

This project's own `README.md` carries the installation matrix, verified against each vendor's own documentation at the date stated there. Before trusting that matrix as current, or before trusting `engine/prices.json` as reflecting today's price, check the date next to each and reconfirm at the source if it looks old. The matrix is a snapshot, not live state, exactly the discipline `harness-medir`'s own `AGENTS.md` already applies to its curation, the project this skill originated from.

## Running it on someone's behalf

Run `python engine/generate_metrics.py --repo <path>`. Read `SKILL.md` in full first, it is the operating instructions, not this file. Never invent a `notes.json` entry on the user's behalf: a decision note is either something the user actually said, or it stays absent.

## Never

- Never present the price ledger or the installation matrix as live state. Both carry a verification date.
- Never write a note to `notes.json` that the user did not actually provide.
- Never claim a transcript reader works for an environment `engine/readers/` does not actually contain a module for.
- Never let the published `dashboard.html` or `dashboard.js` make a network call.

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

Last updated 13 September 2026.
