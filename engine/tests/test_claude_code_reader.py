import json
import os
import tempfile
import unittest

from engine.readers.claude_code import (
    encode_project_path,
    find_linked_subagent_jsonl,
    find_nested_subagent_jsonl,
    find_session_jsonl,
    load_usage_events,
)


class TestEncodeProjectPath(unittest.TestCase):
    def test_matches_the_observed_windows_encoding(self):
        # This exact folder name was observed for real under
        # ~/.claude/projects/ for this repository's own sibling,
        # harness-medir, at C:\Users\teco.sodre\Dropbox\negocios
        # parcerias e clientes\AboutDigital\harness-medir. Reverse
        # engineered from that one real example, not from published
        # documentation: reconfirm if Claude Code changes the scheme.
        result = encode_project_path(
            r"C:\Users\teco.sodre\Dropbox\negocios parcerias e clientes\AboutDigital\harness-medir"
        )
        self.assertEqual(
            result,
            "c--Users-teco-sodre-Dropbox-negocios-parcerias-e-clientes-AboutDigital-harness-medir",
        )


class TestFindSessionJsonl(unittest.TestCase):
    def test_only_reads_the_exact_matching_project(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            projects_dir = os.path.join(tmpdir, "projects")
            repo_a = os.path.join(tmpdir, "repo-a")
            repo_b = os.path.join(tmpdir, "repo-a-longer-name")
            os.makedirs(repo_a)
            os.makedirs(repo_b)

            folder_a = os.path.join(projects_dir, encode_project_path(repo_a))
            folder_b = os.path.join(projects_dir, encode_project_path(repo_b))
            os.makedirs(folder_a)
            os.makedirs(folder_b)
            open(os.path.join(folder_a, "session.jsonl"), "w").close()
            open(os.path.join(folder_b, "session.jsonl"), "w").close()

            found = find_session_jsonl(projects_dir, repo_a)
            self.assertEqual(len(found), 1)
            self.assertEqual(os.path.dirname(found[0]), folder_a)

    def test_no_projects_dir_returns_empty_list(self):
        found = find_session_jsonl("/no/such/dir", "/repo")
        self.assertEqual(found, [])

    def test_also_finds_nested_subagent_transcripts(self):
        # A subagent dispatched via the Task tool gets its own JSONL
        # file, filed under its parent session's own directory as
        # <project_dir>/<session-uuid>/subagents/*.jsonl, never as a
        # top-level file next to the parent's own <uuid>.jsonl. Without
        # find_nested_subagent_jsonl, every token a subagent spent
        # would be silently missed, even inside this same, correctly
        # scoped project directory.
        with tempfile.TemporaryDirectory() as tmpdir:
            projects_dir = os.path.join(tmpdir, "projects")
            repo = os.path.join(tmpdir, "repo")
            os.makedirs(repo)
            project_dir = os.path.join(projects_dir, encode_project_path(repo))
            subagents_dir = os.path.join(project_dir, "session-uuid", "subagents")
            os.makedirs(subagents_dir)
            open(os.path.join(project_dir, "session-uuid.jsonl"), "w").close()
            open(os.path.join(subagents_dir, "agent-1.jsonl"), "w").close()

            found = find_session_jsonl(projects_dir, repo)
            self.assertEqual(len(found), 2)
            self.assertTrue(any(p.endswith("session-uuid.jsonl") for p in found))
            self.assertTrue(any(os.path.join("subagents", "agent-1.jsonl") in p for p in found))


class TestFindNestedSubagentJsonl(unittest.TestCase):
    def test_ignores_session_directories_with_no_subagents(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            os.makedirs(os.path.join(tmpdir, "memory"))
            self.assertEqual(find_nested_subagent_jsonl(tmpdir), [])

    def test_missing_project_dir_returns_empty_list(self):
        self.assertEqual(find_nested_subagent_jsonl("/no/such/dir"), [])


class TestFindLinkedSubagentJsonl(unittest.TestCase):
    def write_jsonl_containing(self, path, text):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(json.dumps({"note": text}) + "\n")

    def test_only_returns_subagent_transcripts_that_mention_the_target_repo(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            projects_dir = os.path.join(tmpdir, "projects")
            linked_project = os.path.join(tmpdir, "sibling-repo")
            target_repo = os.path.join(tmpdir, "this-repo")
            os.makedirs(linked_project)
            os.makedirs(target_repo)

            linked_dir = os.path.join(projects_dir, encode_project_path(linked_project))
            relevant = os.path.join(linked_dir, "session-uuid", "subagents", "agent-relevant.jsonl")
            unrelated = os.path.join(linked_dir, "session-uuid", "subagents", "agent-unrelated.jsonl")
            self.write_jsonl_containing(relevant, "Implementing a task under %s" % target_repo)
            self.write_jsonl_containing(unrelated, "Unrelated work in the linked project itself")

            found = find_linked_subagent_jsonl(projects_dir, linked_project, target_repo)
            self.assertEqual(found, [relevant])

    def test_never_returns_the_linked_projects_own_top_level_session_file(self):
        # The top-level file mixes together a whole session's unrelated
        # same-day work in the linked project and cannot be safely
        # scoped to just the target repository's own share of it, even
        # when it happens to mention the target repo's path too (it
        # dispatched the subagents that did).
        with tempfile.TemporaryDirectory() as tmpdir:
            projects_dir = os.path.join(tmpdir, "projects")
            linked_project = os.path.join(tmpdir, "sibling-repo")
            target_repo = os.path.join(tmpdir, "this-repo")
            os.makedirs(linked_project)
            os.makedirs(target_repo)

            linked_dir = os.path.join(projects_dir, encode_project_path(linked_project))
            top_level = os.path.join(linked_dir, "session-uuid.jsonl")
            self.write_jsonl_containing(top_level, "Dispatching a subagent for %s" % target_repo)

            found = find_linked_subagent_jsonl(projects_dir, linked_project, target_repo)
            self.assertEqual(found, [])

    def test_no_linked_project_dir_returns_empty_list(self):
        found = find_linked_subagent_jsonl("/no/such/dir", "/linked", "/target")
        self.assertEqual(found, [])


class TestLoadUsageEvents(unittest.TestCase):
    def write_jsonl(self, path, rows):
        with open(path, "w", encoding="utf-8") as fh:
            for row in rows:
                fh.write(json.dumps(row) + "\n")

    def test_dedups_by_message_id_keeping_last_occurrence(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = os.path.join(tmpdir, "session.jsonl")
            self.write_jsonl(path, [
                {
                    "timestamp": "2026-09-13T10:00:00.000Z",
                    "message": {"id": "msg_1", "usage": {"input_tokens": 100, "output_tokens": 10}},
                },
                {
                    "timestamp": "2026-09-13T10:00:00.500Z",
                    "message": {"id": "msg_1", "usage": {"input_tokens": 100, "output_tokens": 40}},
                },
            ])
            events = load_usage_events([path])
            self.assertEqual(len(events), 1)
            self.assertEqual(events[0]["output"], 40)

    def test_sorts_by_timestamp(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = os.path.join(tmpdir, "session.jsonl")
            self.write_jsonl(path, [
                {
                    "timestamp": "2026-09-13T10:05:00.000Z",
                    "message": {"id": "msg_2", "usage": {"input_tokens": 1, "output_tokens": 1}},
                },
                {
                    "timestamp": "2026-09-13T10:00:00.000Z",
                    "message": {"id": "msg_1", "usage": {"input_tokens": 2, "output_tokens": 2}},
                },
            ])
            events = load_usage_events([path])
            self.assertEqual([e["ts"] for e in events], [
                "2026-09-13T10:00:00.000Z", "2026-09-13T10:05:00.000Z",
            ])

    def test_lines_without_usage_are_ignored(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = os.path.join(tmpdir, "session.jsonl")
            self.write_jsonl(path, [
                {"timestamp": "2026-09-13T10:00:00.000Z", "message": {"id": "msg_1", "text": "no usage here"}},
            ])
            events = load_usage_events([path])
            self.assertEqual(events, [])


if __name__ == "__main__":
    unittest.main()
