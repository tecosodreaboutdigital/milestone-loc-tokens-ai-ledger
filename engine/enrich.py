"""Planning a one-time migration: bringing a milestone that was frozen
before the per-model / per-TTL token split up to the current token
schema, from the transcripts, and only where they demonstrably agree
with what was frozen.

Nothing here reads git or a transcript and nothing here writes a file.
The caller re-derives each commit's token bucket (see
generate_metrics.read_token_buckets) and passes it in, together with a
`price(tokens, date) -> (cost_recorded, unpriced)` function; this module
decides, per milestone, whether the re-derived bucket may be trusted and
what the enriched tokens and cost would then be.

The safety rule: a milestone is enriched only if the four original
counters of the re-derived bucket equal the frozen ones exactly. A
transcript that was deleted, a --linked-project that was not repeated, a
rewritten history or a reader that changed all show up as a difference
there, and the milestone is refused, never given a number that was
guessed."""

import copy

from engine.cost import TOKEN_KINDS, total_tokens


def needs_enrichment(milestone):
    """The one place that says which frozen milestones a migration may
    touch: those with a recorded cost and real tokens that were frozen
    without the per-model split. A future schema change edits this
    predicate, not the flow around it."""
    tokens = milestone["tokens"]
    return (
        milestone.get("cost_recorded") is not None
        and "by_model" not in tokens
        and total_tokens(tokens) > 0
    )


def _amount(cost):
    return cost["amount"] if cost is not None else 0.0


def _refused(commit, reason, **extra):
    return {"commit": commit, "status": "refused", "reason": reason, **extra}


def _enriched(milestone, bucket, price):
    frozen = milestone["tokens"]
    date = milestone["date"]
    old_cost = milestone["cost_recorded"]
    old_unpriced = milestone.get("unpriced") or []

    tokens = {kind: frozen.get(kind, 0) for kind in TOKEN_KINDS}
    one_hour_only = {**tokens, "cache_creation_1h": bucket["cache_creation_1h"]}
    tokens["cache_creation_1h"] = bucket["cache_creation_1h"]
    tokens["by_model"] = copy.deepcopy(bucket["by_model"])

    # The cost change, split by cause in a fixed order: first the 1-hour
    # cache writes priced with the one configured series, then the model
    # mix on top of that. Both are measured against the recorded cost.
    one_hour_cost, _ = price(one_hour_only, date)
    new_cost, new_unpriced = price(tokens, date)
    # Whether the recorded cost still is what the current ledger gives
    # the frozen tokens: if not, part of the change is a pending price
    # correction (--reprice), not the split.
    now_cost, now_unpriced = price(frozen, date)

    return {
        "commit": milestone["commit"],
        "status": "enrich",
        "tokens": tokens,
        "cost_recorded": new_cost,
        "unpriced": new_unpriced,
        "previous_amount": old_cost["amount"],
        "delta_one_hour": _amount(one_hour_cost) - old_cost["amount"],
        "delta_model": _amount(new_cost) - _amount(one_hour_cost),
        "ledger_drift": now_cost != old_cost or now_unpriced != old_unpriced,
        "becomes_partial": bool(new_unpriced) and not old_unpriced,
    }


def plan_enrichment(milestones, buckets_by_commit, price):
    """One entry per candidate milestone (see needs_enrichment), in order;
    everything else is left out of the plan. An entry is either
    status "enrich" (with the enriched `tokens`, the new `cost_recorded`
    and `unpriced`, `previous_amount`, the two deltas, `ledger_drift` and
    `becomes_partial`) or status "refused" with a `reason`:
    "commit_not_in_history" or "counters_differ" (with `differences`,
    {counter: (frozen, re-derived)}). Changes none of its arguments."""
    plan = []
    for milestone in milestones:
        if not needs_enrichment(milestone):
            continue
        commit = milestone["commit"]
        bucket = buckets_by_commit.get(commit)
        if bucket is None:
            plan.append(_refused(commit, "commit_not_in_history"))
            continue
        frozen = milestone["tokens"]
        differences = {
            kind: (frozen.get(kind, 0), bucket[kind])
            for kind in TOKEN_KINDS
            if frozen.get(kind, 0) != bucket[kind]
        }
        if differences:
            plan.append(_refused(commit, "counters_differ", differences=differences))
            continue
        plan.append(_enriched(milestone, bucket, price))
    return plan


