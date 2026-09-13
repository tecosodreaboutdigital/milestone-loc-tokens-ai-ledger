import os
import subprocess
import tempfile
import unittest

from engine.git_source import (
    commits,
    git_show,
    line_count,
    list_repo_files_at,
    matches_any,
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


if __name__ == "__main__":
    unittest.main()
