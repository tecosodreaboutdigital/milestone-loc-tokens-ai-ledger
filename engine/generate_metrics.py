"""Orchestrates the whole engine: reads git history and, when
available, this machine's Claude Code transcript for this exact
project, prices each milestone against the ledger at the price in
effect on that date, scrubs every string field, and writes both
data.json and a fully rendered dashboard.html. Safe to run from any
of this skill's supported environments: the output does not depend on
which one called it, only on the repository's own git history and
session transcripts.

With --reprice it does something narrower and explicit instead: it
recomputes only the cost of milestones already frozen in data.json,
from their already-frozen tokens, against the ledger as it is now (see
reprice_milestones). It never reads git or a transcript in that mode.

With --enrich --dry-run it reports what a one-time migration would do for
milestones frozen before the per-model and per-TTL token split: the split
is re-derived from the transcripts and would apply only where the four
original counters match the frozen ones exactly (see engine/enrich.py).
It writes nothing."""

import argparse
import html
import itertools
import json
import os
import shutil
import sys
from datetime import datetime, timezone

# Allow this file to run as a direct script (python engine/generate_metrics.py)
# from any working directory, not only as a module (python -m
# engine.generate_metrics) or via python -m unittest. Direct script
# execution puts only this file's own directory on sys.path, so the
# repository root, and therefore the engine package itself, would
# otherwise not be importable.
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from engine.config import load_config, price_mode
from engine.cost import MODEL_TOKEN_FIELDS, TOKEN_KINDS, price_milestone, total_tokens
from engine.enrich import format_report, plan_enrichment
from engine.git_source import commits, line_count, list_repo_files_at, matches_any, sum_metric, word_count_html
from engine.prices import load_ledger
from engine.readers.claude_code import find_linked_subagent_jsonl, find_session_jsonl, load_usage_events
from engine.scrub import scrub_text
from engine.svg_chart import svg_growth_chart, svg_stat_thumbnail

HERE = os.path.dirname(os.path.abspath(__file__))
TEMPLATE_DIR = os.path.join(os.path.dirname(HERE), "template")
EXAMPLE_CONFIG = os.path.join(HERE, "config.example.json")
SHIPPED_PRICES = os.path.join(HERE, "prices.json")
# The exit code of --enrich when it refused at least one milestone.
EXIT_SOME_REFUSED = 3


def empty_tokens():
    """A fresh milestone token record: the original four counters, the
    1-hour cache write subset, and the same tokens split by model id."""
    tokens = {field: 0 for field in MODEL_TOKEN_FIELDS}
    tokens["by_model"] = {}
    return tokens


def bootstrap_if_missing(repo_root, folder_name):
    folder = os.path.join(repo_root, folder_name)
    if os.path.isdir(folder):
        return False
    os.makedirs(folder)
    shutil.copy(EXAMPLE_CONFIG, os.path.join(folder, "config.json"))
    return True


def load_notes(repo_root, folder_name):
    path = os.path.join(repo_root, folder_name, "notes.json")
    if not os.path.isfile(path):
        return {}
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def _bucket_tokens_by_commit(rows, events):
    ev_idx = 0
    buckets = []
    for row in rows:
        commit_dt = datetime.fromisoformat(row["iso"])
        bucket = empty_tokens()
        while ev_idx < len(events):
            event = events[ev_idx]
            e_dt = datetime.fromisoformat(event["ts"].replace("Z", "+00:00"))
            if e_dt <= commit_dt:
                for key in MODEL_TOKEN_FIELDS:
                    bucket[key] += event.get(key, 0)
                # Split by model id, but only events that name a model
                # and actually carry tokens: a row with no model stays
                # in the totals only (see cost.price_milestone), and a
                # zero-usage row (a synthetic message) adds no entry.
                model = event.get("model")
                if model and any(event.get(key, 0) for key in TOKEN_KINDS):
                    per_model = bucket["by_model"].setdefault(
                        scrub_text(model), {field: 0 for field in MODEL_TOKEN_FIELDS}
                    )
                    for key in MODEL_TOKEN_FIELDS:
                        per_model[key] += event.get(key, 0)
                ev_idx += 1
            else:
                break
        buckets.append(bucket)
    return buckets


