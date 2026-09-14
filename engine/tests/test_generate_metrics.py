import contextlib
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from engine.generate_metrics import bootstrap_if_missing, build_milestones, main
from engine.prices import append_entry
from engine.readers.claude_code import encode_project_path

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

    def test_dashboard_html_includes_a_subject_column_header(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            self.make_repo(tmpdir)
            self.write_config(tmpdir)
            main(repo_root=tmpdir, claude_projects_dir="/no/such/dir")
            with open(os.path.join(tmpdir, "logbook", "dashboard.html"), encoding="utf-8") as fh:
                html_out = fh.read()
            self.assertIn("<th>Subject</th>", html_out)

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


class TestConfigurableMilestoneFolder(unittest.TestCase):
    # Fix D: milestone_folder is not actually configurable unless the
    # engine creates it, and the self-exclusion fix must follow
    # whatever the folder is renamed to, not stay pinned to "logbook/".
    def test_main_creates_the_configured_milestone_folder_when_missing(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            run(tmpdir, "init")
            run(tmpdir, "config", "user.email", "test@example.com")
            run(tmpdir, "config", "user.name", "Test")
            with open(os.path.join(tmpdir, "README.md"), "w", encoding="utf-8") as fh:
                fh.write("hello world")
            run(tmpdir, "add", ".")
            run(tmpdir, "commit", "-m", "First milestone")

            bootstrap_if_missing(tmpdir, "logbook")
            config_path = os.path.join(tmpdir, "logbook", "config.json")
            with open(config_path, "w", encoding="utf-8") as fh:
                json.dump({
                    "milestone_folder": "reports/output",
                    "content_globs": ["*.md"],
                    "code_globs": [],
                    "exclude_globs": ["logbook/"],
                    "transcript_reader": "claude_code",
                    "price_provider": "anthropic",
                    "price_model": "claude-sonnet-5",
                    "currency": "USD",
                }, fh)

            output_dir = os.path.join(tmpdir, "reports", "output")
            self.assertFalse(os.path.isdir(output_dir))
            main(repo_root=tmpdir, claude_projects_dir="/no/such/dir")
            self.assertTrue(os.path.isdir(output_dir))
            self.assertTrue(os.path.isfile(os.path.join(output_dir, "dashboard.html")))
            self.assertTrue(os.path.isfile(os.path.join(output_dir, "data.json")))

    def test_configured_milestone_folder_is_always_self_excluded(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            run(tmpdir, "init")
            run(tmpdir, "config", "user.email", "test@example.com")
            run(tmpdir, "config", "user.name", "Test")
            with open(os.path.join(tmpdir, "README.md"), "w", encoding="utf-8") as fh:
                fh.write("hello world from the first milestone")
            # A previously generated dashboard, already tracked, living
            # under a renamed milestone_folder that exclude_globs
            # (deliberately left at the historical "logbook/" default)
            # never mentions.
            os.makedirs(os.path.join(tmpdir, "reports"))
            with open(os.path.join(tmpdir, "reports", "dashboard.html"), "w", encoding="utf-8") as fh:
                fh.write("<html><body>" + " ".join(["word"] * 50) + "</body></html>")
            run(tmpdir, "add", ".")
            run(tmpdir, "commit", "-m", "First milestone")

            os.makedirs(os.path.join(tmpdir, "logbook"))
            config_path = os.path.join(tmpdir, "logbook", "config.json")
            with open(config_path, "w", encoding="utf-8") as fh:
                json.dump({
                    "milestone_folder": "reports",
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
            # Only README.md's 6 words should count. reports/dashboard.html
            # matches content_globs' "*.html" but lives under the
            # milestone_folder itself, which must always be excluded
            # even though exclude_globs never names it.
            self.assertEqual(milestones[0]["words_delta"], 6)


class TestCostFreeze(unittest.TestCase):
    # Fix E: cost_recorded, once written, must never be recalculated,
    # even when a same-day price correction is appended later and
    # would win price_at's later-entry tie-break on a fresh run.
    def test_cost_recorded_never_changes_once_written(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            run(tmpdir, "init")
            run(tmpdir, "config", "user.email", "test@example.com")
            run(tmpdir, "config", "user.name", "Test")
            with open(os.path.join(tmpdir, "README.md"), "w", encoding="utf-8") as fh:
                fh.write("hello world from the only milestone")
            run(tmpdir, "add", ".")
            run(tmpdir, "commit", "-m", "Only milestone")

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

            from engine.git_source import commits
            first_commit = commits(tmpdir)[0]
            commit_date = first_commit["iso"][:10]

            projects_dir = os.path.join(tmpdir, "claude_projects")
            project_dir = os.path.join(projects_dir, encode_project_path(tmpdir))
            os.makedirs(project_dir)
            with open(os.path.join(project_dir, "session.jsonl"), "w", encoding="utf-8") as fh:
                fh.write(json.dumps({
                    "timestamp": first_commit["iso"],
                    "message": {
                        "id": "msg_1",
                        "usage": {
                            "input_tokens": 1000, "output_tokens": 500,
                            "cache_read_input_tokens": 0, "cache_creation_input_tokens": 0,
                        },
                    },
                }) + "\n")

            # An isolated price ledger: this test must never touch the
            # real, shipped engine/prices.json.
            ledger_path = os.path.join(tmpdir, "prices.json")
            with open(ledger_path, "w", encoding="utf-8") as fh:
                json.dump([{
                    "provider": "anthropic", "model": "claude-sonnet-5", "currency": "USD",
                    "entries": [{
                        "effective_date": commit_date, "input_price": 3.0, "output_price": 15.0,
                        "cache_read_price": 0.3, "cache_creation_price": 3.75, "sources": [],
                    }],
                }], fh)

            with patch("engine.generate_metrics.SHIPPED_PRICES", ledger_path):
                main(repo_root=tmpdir, claude_projects_dir=projects_dir)
                with open(os.path.join(tmpdir, "logbook", "data.json"), encoding="utf-8") as fh:
                    first_run = json.load(fh)
                first_cost = first_run["milestones"][0]["cost_recorded"]
                self.assertIsNotNone(first_cost)

                # A same-day price correction, appended later, wins
                # price_at's tie-break and would change what a fresh
                # computation produces, if the freeze were not real.
                append_entry(
                    ledger_path, "anthropic", "claude-sonnet-5", "USD", commit_date,
                    {"input_price": 999.0, "output_price": 999.0,
                     "cache_read_price": 999.0, "cache_creation_price": 999.0},
                    [{"name": "correction", "url": "u", "checked_at": commit_date}],
                )

                main(repo_root=tmpdir, claude_projects_dir=projects_dir)
                with open(os.path.join(tmpdir, "logbook", "data.json"), encoding="utf-8") as fh:
                    second_run = json.load(fh)
                second_cost = second_run["milestones"][0]["cost_recorded"]

            self.assertEqual(first_cost, second_cost)


class TestLinkedProjects(unittest.TestCase):
    # This repository's own real situation, generalised: a project
    # built by subagents dispatched from a different top-level
    # session's own project folder, so the ordinary exact-match lookup
    # (scoped to this repository's own path) never finds their
    # transcripts on its own.
    def make_repo_with_config(self, tmpdir):
        run(tmpdir, "init")
        run(tmpdir, "config", "user.email", "test@example.com")
        run(tmpdir, "config", "user.name", "Test")
        with open(os.path.join(tmpdir, "README.md"), "w", encoding="utf-8") as fh:
            fh.write("hello world from the only milestone")
        run(tmpdir, "add", ".")
        run(tmpdir, "commit", "-m", "Only milestone")

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

    def write_linked_subagent_transcript(self, projects_dir, linked_project_root, target_repo_root, ts, usage):
        linked_dir = os.path.join(projects_dir, encode_project_path(linked_project_root))
        subagents_dir = os.path.join(linked_dir, "session-uuid", "subagents")
        os.makedirs(subagents_dir, exist_ok=True)
        with open(os.path.join(subagents_dir, "agent-1.jsonl"), "w", encoding="utf-8") as fh:
            fh.write(json.dumps({
                "timestamp": ts,
                "message": {
                    "id": "msg_1",
                    "content": "Implementing a task under %s" % target_repo_root,
                    "usage": usage,
                },
            }) + "\n")

    def test_build_milestones_includes_tokens_from_a_linked_projects_subagent_transcript(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            self.make_repo_with_config(tmpdir)
            from engine.config import load_config
            from engine.git_source import commits
            config = load_config(os.path.join(tmpdir, "logbook", "config.json"))
            commit_iso = commits(tmpdir)[0]["iso"]

            projects_dir = os.path.join(tmpdir, "claude_projects")
            linked_project_root = os.path.join(tmpdir, "sibling-repo")
            os.makedirs(linked_project_root)
            self.write_linked_subagent_transcript(
                projects_dir, linked_project_root, tmpdir, commit_iso,
                {"input_tokens": 1000, "output_tokens": 500,
                 "cache_read_input_tokens": 0, "cache_creation_input_tokens": 0},
            )

            without_link = build_milestones(tmpdir, config, projects_dir, linked_projects=None)
            self.assertEqual(without_link[0]["tokens"]["input"], 0)

            with_link = build_milestones(tmpdir, config, projects_dir, linked_projects=[linked_project_root])
            self.assertEqual(with_link[0]["tokens"]["input"], 1000)
            self.assertEqual(with_link[0]["tokens"]["output"], 500)

    def test_a_linked_projects_unrelated_subagent_transcript_is_ignored(self):
        # other_target must not share tmpdir as a path prefix: nested
        # under it (like "tmpdir/some-other-repo") would make tmpdir's
        # own path a genuine substring of other_target's, which is not
        # the scenario this test means to exercise.
        with tempfile.TemporaryDirectory() as tmpdir, tempfile.TemporaryDirectory() as other_target:
            self.make_repo_with_config(tmpdir)
            from engine.config import load_config
            from engine.git_source import commits
            config = load_config(os.path.join(tmpdir, "logbook", "config.json"))
            commit_iso = commits(tmpdir)[0]["iso"]

            projects_dir = os.path.join(tmpdir, "claude_projects")
            linked_project_root = os.path.join(tmpdir, "sibling-repo")
            os.makedirs(linked_project_root)
            self.write_linked_subagent_transcript(
                projects_dir, linked_project_root, other_target, commit_iso,
                {"input_tokens": 1000, "output_tokens": 500,
                 "cache_read_input_tokens": 0, "cache_creation_input_tokens": 0},
            )

            milestones = build_milestones(tmpdir, config, projects_dir, linked_projects=[linked_project_root])
            self.assertEqual(milestones[0]["tokens"]["input"], 0)

    def test_tokens_freeze_alongside_cost_once_recorded(self):
        # A milestone's recorded cost, once written, is frozen. Its
        # tokens must stay consistent with that frozen cost too: a
        # later run that omits --linked-project must not silently
        # revert this milestone's tokens to zero while its cost stays
        # frozen at the non-zero amount those tokens produced.
        with tempfile.TemporaryDirectory() as tmpdir:
            self.make_repo_with_config(tmpdir)
            from engine.git_source import commits
            commit_iso = commits(tmpdir)[0]["iso"]
            commit_date = commit_iso[:10]

            projects_dir = os.path.join(tmpdir, "claude_projects")
            linked_project_root = os.path.join(tmpdir, "sibling-repo")
            os.makedirs(linked_project_root)
            self.write_linked_subagent_transcript(
                projects_dir, linked_project_root, tmpdir, commit_iso,
                {"input_tokens": 1000, "output_tokens": 500,
                 "cache_read_input_tokens": 0, "cache_creation_input_tokens": 0},
            )

            ledger_path = os.path.join(tmpdir, "prices.json")
            with open(ledger_path, "w", encoding="utf-8") as fh:
                json.dump([{
                    "provider": "anthropic", "model": "claude-sonnet-5", "currency": "USD",
                    "entries": [{
                        "effective_date": commit_date, "input_price": 3.0, "output_price": 15.0,
                        "cache_read_price": 0.3, "cache_creation_price": 3.75, "sources": [],
                    }],
                }], fh)

            with patch("engine.generate_metrics.SHIPPED_PRICES", ledger_path):
                main(repo_root=tmpdir, claude_projects_dir=projects_dir, linked_projects=[linked_project_root])
                with open(os.path.join(tmpdir, "logbook", "data.json"), encoding="utf-8") as fh:
                    first_run = json.load(fh)
                self.assertIsNotNone(first_run["milestones"][0]["cost_recorded"])
                self.assertEqual(first_run["milestones"][0]["tokens"]["input"], 1000)

                # Second run, --linked-project not repeated this time.
                main(repo_root=tmpdir, claude_projects_dir=projects_dir, linked_projects=None)
                with open(os.path.join(tmpdir, "logbook", "data.json"), encoding="utf-8") as fh:
                    second_run = json.load(fh)

            self.assertEqual(second_run["milestones"][0]["cost_recorded"], first_run["milestones"][0]["cost_recorded"])
            self.assertEqual(second_run["milestones"][0]["tokens"], first_run["milestones"][0]["tokens"])

    def test_cli_accepts_repeated_linked_project_flags(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            self.make_repo_with_config(tmpdir)
            result = subprocess.run(
                [
                    sys.executable, GENERATE_METRICS_PATH, "--repo", tmpdir,
                    "--claude-projects-dir", "/no/such/dir",
                    "--linked-project", os.path.join(tmpdir, "sibling-a"),
                    "--linked-project", os.path.join(tmpdir, "sibling-b"),
                ],
                cwd=tempfile.gettempdir(),
                capture_output=True,
                text=True,
            )
            self.assertEqual(result.returncode, 0, msg=result.stderr)


class TestWarnings(unittest.TestCase):
    # Fix F: silent failure on no commits or no token usage.
    def test_zero_commits_warns_on_stderr_and_still_writes_valid_output(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            run(tmpdir, "init")
            run(tmpdir, "config", "user.email", "test@example.com")
            run(tmpdir, "config", "user.name", "Test")
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

            stderr = io.StringIO()
            with contextlib.redirect_stderr(stderr):
                main(repo_root=tmpdir, claude_projects_dir="/no/such/dir")

            self.assertIn("no commits found", stderr.getvalue())
            data_path = os.path.join(tmpdir, "logbook", "data.json")
            self.assertTrue(os.path.isfile(data_path))
            with open(data_path, encoding="utf-8") as fh:
                data = json.load(fh)
            self.assertEqual(data["milestones"], [])
            self.assertTrue(os.path.isfile(os.path.join(tmpdir, "logbook", "dashboard.html")))

    def test_zero_tokens_warns_on_stdout_and_the_dashboard_shows_a_visible_note(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            run(tmpdir, "init")
            run(tmpdir, "config", "user.email", "test@example.com")
            run(tmpdir, "config", "user.name", "Test")
            with open(os.path.join(tmpdir, "README.md"), "w", encoding="utf-8") as fh:
                fh.write("hello world")
            run(tmpdir, "add", ".")
            run(tmpdir, "commit", "-m", "First milestone")
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

            stdout = io.StringIO()
            with contextlib.redirect_stdout(stdout):
                main(repo_root=tmpdir, claude_projects_dir="/no/such/dir")

            self.assertIn("no LLM session transcripts were found", stdout.getvalue())
            with open(os.path.join(tmpdir, "logbook", "dashboard.html"), encoding="utf-8") as fh:
                html_out = fh.read()
            self.assertIn("No LLM session transcripts were found", html_out)


class TestTableRendering(unittest.TestCase):
    # Fix G: a Subject column, and every interpolated field HTML-escaped.
    def test_render_table_rows_includes_and_escapes_the_subject_and_note(self):
        from engine.generate_metrics import render_table_rows
        milestones = [{
            "date": "2026-09-13",
            "commit": "abc1234",
            "subject": "Fix <script>alert(1)</script> & tidy up",
            "words_delta": 10,
            "loc_delta": 5,
            "tokens": {"input": 0, "output": 0, "cache_read": 0, "cache_creation": 0},
            "cost_recorded": None,
            "note": "See <b>details</b>",
        }]
        rows_html = render_table_rows(milestones)
        self.assertNotIn("<script>alert(1)</script>", rows_html)
        self.assertIn("&lt;script&gt;alert(1)&lt;/script&gt;", rows_html)
        self.assertIn("&amp;", rows_html)
        self.assertIn("&lt;b&gt;details&lt;/b&gt;", rows_html)
        self.assertIn(
            "<td>Fix &lt;script&gt;alert(1)&lt;/script&gt; &amp; tidy up</td>", rows_html
        )


if __name__ == "__main__":
    unittest.main()
