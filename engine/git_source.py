"""Reads git history and counts words or lines per commit, scoped by
the file globs a config.json declares. Generalises harness-medir's
build/generate_logbook_metrics.py, which hardcoded its own file list."""

import fnmatch
import re
import subprocess


def run_git(repo_root, args):
    result = subprocess.run(
        ["git", *args], cwd=repo_root, capture_output=True, text=True, encoding="utf-8"
    )
    return result.stdout


def commits(repo_root):
    out = run_git(repo_root, ["log", "--reverse", "--pretty=format:%H|%aI|%s"])
    rows = []
    for line in out.splitlines():
        if not line.strip():
            continue
        commit_hash, iso, subject = line.split("|", 2)
        rows.append({"hash": commit_hash, "iso": iso, "subject": subject})
    return rows


def git_show(repo_root, rev, path):
    result = subprocess.run(
        ["git", "show", "%s:%s" % (rev, path)],
        cwd=repo_root,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if result.returncode != 0:
        return None
    return result.stdout


def list_repo_files_at(repo_root, rev):
    out = run_git(repo_root, ["ls-tree", "-r", "--name-only", rev])
    return {line for line in out.splitlines() if line.strip()}


def word_count_html(text):
    if text is None:
        return 0
    body = re.sub(r"<pre>.*?</pre>", " ", text, flags=re.S)
    body = re.sub(r"<svg.*?</svg>", " ", body, flags=re.S)
    body = re.sub(r"<script.*?</script>", " ", body, flags=re.S)
    body = re.sub(r"<style.*?</style>", " ", body, flags=re.S)
    body = re.sub(r"<[^>]+>", " ", body)
    return len(body.split())


def line_count(text):
    if text is None:
        return 0
    return len(text.splitlines())


def matches_any(path, globs):
    for pattern in globs:
        if pattern.endswith("/"):
            if path.startswith(pattern):
                return True
        elif fnmatch.fnmatch(path, pattern):
            return True
    return False


def sum_metric(repo_root, rev, files_at_commit, globs, metric_fn):
    total = 0
    for path in sorted(files_at_commit):
        if matches_any(path, globs):
            total += metric_fn(git_show(repo_root, rev, path))
    return total