def _effective_exclude_globs(config):
    # A project's own generated output (e.g. logbook/dashboard.html)
    # must never be counted as its own content, regardless of what the
    # config file's own exclude_globs happens to say. Always exclude
    # the configured milestone_folder itself, so renaming
    # milestone_folder never silently loses this self-exclusion just
    # because the config file was not updated to match.
    exclude_globs = list(config["exclude_globs"])
    milestone_prefix = config["milestone_folder"].rstrip("/") + "/"
    if milestone_prefix not in exclude_globs:
        exclude_globs.append(milestone_prefix)
    return exclude_globs


def read_token_buckets(repo_root, claude_projects_dir, linked_projects=None):
    """The git history and, per commit, the tokens this project's
    transcripts recorded up to it. Shared by a normal run and by
    --enrich, so the two can never bucket differently. Returns
    (rows, token_buckets, transcript_files, usage_events): the last two
    are how many transcript files and usage events were read."""
    rows = commits(repo_root)
    session_files = find_session_jsonl(claude_projects_dir, repo_root)
    # An explicit, named exception to project isolation: this project's
    # own build history sometimes lives in a different top-level
    # session's transcripts (see find_linked_subagent_jsonl's own
    # docstring). Never guessed, never on by default: a caller must
    # name the other project's path themselves, once, for that path to
    # ever be read.
    for linked_root in (linked_projects or []):
        session_files.extend(find_linked_subagent_jsonl(claude_projects_dir, linked_root, repo_root))
    events = load_usage_events(session_files)
    return rows, _bucket_tokens_by_commit(rows, events), len(session_files), len(events)


def build_milestones(repo_root, config, claude_projects_dir, linked_projects=None):
    rows, token_buckets, _, _ = read_token_buckets(repo_root, claude_projects_dir, linked_projects)
    notes = load_notes(repo_root, config["milestone_folder"])
    price_ledger = load_ledger(SHIPPED_PRICES)
    exclude_globs = _effective_exclude_globs(config)

    milestones = []
    prev_words = 0
    prev_loc = 0
    for row, tokens in zip(rows, token_buckets):
        files_at_commit = list_repo_files_at(repo_root, row["hash"])
        files_at_commit = {path for path in files_at_commit if not matches_any(path, exclude_globs)}
        # sum_metric measures the current cumulative total content
        # matching a glob AT this commit, not what changed since the
        # last one. Track the running total across commits so we can
        # store a genuine per-milestone delta below, not a repeated
        # cumulative number mislabeled as one.
        words = sum_metric(repo_root, row["hash"], files_at_commit, config["content_globs"], word_count_html)
        loc = sum_metric(repo_root, row["hash"], files_at_commit, config["code_globs"], line_count)
        words_delta = words - prev_words
        loc_delta = loc - prev_loc
        prev_words = words
        prev_loc = loc
        commit_date = row["iso"][:10]
        # A milestone with no recorded token usage (no transcript covers
        # it) gets no cost_recorded at all, not a misleading $0.00: a
        # price entry existing is not the same as this milestone having
        # anything to price. Tokens no price applies to are listed in
        # `unpriced`, with the reason, never priced at another rate.
        cost_recorded, unpriced = _price(tokens, price_ledger, config, commit_date)
        note = notes.get(row["hash"][:7])
        milestone = {
            "date": commit_date,
            "commit": row["hash"][:7],
            "subject": scrub_text(row["subject"]),
            "words_delta": words_delta,
            "loc_delta": loc_delta,
            "tokens": tokens,
            "cost_recorded": cost_recorded,
            "note": scrub_text(note) if note else None,
        }
        if unpriced:
            milestone["unpriced"] = unpriced
        milestones.append(milestone)
    return milestones


def _price(tokens, price_ledger, config, date):
    # One place prices a milestone, for a normal run and for --reprice
    # alike, so both honour the config's price_mode identically.
    cost_recorded, unpriced = price_milestone(
        tokens, price_ledger, config["price_provider"], config["price_model"], date, config["currency"],
        price_mode(config),
    )
    # Model ids come from a transcript, so they pass through the scrub
    # like every other string that reaches a published file.
    for item in unpriced:
        item["model"] = scrub_text(item["model"])
        item["reason"] = scrub_text(item["reason"])
    return cost_recorded, unpriced


