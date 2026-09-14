"""Orchestrates the whole engine: reads git history and, when
available, this machine's Claude Code transcript for this exact
project, prices each milestone against the ledger at the price in
effect on that date, scrubs every string field, and writes both
data.json and a fully rendered dashboard.html. Safe to run from any
of this skill's supported environments: the output does not depend on
which one called it, only on the repository's own git history and
session transcripts."""

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

from engine.config import load_config
from engine.cost import compute_cost
from engine.git_source import commits, line_count, list_repo_files_at, matches_any, sum_metric, word_count_html
from engine.prices import find_series, load_ledger, price_at
from engine.readers.claude_code import find_linked_subagent_jsonl, find_session_jsonl, load_usage_events
from engine.scrub import scrub_text
from engine.svg_chart import svg_growth_chart

HERE = os.path.dirname(os.path.abspath(__file__))
TEMPLATE_DIR = os.path.join(os.path.dirname(HERE), "template")
EXAMPLE_CONFIG = os.path.join(HERE, "config.example.json")
SHIPPED_PRICES = os.path.join(HERE, "prices.json")
EMPTY_TOKENS = {"input": 0, "output": 0, "cache_read": 0, "cache_creation": 0}


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
        bucket = dict(EMPTY_TOKENS)
        while ev_idx < len(events):
            e_dt = datetime.fromisoformat(events[ev_idx]["ts"].replace("Z", "+00:00"))
            if e_dt <= commit_dt:
                for key in EMPTY_TOKENS:
                    bucket[key] += events[ev_idx][key]
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


def build_milestones(repo_root, config, claude_projects_dir, linked_projects=None):
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
    token_buckets = _bucket_tokens_by_commit(rows, events)
    notes = load_notes(repo_root, config["milestone_folder"])
    price_ledger = load_ledger(SHIPPED_PRICES)
    price_series = find_series(price_ledger, config["price_provider"], config["price_model"])
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
        price_entry = price_at(price_series, commit_date)
        # A milestone with no recorded token usage (no transcript covers
        # it) gets no cost_recorded at all, not a misleading $0.00: a
        # price entry existing is not the same as this milestone having
        # anything to price.
        has_usage = any(tokens.values())
        cost = compute_cost(tokens, price_entry) if (price_entry and has_usage) else None
        note = notes.get(row["hash"][:7])
        milestones.append({
            "date": commit_date,
            "commit": row["hash"][:7],
            "subject": scrub_text(row["subject"]),
            "words_delta": words_delta,
            "loc_delta": loc_delta,
            "tokens": tokens,
            "cost_recorded": (
                {
                    "amount": cost, "currency": config["currency"],
                    "priced_at": price_entry["effective_date"],
                    "provider": config["price_provider"], "model": config["price_model"],
                }
                if cost is not None else None
            ),
            "note": scrub_text(note) if note else None,
        })
    return milestones


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


def render_table_rows(milestones):
    # data-milestone-tokens lives on the <tr> itself so dashboard.js can
    # read a row's token counts without a second lookup.
    rows = []
    for m in milestones:
        cost_text = "-" if m["cost_recorded"] is None else "$%.4f" % m["cost_recorded"]["amount"]
        subject_text = html.escape(m["subject"])
        note_text = html.escape(m["note"]) if m["note"] else ""
        tokens_json = json.dumps(m["tokens"])
        rows.append(
            '<tr data-milestone-tokens=\'%s\'><td>%s</td><td>%s</td><td>%s</td><td>%d</td><td>%d</td>'
            '<td>%s</td><td data-live-cost>-</td><td>%s</td></tr>'
            % (tokens_json, m["date"], m["commit"], subject_text, m["words_delta"], m["loc_delta"], cost_text, note_text)
        )
    return "\n".join(rows)


def render_dashboard_html(template_path, kpi_html, words_svg, loc_svg, tokens_svg, table_rows_html, embedded_js, zero_tokens_note=""):
    with open(template_path, encoding="utf-8") as fh:
        doc = fh.read()
    doc = doc.replace("__KPI_ROWS__", kpi_html)
    doc = doc.replace("__CHART_WORDS__", words_svg)
    doc = doc.replace("__CHART_LOC__", loc_svg)
    doc = doc.replace("__CHART_TOKENS__", tokens_svg)
    doc = doc.replace("__TABLE_ROWS__", table_rows_html)
    doc = doc.replace("__EMBEDDED_DATA__", embedded_js)
    doc = doc.replace("__ZERO_TOKENS_NOTE__", zero_tokens_note)
    return doc