def apply_enrichment(milestones, plan, enriched_at):
    """The explicit, audited exception to "a recorded cost never changes",
    for a milestone frozen before the token schema gained a dimension.
    Applies every "enrich" entry of `plan` to its milestone, in place:
    the tokens gain the split, cost_recorded and unpriced are re-priced,
    and one record is appended to the milestone's `repricings` list with
    the cost (and unpriced list) it had before and reason "enrich". The
    record is appended even when the cost did not change, because the
    tokens did. The four original counters, words, lines, subject, note
    and every milestone the plan refused are never touched. Returns how
    many milestones were enriched; applying the same plan's inputs again
    finds none left."""
    by_commit = {r["commit"]: r for r in plan if r["status"] == "enrich"}
    enriched = 0
    for milestone in milestones:
        result = by_commit.get(milestone["commit"])
        if result is None:
            continue
        record = {"repriced_at": enriched_at, "previous_cost": milestone["cost_recorded"]}
        if milestone.get("unpriced"):
            record["previous_unpriced"] = milestone["unpriced"]
        record["reason"] = "enrich"
        milestone.setdefault("repricings", []).append(record)
        milestone["tokens"] = result["tokens"]
        milestone["cost_recorded"] = result["cost_recorded"]
        milestone.pop("unpriced", None)
        if result["unpriced"]:
            milestone["unpriced"] = result["unpriced"]
        enriched += 1
    return enriched


def _why_refused(result):
    if result["reason"] == "commit_not_in_history":
        return "commit not found in this repository's git history (rewritten or rebased?)"
    return "; ".join(
        "%s frozen %s, re-derived %s" % (counter, "{:,}".format(frozen), "{:,}".format(rederived))
        for counter, (frozen, rederived) in result["differences"].items()
    )


def format_report(plan, total_milestones, currency, transcript_files, usage_events, dry_run=True):
    """The report of an --enrich run: what it does, and what it refuses
    and why. A dry run says plainly that nothing was written and speaks
    in the conditional; a real run says what was done."""
    enriched = [r for r in plan if r["status"] == "enrich"]
    refused = [r for r in plan if r["status"] == "refused"]
    if dry_run:
        header = "enrich (dry run): nothing was written"
        done, cost_subject, partial_verb = "would enrich", "would be enriched", "would become"
    else:
        header = "enrich: milestones were enriched in place; previous costs are kept in each milestone's repricings"
        done, cost_subject, partial_verb = "enriched", "were enriched", "became"
    lines = [
        header,
        "read %d transcript files, %d usage events" % (transcript_files, usage_events),
        "%d of %d milestones were recorded before the per-model split" % (len(plan), total_milestones),
        "%s: %d" % (done, len(enriched)),
        "refused: %d" % len(refused),
    ]
    lines.extend("  %s: %s" % (r["commit"], _why_refused(r)) for r in refused)
    if enriched:
        before = sum(r["previous_amount"] for r in enriched)
        after = sum(_amount(r["cost_recorded"]) for r in enriched)
        partial = [r for r in enriched if r["becomes_partial"]]
        lines.extend([
            "recorded cost of the milestones that %s: %.4f -> %.4f %s (%+.4f)"
            % (cost_subject, before, after, currency, after - before),
            "  1-hour cache writes: %+.4f" % sum(r["delta_one_hour"] for r in enriched),
            "  model mix: %+.4f" % sum(r["delta_model"] for r in enriched),
            "%s partial (some tokens unpriced): %d" % (partial_verb, len(partial)),
        ])
        for r in partial:
            gaps = ", ".join(
                "%s (%s tokens)" % (u["model"] or "no model id", "{:,}".format(u["tokens"])) for u in r["unpriced"]
            )
            lines.append("  %s: %s" % (r["commit"], gaps))
        drifted = sum(1 for r in enriched if r["ledger_drift"])
        if drifted and dry_run:
            lines.append(
                "note: %d of these milestones already record a cost that differs from what the current "
                "ledger gives their frozen tokens; run --reprice first so the changes above show only "
                "the split" % drifted
            )
        elif drifted:
            lines.append(
                "note: %d of these milestones already recorded a cost that differed from what the current "
                "ledger gives their frozen tokens, so their change includes that price correction, "
                "not only the split" % drifted
            )
    return "\n".join(lines)