def _freeze_previously_recorded_costs(milestones, data_path):
    # SKILL.md and the global constraints both promise that a
    # milestone's cost_recorded, once written, is never recalculated.
    # Without this, a same-day price correction appended later to the
    # live ledger could retroactively change a previously published
    # cost the next time the tie-break in price_at picks the new
    # entry. Any commit already present in a prior run's data.json
    # keeps that run's cost_recorded, no matter what the ledger says
    # now. Milestones with no prior recorded cost (new since the last
    # run, or previously null) keep their freshly computed value.
    #
    # The tokens a recorded cost was computed from freeze alongside it,
    # for the same reason: a discovered transcript is not guaranteed to
    # stay discoverable forever (a --linked-project path given on one
    # run might not be repeated on the next), and a milestone's cost
    # staying frozen while its own tokens quietly reverted to zero
    # would make the two numbers visibly disagree with each other on
    # the published page.
    if not os.path.isfile(data_path):
        return
    try:
        with open(data_path, encoding="utf-8") as fh:
            previous_data = json.load(fh)
    except (OSError, ValueError):
        return
    previous_milestones = {
        m["commit"]: m
        for m in previous_data.get("milestones", [])
        if m.get("cost_recorded") is not None
    }
    for m in milestones:
        previous = previous_milestones.get(m["commit"])
        if previous is not None:
            m["cost_recorded"] = previous["cost_recorded"]
            m["tokens"] = previous["tokens"]
            # The list of what was left unpriced, and the audit trail of
            # any --reprice, are part of the frozen cost record: they
            # travel with it, or a normal run would quietly drop them.
            m.pop("unpriced", None)
            if previous.get("unpriced"):
                m["unpriced"] = previous["unpriced"]
            if previous.get("repricings"):
                m["repricings"] = previous["repricings"]


def reprice_milestones(milestones, config, price_ledger, repriced_at):
    """The explicit, audited exception to "a recorded cost never changes".

    For a price that was WRONG, not one that changed: after a `corrects`
    entry has been appended to the ledger (or a missing series has been
    added), recomputes each milestone's cost from its own already-frozen
    tokens against the ledger as it is now. Only cost_recorded and
    unpriced can change; tokens, words, lines and notes are never
    touched, and no transcript is read. A milestone whose result differs
    gets an entry appended to its `repricings` list holding the moment
    and the cost (and unpriced list) it had before, so the old number is
    never lost. Running it again with nothing new in the ledger changes
    nothing.

    A normal price CHANGE (a later effective_date) does not alter
    anything here: price_at still picks the entry in effect on each
    milestone's own date, so past milestones keep their price. Returns
    the number of milestones changed."""
    changed = 0
    for m in milestones:
        new_cost, new_unpriced = _price(m["tokens"], price_ledger, config, m["date"])
        old_cost = m.get("cost_recorded")
        old_unpriced = m.get("unpriced") or []
        if new_cost == old_cost and new_unpriced == old_unpriced:
            continue
        record = {"repriced_at": repriced_at, "previous_cost": old_cost}
        if old_unpriced:
            record["previous_unpriced"] = old_unpriced
        m.setdefault("repricings", []).append(record)
        m["cost_recorded"] = new_cost
        m.pop("unpriced", None)
        if new_unpriced:
            m["unpriced"] = new_unpriced
        changed += 1
    return changed


def _load_frozen_data(data_path, flag="--reprice"):
    if not os.path.isfile(data_path):
        raise SystemExit(
            "%s needs an existing %s: it works on the milestones already recorded there. "
            "Run a normal generation first." % (flag, data_path)
        )
    try:
        with open(data_path, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError) as err:
        raise SystemExit("%s could not read %s: %s" % (flag, data_path, err))


