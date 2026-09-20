"""Finds and reads this machine's Claude Code session transcripts for
exactly one project, never a suffix match that could collide with a
similarly named sibling project. The dedup-by-message-id rule below
was found and fixed by direct reuse of this project's ancestor script
in an external project: a single assistant message writes more than
one JSONL line (a thinking block, a text block, a tool-use block),
each carrying the whole message's cumulative usage repeated. Without
dedup, the same tokens are counted once per line."""

import json
import os
import re


def encode_project_path(path):
    normalized = os.path.abspath(path)
    drive, rest = os.path.splitdrive(normalized)
    combined = drive.lower() + rest
    return re.sub(r"[:\\/. ]", "-", combined)


def find_nested_subagent_jsonl(project_dir):
    """A subagent dispatched via the Task tool gets its own JSONL
    transcript, filed under its parent session's own directory as
    <project_dir>/<parent-session-uuid>/subagents/*.jsonl, never as a
    top-level file next to the parent's own <uuid>.jsonl. Without this,
    every token a subagent spent (routine for a project built with a
    subagent-driven workflow) would be silently missed, even though it
    lives inside this same, correctly-scoped project directory."""
    paths = []
    if not os.path.isdir(project_dir):
        return paths
    for name in sorted(os.listdir(project_dir)):
        subagents_dir = os.path.join(project_dir, name, "subagents")
        if not os.path.isdir(subagents_dir):
            continue
        paths.extend(
            os.path.join(subagents_dir, sub_name)
            for sub_name in sorted(os.listdir(subagents_dir))
            if sub_name.endswith(".jsonl")
        )
    return paths


def find_session_jsonl(claude_projects_dir, repo_root):
    if not os.path.isdir(claude_projects_dir):
        return []
    target = encode_project_path(repo_root)
    project_dir = os.path.join(claude_projects_dir, target)
    if not os.path.isdir(project_dir):
        return []
    top_level = [
        os.path.join(project_dir, name)
        for name in sorted(os.listdir(project_dir))
        if name.endswith(".jsonl")
    ]
    return top_level + find_nested_subagent_jsonl(project_dir)


def _path_variants(path):
    """Every textual form an absolute path might actually appear in
    inside a JSONL transcript line: both slash styles, both
    drive-letter cases (a transcript stores whatever casing was typed
    or resolved at the time, not a normalised one), and the
    JSON-escaped double-backslash form a literal backslash takes once
    serialised into a JSONL line."""
    normalized = os.path.abspath(path)
    drive, rest = os.path.splitdrive(normalized)
    variants = set()
    for one_drive in {drive, drive.upper(), drive.lower()}:
        full = one_drive + rest
        variants.add(full)
        variants.add(full.replace("\\", "/"))
        variants.add(full.replace("\\", "\\\\"))
    return variants


def find_linked_subagent_jsonl(claude_projects_dir, linked_project_root, target_repo_root):
    """Finds subagent transcripts filed under a DIFFERENT project's own
    session directories, scoped to only the ones that actually did
    work on target_repo_root. This exists for one real, named
    situation: a project built by subagents dispatched from a sibling
    top-level session, whose transcripts are filed under that
    sibling's own project path, never this one's, so
    find_session_jsonl's ordinary exact-match lookup cannot see them.

    Never includes a linked project's own top-level session file: that
    file mixes together a whole session's unrelated same-day work in
    the linked project and cannot be safely scoped to just this
    project's share of it. Only a subagent transcript whose own
    content literally contains target_repo_root's absolute path
    qualifies. Never a same-day time window, never a guess: a
    subagent's transcript either shows it was actually pointed at this
    repository's files, or it is excluded."""
    if not os.path.isdir(claude_projects_dir):
        return []
    linked_dir = os.path.join(claude_projects_dir, encode_project_path(linked_project_root))
    variants = _path_variants(target_repo_root)
    matches = []
    for path in find_nested_subagent_jsonl(linked_dir):
        with open(path, encoding="utf-8") as fh:
            content = fh.read()
        if any(variant in content for variant in variants):
            matches.append(path)
    return matches


def _as_count(value):
    """A token count from a transcript field: a non-negative int, else 0.
    A bool is an int in Python but never a count."""
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return 0
    return value


def _one_hour_cache_writes(usage, cache_creation_total):
    """The 1-hour share of a message's cache writes. A real transcript
    carries usage.cache_creation = {"ephemeral_5m_input_tokens": N,
    "ephemeral_1h_input_tokens": M}; older or partial rows have no such
    object, and then nothing is known to be 1-hour, so 0. Capped at the
    message's own cache_creation_input_tokens so the 1-hour figure is
    always a subset of it, never a second, larger number."""
    breakdown = usage.get("cache_creation")
    if not isinstance(breakdown, dict):
        return 0
    return min(_as_count(breakdown.get("ephemeral_1h_input_tokens")), cache_creation_total)


def load_usage_events(paths):
    """One event per assistant message: its timestamp, the model id that
    served it (message.model, None when the row has none), and its token
    counts, with cache_creation_1h the 1-hour subset of cache_creation.
    Deduped by message id, keeping the last occurrence (see the module
    docstring)."""
    by_id = {}
    unkeyed = []
    for path in paths:
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                except ValueError:
                    continue
                message = row.get("message")
                timestamp = row.get("timestamp")
                if not (isinstance(message, dict) and "usage" in message and timestamp):
                    continue
                usage = message["usage"]
                cache_creation = usage.get("cache_creation_input_tokens", 0) or 0
                model = message.get("model")
                event = {
                    "ts": timestamp,
                    "model": model if isinstance(model, str) and model else None,
                    "input": usage.get("input_tokens", 0) or 0,
                    "output": usage.get("output_tokens", 0) or 0,
                    "cache_read": usage.get("cache_read_input_tokens", 0) or 0,
                    "cache_creation": cache_creation,
                    "cache_creation_1h": _one_hour_cache_writes(usage, _as_count(cache_creation)),
                }
                message_id = message.get("id")
                if message_id:
                    by_id[message_id] = event
                else:
                    unkeyed.append(event)
    events = list(by_id.values()) + unkeyed
    events.sort(key=lambda e: e["ts"])
    return events