def main(repo_root=None, claude_projects_dir=None, linked_projects=None):
    repo_root = repo_root or os.getcwd()
    claude_projects_dir = claude_projects_dir or os.path.expanduser("~/.claude/projects")

    bootstrap_if_missing(repo_root, "logbook")
    config = load_config(os.path.join(repo_root, "logbook", "config.json"))
    folder = os.path.join(repo_root, config["milestone_folder"])
    os.makedirs(folder, exist_ok=True)

    milestones = build_milestones(repo_root, config, claude_projects_dir, linked_projects)

    if len(milestones) == 0:
        print("warning: no commits found in this repository, nothing to report", file=sys.stderr)

    # A milestone's recorded cost, and the tokens it was computed from,
    # are frozen the moment they are first written, so this must happen
    # before anything below reads `milestones` (the zero-tokens check,
    # the table, the charts, and data.json all need the final, frozen
    # numbers, not the freshly recomputed ones freezing might override).
    data_path = os.path.join(folder, "data.json")
    _freeze_previously_recorded_costs(milestones, data_path)

    total_tokens = sum(sum(m["tokens"].values()) for m in milestones)
    zero_tokens = total_tokens == 0
    if zero_tokens:
        print(
            "warning: no LLM session transcripts were found for this project (see AGENTS.md); "
            "token and cost figures will show as zero or absent, not because nothing happened, "
            "but because no matching transcript was found"
        )

    words_series = [m["words_delta"] for m in milestones] or [0]
    loc_series = [m["loc_delta"] for m in milestones] or [0]
    tokens_series = [sum(m["tokens"].values()) for m in milestones] or [0]
    x_labels = ["M%d" % (i + 1) for i in range(len(milestones))] or ["M1"]

    # The charts show cumulative growth over time, the point of a
    # growth chart, even though words_delta/loc_delta are now genuine
    # per-milestone deltas: accumulate them back into running totals
    # for charting only. tokens_series is already a genuine per-commit
    # bucket (see _bucket_tokens_by_commit), so its chart is unchanged.
    words_cumulative = list(itertools.accumulate(words_series))
    loc_cumulative = list(itertools.accumulate(loc_series))

    words_svg = svg_growth_chart("w", words_cumulative, x_labels, lambda v: str(int(v)), "Words per milestone", "Words published")
    loc_svg = svg_growth_chart("l", loc_cumulative, x_labels, lambda v: str(int(v)), "Lines per milestone", "Lines of code")
    tokens_svg = svg_growth_chart("t", tokens_series, x_labels, lambda v: str(int(v)), "Tokens per milestone", "LLM tokens consumed")

    kpi_html = (
        '<div class="kpi"><span class="kpi-n">%d</span><span class="kpi-l">Milestones</span></div>'
        '<div class="kpi"><span class="kpi-n">%d</span><span class="kpi-l">Words published</span></div>'
        '<div class="kpi"><span class="kpi-n">%d</span><span class="kpi-l">Tokens consumed</span></div>'
        % (len(milestones), sum(words_series), sum(tokens_series))
    )
    table_rows_html = render_table_rows(milestones)

    price_ledger = load_ledger(SHIPPED_PRICES)
    prices_by_series = {
        "%s::%s" % (s["provider"], s["model"]): s["entries"] for s in price_ledger
    }
    today = datetime.now(timezone.utc).date().isoformat()
    embedded_js = "window.MILESTONE_DATA = %s;" % json.dumps({
        "prices": prices_by_series,
        "today": today,
    })

    zero_tokens_note = (
        '<p class="honest-note">No LLM session transcripts were found for this project '
        '(see AGENTS.md). Token and cost figures below show as zero or absent because no '
        'matching transcript was found, not because no work happened.</p>'
        if zero_tokens else ""
    )

    html_out = render_dashboard_html(
        os.path.join(TEMPLATE_DIR, "dashboard.html"), kpi_html, words_svg, loc_svg, tokens_svg,
        table_rows_html, embedded_js, zero_tokens_note,
    )
    with open(os.path.join(folder, "dashboard.html"), "w", encoding="utf-8") as fh:
        fh.write(html_out)
    shutil.copy(os.path.join(TEMPLATE_DIR, "dashboard.js"), os.path.join(folder, "dashboard.js"))

    with open(data_path, "w", encoding="utf-8") as fh:
        json.dump({
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "milestones": milestones,
        }, fh, indent=2)
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
    args = parser.parse_args()
    main(repo_root=args.repo, claude_projects_dir=args.claude_projects_dir, linked_projects=args.linked_projects)
