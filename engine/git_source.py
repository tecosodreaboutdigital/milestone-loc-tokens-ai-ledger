"""Reads git history and counts words or lines per commit, scoped by
the file globs a config.json declares. Generalises harness-medir's
build/generate_logbook_metrics.py, which hardcoded its own file list."""

import contextlib
import fnmatch
import io
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
    return set(_tree_at(repo_root, rev))


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


def _decode_like_git_show(data):
    """The text git_show returns for the same bytes: UTF-8 with replacement,
    universal newlines (what subprocess text=True applies)."""
    text = data.decode("utf-8", "replace")
    return text.replace("\r\n", "\n").replace("\r", "\n")


_FULL_HASH = re.compile(r"^[0-9a-f]{40}([0-9a-f]{24})?$")
# The last tree read, keyed by (repo_root, commit hash): list_repo_files_at
# and both sum_metric calls for one commit share a single `git ls-tree`.
# Only a full commit hash is cached, since a ref such as HEAD can move.
_last_tree = [None, None]


def _tree_at(repo_root, rev):
    """path -> blob id (None for a non-blob entry such as a submodule), each
    path spelled exactly as `git ls-tree -r --name-only` spells it, git's
    default quoting of non-ASCII paths included."""
    key = (repo_root, rev)
    if _FULL_HASH.match(rev) and _last_tree[0] == key:
        return _last_tree[1]
    entries = {}
    for line in run_git(repo_root, ["ls-tree", "-r", rev]).splitlines():
        if not line.strip():
            continue
        meta, _, path = line.partition("\t")
        parts = meta.split()
        entries[path] = parts[2] if len(parts) == 3 and parts[1] == "blob" else None
    if _FULL_HASH.match(rev):
        _last_tree[0], _last_tree[1] = key, entries
    return entries


def _blob_ids_at(repo_root, rev):
    return {path: blob_id for path, blob_id in _tree_at(repo_root, rev).items() if blob_id}


def _parse_batch(read_line, read_bytes):
    """One `git cat-file --batch` answer: (is_blob, text or None)."""
    header = read_line().decode("ascii", "replace").split()
    if len(header) != 3:
        return False, None  # "<id> missing"
    body = read_bytes(int(header[2]))
    read_bytes(1)  # the newline after every object
    if header[1] != "blob":
        return False, None
    return True, _decode_like_git_show(body)


class _BatchReader:
    """A `git cat-file --batch` kept open for a whole run (see batch_reads).
    One id is written and its answer read before the next, so neither pipe
    can fill up and block the other."""

    def __init__(self, repo_root):
        self.proc = subprocess.Popen(
            ["git", "cat-file", "--batch"], cwd=repo_root,
            stdin=subprocess.PIPE, stdout=subprocess.PIPE,
        )

    def read(self, blob_ids):
        texts = {}
        for blob_id in blob_ids:
            self.proc.stdin.write((blob_id + "\n").encode("ascii"))
            self.proc.stdin.flush()
            is_blob, text = _parse_batch(self.proc.stdout.readline, self.proc.stdout.read)
            if is_blob:
                texts[blob_id] = text
        return texts

    def close(self):
        self.proc.stdin.close()
        self.proc.stdout.close()
        self.proc.wait()


_open_readers = {}


@contextlib.contextmanager
def batch_reads(repo_root):
    """Keep one `git cat-file --batch` open while measuring many commits.
    The process is closed on exit, so the repository folder is never left
    held open (Windows cannot delete a folder a live process still uses)."""
    reader = _BatchReader(repo_root)
    _open_readers[repo_root] = reader
    try:
        yield
    finally:
        _open_readers.pop(repo_root, None)
        reader.close()


def read_blobs(repo_root, blob_ids):
    """blob id -> text, for many blobs at once: through the reader batch_reads
    opened, or else one `git cat-file --batch` for this call. Either way it
    replaces what used to be one `git show` process per file."""
    if not blob_ids:
        return {}
    reader = _open_readers.get(repo_root)
    if reader is not None:
        return reader.read(blob_ids)
    result = subprocess.run(
        ["git", "cat-file", "--batch"],
        cwd=repo_root,
        input="".join(blob_id + "\n" for blob_id in blob_ids).encode("ascii"),
        capture_output=True,
    )
    out = io.BytesIO(result.stdout)
    texts = {}
    for blob_id in blob_ids:
        is_blob, text = _parse_batch(out.readline, out.read)
        if is_blob:
            texts[blob_id] = text
    return texts


# Measured values per (blob id, metric). A blob id names exact content, so
# a file left unchanged across many commits is read and measured once per
# run, not once per commit.
_measured = {}


def sum_metric(repo_root, rev, files_at_commit, globs, metric_fn):
    blob_ids = _blob_ids_at(repo_root, rev)
    paths = [path for path in sorted(files_at_commit) if matches_any(path, globs)]
    to_read = []
    for path in paths:
        blob_id = blob_ids.get(path)
        # A path git had to quote (non-ASCII) never resolved through
        # `git show rev:path` either; it keeps counting as before.
        if blob_id and not path.startswith('"') and (blob_id, metric_fn) not in _measured:
            to_read.append(blob_id)
    for blob_id, text in read_blobs(repo_root, sorted(set(to_read))).items():
        _measured[(blob_id, metric_fn)] = metric_fn(text)
    total = 0
    for path in paths:
        blob_id = blob_ids.get(path)
        if path.startswith('"'):
            total += metric_fn(None)
        elif blob_id and (blob_id, metric_fn) in _measured:
            total += _measured[(blob_id, metric_fn)]
        else:
            total += metric_fn(git_show(repo_root, rev, path))
    return total
