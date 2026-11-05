import os
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from engine import git_source
from engine.git_source import (
    commits,
    git_show,
    line_count,
    list_repo_files_at,
    matches_any,
    read_blobs,
    sum_metric,
    word_count_html,
)


def run(cwd, *args):
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True)


class TestGitSource(unittest.TestCase):
    def make_repo(self, tmpdir):
        run(tmpdir, "init")
        run(tmpdir, "config", "user.email", "test@example.com")
        run(tmpdir, "config", "user.name", "Test")
        with open(os.path.join(tmpdir, "index.html"), "w", encoding="utf-8") as fh:
            fh.write("<p>hello world</p>")
        with open(os.path.join(tmpdir, "engine.py"), "w", encoding="utf-8") as fh:
            fh.write("line one\nline two\nline three\n")
        run(tmpdir, "add", ".")
        run(tmpdir, "commit", "-m", "First milestone")
        with open(os.path.join(tmpdir, "index.html"), "w", encoding="utf-8") as fh:
            fh.write("<p>hello world again and again</p>")
        run(tmpdir, "add", ".")
        run(tmpdir, "commit", "-m", "Second milestone")

    def test_commits_lists_both_in_order(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            self.make_repo(tmpdir)
            rows = commits(tmpdir)
            self.assertEqual(len(rows), 2)
            self.assertEqual(rows[0]["subject"], "First milestone")
            self.assertEqual(rows[1]["subject"], "Second milestone")

    def test_git_show_reads_file_at_revision(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            self.make_repo(tmpdir)
            rows = commits(tmpdir)
            content = git_show(tmpdir, rows[0]["hash"], "index.html")
            self.assertEqual(content, "<p>hello world</p>")

    def test_git_show_missing_file_returns_none(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            self.make_repo(tmpdir)
            rows = commits(tmpdir)
            self.assertIsNone(git_show(tmpdir, rows[0]["hash"], "does-not-exist.md"))

    def test_word_count_html_strips_tags(self):
        self.assertEqual(word_count_html("<p>hello world</p>"), 2)

    def test_line_count(self):
        self.assertEqual(line_count("a\nb\nc\n"), 3)

    def test_matches_any_prefix_and_wildcard(self):
        self.assertTrue(matches_any("engine/config.py", ["engine/"]))
        self.assertTrue(matches_any("README.md", ["*.md"]))
        self.assertFalse(matches_any("template/dashboard.html", ["engine/"]))

    def test_sum_metric_over_globs(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            self.make_repo(tmpdir)
            rows = commits(tmpdir)
            files = list_repo_files_at(tmpdir, rows[1]["hash"])
            total = sum_metric(tmpdir, rows[1]["hash"], files, ["engine.py"], line_count)
            self.assertEqual(total, 3)


def write(tmpdir, rel, data):
    path = os.path.join(tmpdir, rel)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as fh:
        fh.write(data)


class TestBatchedReads(unittest.TestCase):
    """sum_metric reads through one `git cat-file --batch` per call and
    measures each blob once; its totals must equal the per-file git_show
    reading it replaced, on every commit."""

    def setUp(self):
        git_source._measured.clear()

    def make_history(self, tmpdir):
        run(tmpdir, "init")
        run(tmpdir, "config", "user.email", "test@example.com")
        run(tmpdir, "config", "user.name", "Test")
        run(tmpdir, "config", "core.autocrlf", "false")
        write(tmpdir, "index.html", b"<p>one two three</p>\n")
        write(tmpdir, "docs/crlf.md", b"line one\r\nline two\r\nlone\rcarriage\r\n")
        write(tmpdir, "docs/a\xc3\xa7\xc3\xa3o.md", "acentuação e ç".encode("utf-8"))
        write(tmpdir, "engine/a.py", b"x = 1\ny = 2\n")
        write(tmpdir, "engine/bad.py", b"ok\n\xff\xfe broken bytes\n")
        run(tmpdir, "add", ".")
        run(tmpdir, "commit", "-m", "first")
        write(tmpdir, "index.html", b"<p>one two three four</p>\n<script>var a;</script>\n")
        write(tmpdir, "engine/b.py", b"z = 3\n")
        run(tmpdir, "add", ".")
        run(tmpdir, "commit", "-m", "second")
        run(tmpdir, "rm", "-q", "engine/a.py")
        run(tmpdir, "commit", "-m", "third")

    def reference(self, tmpdir, rev, files, globs, metric_fn):
        return sum(
            metric_fn(git_show(tmpdir, rev, path))
            for path in sorted(files)
            if matches_any(path, globs)
        )

    def test_totals_match_per_file_git_show_on_every_commit(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            self.make_history(tmpdir)
            for row in commits(tmpdir):
                files = list_repo_files_at(tmpdir, row["hash"])
                for globs, metric_fn in (
                    (["*.md", "*.html"], word_count_html),
                    (["engine/"], line_count),
                    (["*.md", "*.html", "engine/"], line_count),
                ):
                    self.assertEqual(
                        sum_metric(tmpdir, row["hash"], files, globs, metric_fn),
                        self.reference(tmpdir, row["hash"], files, globs, metric_fn),
                        (row["subject"], globs, metric_fn.__name__),
                    )

    def test_read_blobs_returns_what_git_show_returns(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            self.make_history(tmpdir)
            rev = commits(tmpdir)[0]["hash"]
            ids = git_source._blob_ids_at(tmpdir, rev)
            wanted = {p: ids[p] for p in ("index.html", "docs/crlf.md", "engine/bad.py")}
            texts = read_blobs(tmpdir, list(wanted.values()))
            for path, blob_id in wanted.items():
                self.assertEqual(texts[blob_id], git_show(tmpdir, rev, path), path)

    def test_unchanged_file_is_read_once_across_commits(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            self.make_history(tmpdir)
            requested = []
            real = git_source.read_blobs

            def spy(repo_root, blob_ids):
                requested.extend(blob_ids)
                return real(repo_root, blob_ids)

            with patch.object(git_source, "read_blobs", spy):
                for row in commits(tmpdir):
                    files = list_repo_files_at(tmpdir, row["hash"])
                    sum_metric(tmpdir, row["hash"], files, ["*.md", "*.html", "engine/"], line_count)
            self.assertEqual(len(requested), len(set(requested)))
            # first commit: four readable blobs (the non-ASCII path is
            # quoted by git and, as before, never read); second: two new
            # blobs; third: a deletion only, nothing new to read
            self.assertEqual(len(requested), 6)

    def test_batch_reads_gives_same_totals_and_releases_the_folder(self):
        # TemporaryDirectory's own cleanup is part of the test: on Windows
        # it fails if the cat-file process were still alive in the folder.
        with tempfile.TemporaryDirectory() as tmpdir:
            self.make_history(tmpdir)
            rows = commits(tmpdir)
            globs = ["*.md", "*.html", "engine/"]
            expected = []
            for row in rows:
                files = list_repo_files_at(tmpdir, row["hash"])
                expected.append(self.reference(tmpdir, row["hash"], files, globs, line_count))
            with git_source.batch_reads(tmpdir):
                got = [
                    sum_metric(tmpdir, row["hash"], list_repo_files_at(tmpdir, row["hash"]), globs, line_count)
                    for row in rows
                ]
            self.assertEqual(got, expected)
            self.assertNotIn(tmpdir, git_source._open_readers)

    def test_list_repo_files_at_matches_ls_tree_name_only(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            self.make_history(tmpdir)
            for row in commits(tmpdir):
                out = subprocess.run(
                    ["git", "ls-tree", "-r", "--name-only", row["hash"]],
                    cwd=tmpdir, capture_output=True, text=True, encoding="utf-8",
                ).stdout
                self.assertEqual(
                    list_repo_files_at(tmpdir, row["hash"]),
                    {line for line in out.splitlines() if line.strip()},
                )

    def test_read_blobs_with_no_ids_starts_no_process(self):
        with patch.object(git_source.subprocess, "run") as fake_run:
            self.assertEqual(read_blobs(".", []), {})
            fake_run.assert_not_called()


if __name__ == "__main__":
    unittest.main()