def _enrich_dry_run(repo_root, config, data_path, claude_projects_dir, linked_projects):
    """What --enrich would do to the milestones frozen in data.json, from
    the transcripts as they are now; writes nothing and prints the
    report. Returns the process exit code: EXIT_SOME_REFUSED when any
    milestone was refused, so a script notices, else 0."""
    milestones = _load_frozen_data(data_path, "--enrich").get("milestones", [])
    rows, token_buckets, transcript_files, usage_events = read_token_buckets(
        repo_root, claude_projects_dir, linked_projects
    )
    buckets_by_commit = {row["hash"][:7]: bucket for row, bucket in zip(rows, token_buckets)}
    price_ledger = load_ledger(SHIPPED_PRICES)
    plan = plan_enrichment(
        milestones, buckets_by_commit, lambda tokens, date: _price(tokens, price_ledger, config, date)
    )
    print(format_report(plan, len(milestones), config["currency"], transcript_files, usage_events))
    return EXIT_SOME_REFUSED if any(r["status"] == "refused" for r in plan) else 0


def _recorded_total(milestones):
    return sum(m["cost_recorded"]["amount"] for m in milestones if m.get("cost_recorded") is not None)


def format_compact_count(n):
    """1234 -> "1,234"; 65130453 -> "65.1M"; 770294 -> "770.3K". Only
    for the headline number on a README thumbnail card, where a raw
    eight-digit token count would overflow a 320px-wide card; the
    dashboard's own KPI tiles keep showing the exact integer."""
    if n >= 1_000_000:
        return "%.1fM" % (n / 1_000_000)
    if n >= 10_000:
        return "%.1fK" % (n / 1_000)
    return "{:,}".format(n)


def _cost_cell(m):
    cost = m["cost_recorded"]
    unpriced = m.get("unpriced") or []
    text = "-" if cost is None else "$%.4f" % cost["amount"]
    if not unpriced:
        return "<td>%s</td>" % text
    if cost is not None:
        text += " (partial)"
    tip = "; ".join(
        "%s: %s tokens, %s" % (u["model"] or "no model id", "{:,}".format(u["tokens"]), u["reason"])
        for u in unpriced
    )
    return '<td title="Unpriced: %s">%s</td>' % (html.escape(tip, quote=True), html.escape(text))


def render_table_rows(milestones):
    # data-milestone-tokens lives on the <tr> itself so dashboard.js can
    # read a row's token counts without a second lookup. Only the flat
    # counters go there: the live price panel reprices all tokens at one
    # price, so the by_model split (which lives in data.json) is not needed.
    rows = []
    for m in milestones:
        subject_text = html.escape(m["subject"])
        note_text = html.escape(m["note"]) if m["note"] else ""
        tokens_json = json.dumps({key: m["tokens"].get(key, 0) for key in MODEL_TOKEN_FIELDS})
        rows.append(
            '<tr data-milestone-tokens=\'%s\'><td>%s</td><td>%s</td><td>%s</td><td>%d</td><td>%d</td>'
            '%s<td data-live-cost>-</td><td>%s</td></tr>'
            % (tokens_json, m["date"], m["commit"], subject_text, m["words_delta"], m["loc_delta"], _cost_cell(m), note_text)
        )
    return "\n".join(rows)


def render_unpriced_section(milestones):
    """What the recorded cost could not price, and why, one line each.
    Empty when nothing is unpriced, so a fully priced dashboard is
    unchanged."""
    lines = []
    for index, m in enumerate(milestones):
        for item in m.get("unpriced") or []:
            lines.append(
                "<li>M%d <code>%s</code>: <code>%s</code>, %s tokens. %s</li>" % (
                    index + 1, html.escape(m["commit"]),
                    html.escape(item["model"] or "no model id"),
                    "{:,}".format(item["tokens"]), html.escape(item["reason"]),
                )
            )
    if not lines:
        return ""
    return (
        '<div class="price-panel"><h2>Unpriced usage</h2>'
        '<p class="honest-note">Tokens the recorded cost does not include, because no ledger price '
        'applies to them. A milestone marked partial shows only what could be priced. To fill a gap, '
        'add a sourced entry with <code>engine/update_prices.py</code>, then run '
        '<code>engine/generate_metrics.py --reprice</code>.</p>'
        '<ul class="sources-list">%s</ul></div>' % "".join(lines)
    )


