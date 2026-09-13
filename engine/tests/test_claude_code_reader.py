import json
import os
import tempfile
import unittest

from engine.readers.claude_code import (
    encode_project_path,
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
