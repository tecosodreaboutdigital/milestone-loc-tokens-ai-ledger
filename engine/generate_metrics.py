"""Orchestrates the whole engine: reads git history and, when
available, this machine's Claude Code transcript for this exact
project, prices each milestone against the ledger at the price in
effect on that date, scrubs every string field, and writes both
data.json and a fully rendered dashboard.html. Safe to run from any
of this skill's supported environments: the output does not depend on
which one called it, only on the repository's own git history and
session transcripts."""

import argparse
import json
import os
import shutil
from datetime import datetime, timezone

from engine.config import load_config
from engine.cost import compute_cost
from engine.git_source import commits, line_count, list_repo_files_at, sum_metric, word_count_html
from engine.prices import find_series, load_ledger, price_at
from engine.readers.claude_code import find_session_jsonl, load_usage_events
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


def build_milestones(repo_root, config, claude_projects_dir):
    rows = commits(repo_root)
    session_files = find_session_jsonl(claude_projects_dir, repo_root)
    events = load_usage_events(session_files)
    token_buckets = _bucket_tokens_by_commit(rows, events)
    notes = load_notes(repo_root, config["milestone_folder"])
    price_ledger = load_ledger(SHIPPED_PRICES)
    price_series = find_series(price_ledger, config["price_provider"], config["price_model"])

    milestones = []
    for row, tokens in zip(rows, token_buckets):
        files_at_commit = list_repo_files_at(repo_root, row["hash"])
        words = sum_metric(repo_root, row["hash"], files_at_commit, config["content_globs"], word_count_html)
        loc = sum_metric(repo_root, row["hash"], files_at_commit, config["code_globs"], line_count)
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
            "words_delta": words,
            "loc_delta": loc,
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


def render_table_rows(milestones):
    # data-milestone-tokens lives on the <tr> itself so dashboard.js can
    # read a row's token counts without a second lookup.
    rows = []
    for m in milestones:
        cost_text = "-" if m["cost_recorded"] is None else "$%.4f" % m["cost_recorded"]["amount"]
        note_text = m["note"] or ""
        tokens_json = json.dumps(m["tokens"])
        rows.append(
            '<tr data-milestone-tokens=\'%s\'><td>%s</td><td>%s</td><td>%d</td><td>%d</td>'
            '<td>%s</td><td data-live-cost>-</td><td>%s</td></tr>'
            % (tokens_json, m["date"], m["commit"], m["words_delta"], m["loc_delta"], cost_text, note_text)
        )
    return "\n".join(rows)


def render_dashboard_html(template_path, kpi_html, words_svg, tokens_svg, table_rows_html, embedded_js):
    with open(template_path, encoding="utf-8") as fh:
        html = fh.read()
    html = html.replace("__KPI_ROWS__", kpi_html)
    html = html.replace("__CHART_WORDS__", words_svg)
    html = html.replace("__CHART_TOKENS__", tokens_svg)
    html = html.replace("__TABLE_ROWS__", table_rows_html)
    html = html.replace("__EMBEDDED_DATA__", embedded_js)
    return html


def main(repo_root=None, claude_projects_dir=None):
    repo_root = repo_root or os.getcwd()
    claude_projects_dir = claude_projects_dir or os.path.expanduser("~/.claude/projects")

    bootstrap_if_missing(repo_root, "logbook")
    config = load_config(os.path.join(repo_root, "logbook", "config.json"))
    folder = os.path.join(repo_root, config["milestone_folder"])

    milestones = build_milestones(repo_root, config, claude_projects_dir)

    words_series = [m["words_delta"] for m in milestones] or [0]
    tokens_series = [sum(m["tokens"].values()) for m in milestones] or [0]
    x_labels = ["M%d" % (i + 1) for i in range(len(milestones))] or ["M1"]

    words_svg = svg_growth_chart("w", words_series, x_labels, lambda v: str(int(v)), "Words per milestone", "Words published")
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

    html = render_dashboard_html(
        os.path.join(TEMPLATE_DIR, "dashboard.html"), kpi_html, words_svg, tokens_svg, table_rows_html, embedded_js,
    )
    with open(os.path.join(folder, "dashboard.html"), "w", encoding="utf-8") as fh:
        fh.write(html)
    shutil.copy(os.path.join(TEMPLATE_DIR, "dashboard.js"), os.path.join(folder, "dashboard.js"))

    with open(os.path.join(folder, "data.json"), "w", encoding="utf-8") as fh:
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
    args = parser.parse_args()
    main(repo_root=args.repo, claude_projects_dir=args.claude_projects_dir)