def render_dashboard_html(template_path, kpi_html, words_svg, loc_svg, tokens_svg, cost_svg, table_rows_html, embedded_js, zero_tokens_note="", unpriced_html=""):
    with open(template_path, encoding="utf-8") as fh:
        doc = fh.read()
    doc = doc.replace("__KPI_ROWS__", kpi_html)
    doc = doc.replace("__CHART_WORDS__", words_svg)
    doc = doc.replace("__CHART_LOC__", loc_svg)
    doc = doc.replace("__CHART_TOKENS__", tokens_svg)
    doc = doc.replace("__CHART_COST__", cost_svg)
    doc = doc.replace("__TABLE_ROWS__", table_rows_html)
    doc = doc.replace("__EMBEDDED_DATA__", embedded_js)
    doc = doc.replace("__ZERO_TOKENS_NOTE__", zero_tokens_note)
    doc = doc.replace("__UNPRICED_SECTION__", unpriced_html)
    return doc


def _previous_last_repriced_at(data_path):
    try:
        with open(data_path, encoding="utf-8") as fh:
            return json.load(fh).get("last_repriced_at")
    except (OSError, ValueError):
        return None


def main(repo_root=None, claude_projects_dir=None, linked_projects=None, reprice=False, enrich=False, dry_run=False):
    """Returns the process exit code: 0, or EXIT_SOME_REFUSED after an
    --enrich that refused a milestone."""
    if enrich and not dry_run:
        raise SystemExit(
            "--enrich only runs with --dry-run for now: it reports what it would do and writes nothing. "
            "The write path is not implemented yet."
        )
    repo_root = repo_root or os.getcwd()
    claude_projects_dir = claude_projects_dir or os.path.expanduser("~/.claude/projects")

    if not (reprice or enrich):
        bootstrap_if_missing(repo_root, "logbook")
    # --reprice and --enrich never bootstrap a folder: they only work on
    # milestones that already exist in a data.json.
    config = load_config(os.path.join(repo_root, "logbook", "config.json"))
    folder = os.path.join(repo_root, config["milestone_folder"])
    data_path = os.path.join(folder, "data.json")
    if enrich:
        return _enrich_dry_run(repo_root, config, data_path, claude_projects_dir, linked_projects)
    generated_at = datetime.now(timezone.utc).isoformat()
    last_repriced_at = _previous_last_repriced_at(data_path)

    if reprice:
        frozen = _load_frozen_data(data_path)
        milestones = frozen.get("milestones", [])
        # generated_at keeps saying when the milestones and tokens were
        # last read from git and transcripts; only the costs are new.
        generated_at = frozen.get("generated_at", generated_at)
        before = _recorded_total(milestones)
        now = datetime.now(timezone.utc).isoformat()
        changed = reprice_milestones(milestones, config, load_ledger(SHIPPED_PRICES), now)
        if changed:
            last_repriced_at = now
        print(
            "repriced %d of %d milestones from their frozen tokens; recorded cost %.4f -> %.4f %s"
            % (changed, len(milestones), before, _recorded_total(milestones), config["currency"])
        )
    else:
        os.makedirs(folder, exist_ok=True)
        milestones = build_milestones(repo_root, config, claude_projects_dir, linked_projects)

        if len(milestones) == 0:
            print("warning: no commits found in this repository, nothing to report", file=sys.stderr)

        # A milestone's recorded cost, and the tokens it was computed from,
        # are frozen the moment they are first written, so this must happen
        # before anything below reads `milestones` (the zero-tokens check,
        # the table, the charts, and data.json all need the final, frozen
        # numbers, not the freshly recomputed ones freezing might override).
        _freeze_previously_recorded_costs(milestones, data_path)

    os.makedirs(folder, exist_ok=True)
    all_tokens = sum(total_tokens(m["tokens"]) for m in milestones)
    zero_tokens = all_tokens == 0
    if zero_tokens:
        print(
            "warning: no LLM session transcripts were found for this project (see AGENTS.md); "
            "token and cost figures will show as zero or absent, not because nothing happened, "
            "but because no matching transcript was found"
        )

    words_series = [m["words_delta"] for m in milestones] or [0]
    loc_series = [m["loc_delta"] for m in milestones] or [0]
    tokens_series = [total_tokens(m["tokens"]) for m in milestones] or [0]
    # A milestone with no recorded cost (no price entry covered it yet)
    # contributes 0 to the running total charted below, the same
    # convention the "Cost recorded" KPI uses: it is a ledger of what
    # is actually known, never a guess for what is not.
    cost_series = [
        (m["cost_recorded"]["amount"] if m["cost_recorded"] is not None else 0.0)
        for m in milestones
    ] or [0.0]
    x_labels = ["M%d" % (i + 1) for i in range(len(milestones))] or ["M1"]

    # The charts show cumulative growth over time, the point of a
    # growth chart, even though words_delta/loc_delta are now genuine
    # per-milestone deltas: accumulate them back into running totals
    # for charting only. tokens_series is already a genuine per-commit
    # bucket (see _bucket_tokens_by_commit), so its chart is unchanged.
    # cost_series accumulates too: a ledger's own running balance is
    # the whole point of charting it.
    words_cumulative = list(itertools.accumulate(words_series))
    loc_cumulative = list(itertools.accumulate(loc_series))
    cost_cumulative = list(itertools.accumulate(cost_series))

    words_svg = svg_growth_chart("w", words_cumulative, x_labels, lambda v: str(int(v)), "Words per milestone", "Words published")
    loc_svg = svg_growth_chart("l", loc_cumulative, x_labels, lambda v: str(int(v)), "Lines per milestone", "Lines of code")
    tokens_svg = svg_growth_chart("t", tokens_series, x_labels, lambda v: str(int(v)), "Tokens per milestone", "LLM tokens consumed")
    cost_svg = svg_growth_chart("c", cost_cumulative, x_labels, lambda v: "$%.2f" % v, "Recorded cost per milestone", "Cumulative recorded cost (USD)")

    total_cost = sum(cost_series)
    # A milestone counts as unpriced when nothing about it was priced, or
    # when only part of it was (its `unpriced` list says what is missing).
    unpriced_count = sum(1 for m in milestones if m["cost_recorded"] is None or m.get("unpriced"))
    kpi_html = (
        '<div class="kpi"><span class="kpi-n">%d</span><span class="kpi-l">Milestones</span></div>'
        '<div class="kpi"><span class="kpi-n">%d</span><span class="kpi-l">Words published</span></div>'
        '<div class="kpi"><span class="kpi-n">%d</span><span class="kpi-l">Tokens consumed</span></div>'
        '<div class="kpi"><span class="kpi-n">$%.2f</span><span class="kpi-l">Cost recorded</span></div>'
        '<div class="kpi"><span class="kpi-n">%d</span><span class="kpi-l">Unpriced milestones</span></div>'
        % (len(milestones), sum(words_series), sum(tokens_series), total_cost, unpriced_count)
    )
    table_rows_html = render_table_rows(milestones)

    words_thumb = svg_stat_thumbnail(format_compact_count(sum(words_series)), "Words published", words_cumulative)
    tokens_thumb = svg_stat_thumbnail(format_compact_count(sum(tokens_series)), "Tokens consumed", tokens_series)
    cost_thumb = svg_stat_thumbnail("$%.2f" % total_cost, "Cost recorded", cost_cumulative)

    price_ledger = load_ledger(SHIPPED_PRICES)
    prices_by_series = {
        "%s::%s" % (s["provider"], s["model"]): s["entries"] for s in price_ledger
    }
    today = datetime.now(timezone.utc).date().isoformat()
    embedded_js = "window.MILESTONE_DATA = %s;" % json.dumps({
        "prices": prices_by_series,
        "today": today,
        # The series this project's config prices with: the price panel
        # opens on it instead of on whichever series sorts first.
        "default_series": "%s::%s" % (config["price_provider"], config["price_model"]),
    })

    zero_tokens_note = (
        '<p class="honest-note">No LLM session transcripts were found for this project '
        '(see AGENTS.md). Token and cost figures below show as zero or absent because no '
        'matching transcript was found, not because no work happened.</p>'
        if zero_tokens else ""
    )

    html_out = render_dashboard_html(
        os.path.join(TEMPLATE_DIR, "dashboard.html"), kpi_html, words_svg, loc_svg, tokens_svg, cost_svg,
        table_rows_html, embedded_js, zero_tokens_note, render_unpriced_section(milestones),
    )
    with open(os.path.join(folder, "dashboard.html"), "w", encoding="utf-8") as fh:
        fh.write(html_out)
    shutil.copy(os.path.join(TEMPLATE_DIR, "dashboard.js"), os.path.join(folder, "dashboard.js"))

    # Small, standalone thumbnail cards, meant to be embedded outside
    # the dashboard itself (the README, say) where a full interactive
    # page cannot go. Regenerated on every run alongside the dashboard
    # so they can never drift from the numbers it shows.
    for name, svg in (
        ("thumb-words.svg", words_thumb),
        ("thumb-tokens.svg", tokens_thumb),
        ("thumb-cost.svg", cost_thumb),
    ):
        with open(os.path.join(folder, name), "w", encoding="utf-8") as fh:
            fh.write(svg + "\n")

    with open(data_path, "w", encoding="utf-8") as fh:
        document = {"generated_at": generated_at}
        if last_repriced_at:
            document["last_repriced_at"] = last_repriced_at
        document["milestones"] = milestones
        json.dump(document, fh, indent=2)
        fh.write("\n")

    print("wrote %d milestones to %s" % (len(milestones), folder))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Generate this repository's milestone ledger dashboard.")
    parser.add_argument("--repo", default=None, help="Repository root, defaults to the current directory.")
    parser.add_argument("--claude-projects-dir", default=None, help="Defaults to ~/.claude/projects.")
    parser.add_argument(
        "--linked-project", action="append", dest="linked_projects", default=None,
        help=(
            "Absolute path to another project whose subagent transcripts should also be "
            "scanned, restricted to the ones that literally reference this repository's own "
            "path (see engine/readers/claude_code.find_linked_subagent_jsonl). Only for a "
            "project actually built by subagents dispatched from that other project's own "
            "session. Repeatable."
        ),
    )
    parser.add_argument(
        "--reprice", action="store_true",
        help=(
            "Correct a price that was WRONG (not one that changed): recompute only the cost of "
            "milestones already frozen in data.json, from their already-frozen tokens, against "
            "the current price ledger, and record the old cost of every milestone that changes. "
            "Use it after appending a `corrects` entry (or adding a missing model series) with "
            "update_prices.py. Never reads git history or transcripts, never touches tokens. "
            "Do not use it for a normal price change: a later effective_date already leaves "
            "past milestones at the price that was true then."
        ),
    )
    parser.add_argument(
        "--enrich", action="store_true",
        help=(
            "One-time migration for milestones frozen before the per-model and per-TTL token "
            "split: re-derive the split from the transcripts and apply it only where the four "
            "original counters match the frozen ones exactly (see engine/enrich.py). Unlike "
            "--reprice it reads transcripts, so --linked-project and --claude-projects-dir apply. "
            "Only --dry-run is implemented so far: it prints what it would do and writes nothing. "
            "Exits with %d when it refused at least one milestone." % EXIT_SOME_REFUSED
        ),
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="With --enrich: report what would change and write nothing.",
    )
    args = parser.parse_args()
    if args.enrich and args.reprice:
        parser.error("--enrich and --reprice are separate corrections; run one at a time")
    if args.dry_run and not args.enrich:
        parser.error("--dry-run only applies to --enrich")
    if args.reprice and (args.linked_projects or args.claude_projects_dir):
        parser.error(
            "--reprice never reads transcripts, so --linked-project and "
            "--claude-projects-dir do not apply to it"
        )
    sys.exit(main(
        repo_root=args.repo, claude_projects_dir=args.claude_projects_dir,
        linked_projects=args.linked_projects, reprice=args.reprice,
        enrich=args.enrich, dry_run=args.dry_run,
    ))
