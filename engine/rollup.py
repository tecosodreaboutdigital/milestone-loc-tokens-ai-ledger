"""Totals over the per-commit ledger: one row per day, and one row per
named milestone (a range of commits declared in config.json under
`milestones`). Derived on every run from the commits' own numbers, never
frozen: the per-commit record stays the fact.

Each commit carries two costs. `cost_recorded` is frozen at the price in
effect on its own date (see generate_metrics). `cost_at_today` is the same
tokens priced at the ledger entry in effect on the day of the run, so the
whole history can be read in today's dollars; it is recomputed every run
and never replaces the recorded cost."""

from engine.cost import total_tokens

IN_PROGRESS = "In progress"


def _amount(cost):
    return None if cost is None else cost["amount"]


def _sum_costs(values):
    # None means nothing was priced; a group where nothing was priced
    # shows no cost rather than a misleading $0.00.
    priced = [v for v in values if v is not None]
    return sum(priced) if priced else None


def _partial(m, cost_key, unpriced_key):
    # Partial when the commit had tokens no price covered, or tokens with
    # no cost at all.
    return bool(m.get(unpriced_key) or (m.get(cost_key) is None and total_tokens(m["tokens"])))


def _totals(commits):
    return {
        "commits": len(commits),
        "words_delta": sum(m["words_delta"] for m in commits),
        "loc_delta": sum(m["loc_delta"] for m in commits),
        "tokens": sum(total_tokens(m["tokens"]) for m in commits),
        "cost_recorded": _sum_costs(_amount(m.get("cost_recorded")) for m in commits),
        "cost_at_today": _sum_costs(_amount(m.get("cost_at_today")) for m in commits),
        # Each cost has its own: a model unpriced on a commit's date may be
        # priced today, so the recorded flag says nothing about today's.
        "partial": any(_partial(m, "cost_recorded", "unpriced") for m in commits),
        "partial_at_today": any(_partial(m, "cost_at_today", "unpriced_at_today") for m in commits),
    }


def group_by_day(milestones, day_of=None):
    """One row per commit date, in commit order. `day_of(m)` gives the
    date a commit counts under (see config.day_timezone); by default it
    is the commit's own recorded `date`."""
    days = {}
    for m in milestones:
        days.setdefault(day_of(m) if day_of else m["date"], []).append(m)
    return [{"date": date, **_totals(commits)} for date, commits in days.items()]


def group_by_named_milestone(milestones, named):
    """Each named milestone covers the commits after the previous one's
    `until` up to and including its own. Commits after the last one form
    an "In progress" group. An `until` that is not a commit of this
    history, or one that comes before the previous one, raises
    ValueError: a milestone is never silently dropped or reordered."""
    if not named:
        return []
    position = {m["commit"]: i for i, m in enumerate(milestones)}
    groups, start = [], 0
    for entry in named:
        until = entry["until"][:7]
        if until not in position:
            raise ValueError("milestone %r: commit %s is not in this history" % (entry["name"], until))
        end = position[until]
        if end < start:
            raise ValueError("milestone %r: commit %s comes before the previous milestone" % (entry["name"], until))
        groups.append((entry["name"], milestones[start:end + 1]))
        start = end + 1
    if start < len(milestones):
        groups.append((IN_PROGRESS, milestones[start:]))
    return [
        {
            "name": name,
            "from": commits[0]["commit"],
            "until": commits[-1]["commit"],
            "first_date": commits[0]["date"],
            "last_date": commits[-1]["date"],
            **_totals(commits),
        }
        for name, commits in groups
    ]
