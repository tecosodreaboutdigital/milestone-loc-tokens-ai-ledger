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


def find_session_jsonl(claude_projects_dir, repo_root):
    if not os.path.isdir(claude_projects_dir):
        return []
    target = encode_project_path(repo_root)
    project_dir = os.path.join(claude_projects_dir, target)
    if not os.path.isdir(project_dir):
        return []
    return [
        os.path.join(project_dir, name)
        for name in sorted(os.listdir(project_dir))
        if name.endswith(".jsonl")
    ]


def load_usage_events(paths):
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
                event = {
                    "ts": timestamp,
                    "input": usage.get("input_tokens", 0) or 0,
                    "output": usage.get("output_tokens", 0) or 0,
                    "cache_read": usage.get("cache_read_input_tokens", 0) or 0,
                    "cache_creation": usage.get("cache_creation_input_tokens", 0) or 0,
                }
                message_id = message.get("id")
                if message_id:
                    by_id[message_id] = event
                else:
                    unkeyed.append(event)
    events = list(by_id.values()) + unkeyed
    events.sort(key=lambda e: e["ts"])
    return events
