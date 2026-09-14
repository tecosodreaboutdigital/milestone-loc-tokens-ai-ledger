import json
import os
import subprocess
import sys
import tempfile
import unittest

from engine.generate_metrics import bootstrap_if_missing, build_milestones, main

GENERATE_METRICS_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "generate_metrics.py"
)


def run(cwd, *args):
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True)


class TestBootstrap(unittest.TestCase):
    def test_creates_folder_and_copies_example_config_once(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            created = bootstrap_if_missing(tmpdir, "logbook")
            self.assertTrue(created)
            config_path = os.path.join(tmpdir, "logbook", "config.json")
            self.assertTrue(os.path.isfile(config_path))

            with open(config_path, "w", encoding="utf-8") as fh:
                json.dump({"edited": True}, fh)

            created_again = bootstrap_if_missing(tmpdir, "logbook")
            self.assertFalse(created_again)
            with open(config_path, encoding="utf-8") as fh:
                self.assertEqual(json.load(fh), {"edited": True})


class TestBuildAndGenerate(unittest.TestCase):
    def make_repo(self, tmpdir):
        run(tmpdir, "init")
        run(tmpdir, "config", "user.email", "test@example.com")
        run(tmpdir, "config", "user.name", "Test")
        with open(os.path.join(tmpdir, "README.md"), "w", encoding="utf-8") as fh:
            fh.write("hello world from the first milestone")
        run(tmpdir, "add", ".")
        run(tmpdir, "commit", "-m", "First milestone")
        with open(os.path.join(tmpdir, "README.md"), "w", encoding="utf-8") as fh:
            fh.write("hello world from the second milestone, now longer")
        run(tmpdir, "add", ".")
        run(tmpdir, "commit", "-m", "Second milestone")

    def write_config(self, tmpdir):
        bootstrap_if_missing(tmpdir, "logbook")
        config_path = os.path.join(tmpdir, "logbook", "config.json")
        with open(config_path, "w", encoding="utf-8") as fh:
            json.dump({
                "milestone_folder": "logbook",
                "content_globs": ["*.md"],
                "code_globs": [],
                "exclude_globs": ["logbook/"],
                "transcript_reader": "claude_code",
                "price_provider": "anthropic",
                "price_model": "claude-sonnet-5",
                "currency": "USD",
            }, fh)

    def test_build_milestones_counts_words_and_has_no_tokens_without_a_transcript(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            self.make_repo(tmpdir)
            self.write_config(tmpdir)
            from engine.config import load_config
            config = load_config(os.path.join(tmpdir, "logbook", "config.json"))
            milestones = build_milestones(tmpdir, config, claude_projects_dir="/no/such/dir")
            self.assertEqual(len(milestones), 2)
            self.assertEqual(milestones[0]["words_delta"], 6)
            self.assertEqual(milestones[0]["tokens"], {"input": 0, "output": 0, "cache_read": 0, "cache_creation": 0})
            self.assertIsNone(milestones[0]["cost_recorded"])

    def test_main_writes_data_json_and_dashboard_html(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            self.make_repo(tmpdir)
            self.write_config(tmpdir)
            main(repo_root=tmpdir, claude_projects_dir="/no/such/dir")
            data_path = os.path.join(tmpdir, "logbook", "data.json")
            dashboard_path = os.path.join(tmpdir, "logbook", "dashboard.html")
            self.assertTrue(os.path.isfile(data_path))
            self.assertTrue(os.path.isfile(dashboard_path))
            with open(data_path, encoding="utf-8") as fh:
                data = json.load(fh)
            self.assertEqual(len(data["milestones"]), 2)

    def test_running_main_twice_with_no_new_commit_is_idempotent(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            self.make_repo(tmpdir)
            self.write_config(tmpdir)
            main(repo_root=tmpdir, claude_projects_dir="/no/such/dir")
            with open(os.path.join(tmpdir, "logbook", "data.json"), encoding="utf-8") as fh:
                first = json.load(fh)
            main(repo_root=tmpdir, claude_projects_dir="/no/such/dir")
            with open(os.path.join(tmpdir, "logbook", "data.json"), encoding="utf-8") as fh:
                second = json.load(fh)
            self.assertEqual(first["milestones"], second["milestones"])

    def test_excluded_files_are_not_counted_in_words_delta(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            run(tmpdir, "init")
            run(tmpdir, "config", "user.email", "test@example.com")
            run(tmpdir, "config", "user.name", "Test")
            with open(os.path.join(tmpdir, "README.md"), "w", encoding="utf-8") as fh:
                fh.write("hello world from the first milestone")
            os.makedirs(os.path.join(tmpdir, "logbook"))
            with open(os.path.join(tmpdir, "logbook", "dashboard.html"), "w", encoding="utf-8") as fh:
                fh.write("<html><body>this is a previously rendered dashboard with many words</body></html>")
            run(tmpdir, "add", ".")
            run(tmpdir, "commit", "-m", "First milestone")

            config_path = os.path.join(tmpdir, "logbook", "config.json")
            with open(config_path, "w", encoding="utf-8") as fh:
                json.dump({
                    "milestone_folder": "logbook",
                    "content_globs": ["*.md", "*.html"],
                    "code_globs": [],
                    "exclude_globs": ["logbook/"],
                    "transcript_reader": "claude_code",
                    "price_provider": "anthropic",
                    "price_model": "claude-sonnet-5",
                    "currency": "USD",
                }, fh)
            from engine.config import load_config
            config = load_config(config_path)
            self.assertEqual(config["exclude_globs"], ["logbook/"])
            milestones = build_milestones(tmpdir, config, claude_projects_dir="/no/such/dir")
            # Only README.md's 6 words should count. logbook/dashboard.html
            # matches content_globs' "*.html" but lives under the excluded
            # "logbook/" prefix, so its own word count must not leak in.
            self.assertEqual(milestones[0]["words_delta"], 6)

    def test_notes_json_is_merged_in_when_present(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            self.make_repo(tmpdir)
            self.write_config(tmpdir)
            from engine.config import load_config
            config = load_config(os.path.join(tmpdir, "logbook", "config.json"))
            from engine.git_source import commits
            first_hash = commits(tmpdir)[0]["hash"][:7]
            with open(os.path.join(tmpdir, "logbook", "notes.json"), "w", encoding="utf-8") as fh:
                json.dump({first_hash: "Decided to start with the README only."}, fh)
            milestones = build_milestones(tmpdir, config, claude_projects_dir="/no/such/dir")
            self.assertEqual(milestones[0]["note"], "Decided to start with the README only.")
            self.assertIsNone(milestones[1]["note"])

    def test_dashboard_html_renders_a_separate_lines_chart(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            self.make_repo(tmpdir)
            self.write_config(tmpdir)
            main(repo_root=tmpdir, claude_projects_dir="/no/such/dir")
            with open(os.path.join(tmpdir, "logbook", "dashboard.html"), encoding="utf-8") as fh:
                html_out = fh.read()
            self.assertNotIn("__CHART_LOC__", html_out)
            self.assertIn('aria-label="Words per milestone"', html_out)
            self.assertIn('aria-label="Lines per milestone"', html_out)
            self.assertIn("<h2>Words</h2>", html_out)
            self.assertIn("<h2>Lines</h2>", html_out)

    def test_runs_as_a_direct_script_from_another_working_directory(self):
        # The documented usage is `python engine/generate_metrics.py
        # --repo <path>`, invoked directly rather than through
        # `python -m unittest`. That code path only puts this file's own
        # directory on sys.path, so `from engine.config import
        # load_config` and friends would fail with ModuleNotFoundError
        # unless the script bootstraps the repository root onto
        # sys.path itself. Running from tempfile.gettempdir() (never the
        # repository root) proves the fix does not depend on an
        # inherited working directory.
        with tempfile.TemporaryDirectory() as tmpdir:
            self.make_repo(tmpdir)
            self.write_config(tmpdir)
            result = subprocess.run(
                [sys.executable, GENERATE_METRICS_PATH, "--repo", tmpdir],
                cwd=tempfile.gettempdir(),
                capture_output=True,
                text=True,
            )
            self.assertEqual(result.returncode, 0, msg=result.stderr)
            self.assertNotIn("ModuleNotFoundError", result.stderr)


class TestDeltaSeries(unittest.TestCase):
    # Fix A: words_delta/loc_delta must be genuine per-milestone deltas,
    # not the cumulative running total sum_metric measures at each
    # commit. A repo whose content and code both grow unevenly across
    # three commits is the only way to tell a delta series apart from a
    # cumulative one: a cumulative series would repeat 3, 8, 8, not
    # 3, 5, 0.
    def test_words_and_loc_deltas_are_not_cumulative_totals(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            run(tmpdir, "init")
            run(tmpdir, "config", "user.email", "test@example.com")
            run(tmpdir, "config", "user.name", "Test")

            with open(os.path.join(tmpdir, "README.md"), "w", encoding="utf-8") as fh:
                fh.write("one two three")
            run(tmpdir, "add", ".")
            run(tmpdir, "commit", "-m", "First milestone")

            with open(os.path.join(tmpdir, "README.md"), "w", encoding="utf-8") as fh:
                fh.write("one two three four five six seven eight")
            with open(os.path.join(tmpdir, "code.py"), "w", encoding="utf-8") as fh:
                fh.write("line1\nline2\n")
            run(tmpdir, "add", ".")
            run(tmpdir, "commit", "-m", "Second milestone")

            with open(os.path.join(tmpdir, "code.py"), "w", encoding="utf-8") as fh:
                fh.write("line1\nline2\nline3\nline4\nline5\n")
            run(tmpdir, "add", ".")
            run(tmpdir, "commit", "-m", "Third milestone")

            bootstrap_if_missing(tmpdir, "logbook")
            config_path = os.path.join(tmpdir, "logbook", "config.json")
            with open(config_path, "w", encoding="utf-8") as fh:
                json.dump({
                    "milestone_folder": "logbook",
                    "content_globs": ["*.md"],
                    "code_globs": ["*.py"],
                    "exclude_globs": ["logbook/"],
                    "transcript_reader": "claude_code",
                    "price_provider": "anthropic",
                    "price_model": "claude-sonnet-5",
                    "currency": "USD",
                }, fh)
            from engine.config import load_config
            config = load_config(config_path)
            milestones = build_milestones(tmpdir, config, claude_projects_dir="/no/such/dir")

            self.assertEqual([m["words_delta"] for m in milestones], [3, 5, 0])
            self.assertEqual([m["loc_delta"] for m in milestones], [0, 2, 3])
            # The deltas must still telescope back to the true final total.
            self.assertEqual(sum(m["words_delta"] for m in milestones), 8)
            self.assertEqual(sum(m["loc_delta"] for m in milestones), 5)


if __name__ == "__main__":
    unittest.main()
