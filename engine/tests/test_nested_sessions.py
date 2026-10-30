"""The opt-in include_nested_sessions: sessions opened in a worktree or a
subfolder of the repository, each message counted only when its own cwd
is inside the repository."""

import json
import os
import tempfile
import unittest

from engine.config import include_nested_sessions, load_config
from engine.readers.claude_code import (
    encode_project_path,
    find_nested_project_session_jsonl,
    find_session_jsonl,
    load_usage_events,
    path_is_within,
)


def _assistant_row(message_id, ts, cwd, output=10):
    row = {
        "timestamp": ts,
        "message": {
            "id": message_id,
            "model": "claude-sonnet-5",
            "usage": {"input_tokens": 1, "output_tokens": output},
        },
    }
    if cwd is not None:
        row["cwd"] = cwd
    return row


def _write_jsonl(path, rows):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row) + "\n")


class TestPathIsWithin(unittest.TestCase):
    def test_the_root_itself_and_a_folder_inside_it(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            repo = os.path.join(tmpdir, "repo")
            self.assertTrue(path_is_within(repo, repo))
            self.assertTrue(path_is_within(os.path.join(repo, ".claude", "worktrees", "x"), repo))

    def test_a_sibling_with_a_longer_name_is_not_inside(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            repo = os.path.join(tmpdir, "repo-a")
            self.assertFalse(path_is_within(os.path.join(tmpdir, "repo-a-longer-name"), repo))
            self.assertFalse(path_is_within(tmpdir, repo))

    def test_missing_or_non_string_cwd_is_never_inside(self):
        self.assertFalse(path_is_within(None, "/repo"))
        self.assertFalse(path_is_within("", "/repo"))
        self.assertFalse(path_is_within(42, "/repo"))

    @unittest.skipUnless(os.name == "nt", "Git Bash and drive-letter forms only exist on Windows")
    def test_git_bash_and_native_forms_of_the_same_folder(self):
        repo = r"C:\Users\someone\Projects\repo"
        self.assertTrue(path_is_within("/c/Users/someone/Projects/repo/.claude/worktrees/x", repo))
        self.assertTrue(path_is_within(r"c:\Users\someone\Projects\repo\site", repo))
        self.assertTrue(path_is_within(r"C:\Users\someone\Projects\repo", repo))
        self.assertFalse(path_is_within("/c/Users/someone/Projects/other", repo))


class TestFindNestedProjectSessionJsonl(unittest.TestCase):
    def test_finds_worktree_and_subfolder_folders_but_not_the_exact_one(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            projects = os.path.join(tmpdir, "projects")
            repo = os.path.join(tmpdir, "repo")
            exact = os.path.join(projects, encode_project_path(repo))
            worktree = os.path.join(projects, encode_project_path(os.path.join(repo, ".claude", "worktrees", "w1")))
            subfolder = os.path.join(projects, encode_project_path(os.path.join(repo, "site", "page")))
            unrelated = os.path.join(projects, encode_project_path(os.path.join(tmpdir, "other")))
            for folder in (exact, worktree, subfolder, unrelated):
                _write_jsonl(os.path.join(folder, "s.jsonl"), [])
            _write_jsonl(os.path.join(worktree, "s", "subagents", "agent-1.jsonl"), [])

            found = find_nested_project_session_jsonl(projects, repo)
            folders = {os.path.dirname(p) for p in found}
            self.assertIn(worktree, folders)
            self.assertIn(subfolder, folders)
            self.assertNotIn(exact, folders)
            self.assertNotIn(unrelated, folders)
            self.assertTrue(any(p.endswith("agent-1.jsonl") for p in found))

    def test_drive_letter_case_of_the_folder_name_does_not_matter(self):
        # On Windows the same machine files c--Users-... and C--Users-...
        with tempfile.TemporaryDirectory() as tmpdir:
            projects = os.path.join(tmpdir, "projects")
            repo = os.path.join(tmpdir, "repo")
            name = encode_project_path(os.path.join(repo, ".claude", "worktrees", "w1"))
            folder = os.path.join(projects, name[:1].upper() + name[1:])
            _write_jsonl(os.path.join(folder, "s.jsonl"), [])
            self.assertEqual(len(find_nested_project_session_jsonl(projects, repo)), 1)

    def test_no_projects_dir_returns_empty_list(self):
        self.assertEqual(find_nested_project_session_jsonl("/no/such/dir", "/repo"), [])

    def test_exact_lookup_is_unchanged(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            projects = os.path.join(tmpdir, "projects")
            repo = os.path.join(tmpdir, "repo")
            worktree = os.path.join(projects, encode_project_path(os.path.join(repo, ".claude", "worktrees", "w1")))
            _write_jsonl(os.path.join(worktree, "s.jsonl"), [])
            self.assertEqual(find_session_jsonl(projects, repo), [])


class TestScopedUsageEvents(unittest.TestCase):
    def test_keeps_only_messages_whose_cwd_is_inside_the_repo(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            repo = os.path.join(tmpdir, "repo")
            other = os.path.join(tmpdir, "other-project")
            path = os.path.join(tmpdir, "nested.jsonl")
            _write_jsonl(path, [
                _assistant_row("m1", "2026-10-01T10:00:00Z", os.path.join(repo, ".claude", "worktrees", "w1")),
                _assistant_row("m2", "2026-10-01T10:01:00Z", other),
                _assistant_row("m3", "2026-10-01T10:02:00Z", None),
                _assistant_row("m4", "2026-10-01T10:03:00Z", os.path.join(repo, "site")),
            ])
            events = load_usage_events([path], scoped_paths=[path], scope_root=repo)
            self.assertEqual([e["ts"] for e in events], ["2026-10-01T10:00:00Z", "2026-10-01T10:03:00Z"])

    def test_unscoped_paths_are_read_whole_as_before(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            repo = os.path.join(tmpdir, "repo")
            path = os.path.join(tmpdir, "exact.jsonl")
            _write_jsonl(path, [
                _assistant_row("m1", "2026-10-01T10:00:00Z", os.path.join(tmpdir, "elsewhere")),
                _assistant_row("m2", "2026-10-01T10:01:00Z", None),
            ])
            self.assertEqual(len(load_usage_events([path])), 2)
            self.assertEqual(len(load_usage_events([path], scoped_paths=[], scope_root=repo)), 2)


class TestIncludeNestedSessionsConfig(unittest.TestCase):
    def _config(self, tmpdir, **extra):
        data = {
            "milestone_folder": "logbook", "content_globs": [], "code_globs": [], "exclude_globs": [],
            "transcript_reader": "claude_code", "price_provider": "anthropic",
            "price_model": "claude-sonnet-5", "currency": "USD",
        }
        data.update(extra)
        path = os.path.join(tmpdir, "config.json")
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(data, fh)
        return path

    def test_absent_key_means_false(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            self.assertFalse(include_nested_sessions(load_config(self._config(tmpdir))))

    def test_true_is_read_as_true(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            config = load_config(self._config(tmpdir, include_nested_sessions=True))
            self.assertTrue(include_nested_sessions(config))

    def test_anything_but_a_boolean_fails_loudly(self):
        for value in ("yes", 1, None, "true"):
            with tempfile.TemporaryDirectory() as tmpdir:
                with self.assertRaises(ValueError):
                    load_config(self._config(tmpdir, include_nested_sessions=value))


if __name__ == "__main__":
    unittest.main()
