"""Reading the ledger at a coarser grain: the day a commit counts under
(config day_timezone), the per commit / per day / per milestone charts,
and the Named milestones table shown only when the config names some."""

import json
import os
import subprocess
import tempfile
import unittest
from datetime import timedelta, timezone

from engine.config import day_timezone
from engine.generate_metrics import (
    _day_of, bootstrap_if_missing, build_grains, main, render_grain_charts, render_named_section,
)
from engine.rollup import group_by_day, group_by_named_milestone


def _commit(commit, date, words=0, tokens=0):
    return {
        "date": date, "commit": commit, "subject": "s", "words_delta": words, "loc_delta": 0,
        "tokens": {"input": tokens, "output": 0, "cache_read": 0, "cache_creation": 0},
        "cost_recorded": None, "note": None,
    }


LEDGER = [
    _commit("a000001", "2026-10-30", words=10, tokens=5),
    _commit("a000002", "2026-10-30", words=5, tokens=1),
    _commit("a000003", "2026-10-31", words=1, tokens=2),
]


class TestDayTimezoneConfig(unittest.TestCase):
    def test_absent_means_each_commits_own_date(self):
        self.assertIsNone(day_timezone({}))

    def test_utc_and_fixed_offsets(self):
        self.assertEqual(day_timezone({"day_timezone": "UTC"}), timezone.utc)
        self.assertEqual(day_timezone({"day_timezone": "-03:00"}), timezone(-timedelta(hours=3)))
        self.assertEqual(day_timezone({"day_timezone": "+05:30"}), timezone(timedelta(hours=5, minutes=30)))

    def test_anything_else_fails_loudly(self):
        for bad in ("", "Mars/Olympus", "+25:00", "-3", 3):
            with self.assertRaises(ValueError, msg=repr(bad)):
                day_timezone({"day_timezone": bad})


class TestDayOf(unittest.TestCase):
    def test_a_late_evening_commit_moves_to_the_next_day_in_utc(self):
        day_of = _day_of(timezone.utc, {"a000002": "2026-10-30T23:30:00-03:00"})
        self.assertEqual(day_of(LEDGER[1]), "2026-10-31")
        days = group_by_day(LEDGER, day_of)
        self.assertEqual([(d["date"], d["commits"]) for d in days], [("2026-10-30", 1), ("2026-10-31", 2)])

    def test_a_commit_with_no_known_timestamp_keeps_its_recorded_date(self):
        self.assertEqual(_day_of(timezone.utc, {})(LEDGER[0]), "2026-10-30")

    def test_without_a_timezone_there_is_no_override(self):
        self.assertIsNone(_day_of(None, {"a000001": "2026-10-30T23:30:00-03:00"}))


class TestGrains(unittest.TestCase):
    def test_each_commit_knows_its_group_at_every_grain(self):
        days = group_by_day(LEDGER)
        named = group_by_named_milestone(LEDGER, [{"name": "A long milestone name here", "until": "a000001"}])
        grains = build_grains(LEDGER, days, named)
        self.assertEqual(grains["commit"]["labels"], ["C1", "C2", "C3"])
        self.assertEqual(grains["day"], {"labels": ["10-30", "10-31"], "index": [0, 0, 1], "label_px": 34})
        self.assertEqual(grains["milestone"]["index"], [0, 1, 1])
        self.assertEqual(grains["milestone"]["labels"], ["A long milest…", "In progress"])

    def test_no_milestone_grain_without_named_milestones(self):
        self.assertNotIn("milestone", build_grains(LEDGER, group_by_day(LEDGER), []))

    def test_only_the_per_commit_chart_shows_until_the_switch_picks_another(self):
        grains = build_grains(LEDGER, group_by_day(LEDGER), [])
        html = render_grain_charts("w", grains, [m["words_delta"] for m in LEDGER], True, str, "Words per %s", "sub")
        self.assertIn('<div class="grain-chart" data-grain="commit">', html)
        self.assertIn('<div class="grain-chart" data-grain="day" hidden>', html)
        self.assertIn('aria-label="Words per day"', html)
        # Cumulative: the day chart ends where the commit chart ends.
        self.assertIn(">16<", html.split('data-grain="day"')[1])

    def test_a_milestone_label_is_escaped_in_the_chart(self):
        named = group_by_named_milestone(LEDGER, [{"name": "<b>x</b>", "until": "a000003"}])
        grains = build_grains(LEDGER, group_by_day(LEDGER), named)
        html = render_grain_charts("t", grains, [1, 2, 3], False, str, "Tokens per %s", "sub")
        self.assertNotIn("<b>x</b>", html)
        self.assertIn("&lt;b&gt;x&lt;/b&gt;", html)

    def test_the_named_milestones_section_is_left_out_when_none_is_named(self):
        self.assertEqual(render_named_section([], "2026-11-01"), "")
        named = group_by_named_milestone(LEDGER, [{"name": "One", "until": "a000003"}])
        section = render_named_section(named, "2026-11-01")
        self.assertIn("<h2>Named milestones</h2>", section)
        self.assertIn('data-group-grain="milestone" data-group-index="0"', section)
        self.assertIn("<td data-live-cost>-</td>", section)


class TestGrainsEndToEnd(unittest.TestCase):
    def make_repo(self, tmpdir, config_extra):
        def git(*args):
            return subprocess.run(["git", *args], cwd=tmpdir, check=True, capture_output=True, text=True).stdout
        git("init")
        git("config", "user.email", "test@example.com")
        git("config", "user.name", "Test")
        for i, stamp in enumerate(("2026-10-30T10:00:00-03:00", "2026-10-30T23:30:00-03:00")):
            with open(os.path.join(tmpdir, "README.md"), "w", encoding="utf-8") as fh:
                fh.write("word " * (i + 1))
            git("add", ".")
            subprocess.run(["git", "commit", "-m", "c%d" % i], cwd=tmpdir, check=True, capture_output=True,
                           env={**os.environ, "GIT_AUTHOR_DATE": stamp, "GIT_COMMITTER_DATE": stamp})
        bootstrap_if_missing(tmpdir, "logbook")
        config = {
            "milestone_folder": "logbook", "content_globs": ["*.md"], "code_globs": [],
            "exclude_globs": ["logbook/"], "transcript_reader": "claude_code",
            "price_provider": "anthropic", "price_model": "claude-sonnet-5", "currency": "USD",
            **config_extra,
        }
        with open(os.path.join(tmpdir, "logbook", "config.json"), "w", encoding="utf-8") as fh:
            json.dump(config, fh)
        return git("rev-list", "--max-parents=0", "HEAD").strip()

    def read(self, tmpdir, name):
        with open(os.path.join(tmpdir, "logbook", name), encoding="utf-8") as fh:
            return fh.read()

    def test_day_timezone_regroups_the_days_and_never_the_recorded_dates(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            self.make_repo(tmpdir, {"day_timezone": "UTC"})
            main(repo_root=tmpdir, claude_projects_dir="/no/such/dir")
            data = json.loads(self.read(tmpdir, "data.json"))
            self.assertEqual([m["date"] for m in data["milestones"]], ["2026-10-30", "2026-10-30"])
            self.assertEqual([d["date"] for d in data["days"]], ["2026-10-30", "2026-10-31"])
            self.assertEqual(data["day_timezone"], "UTC")
            self.assertEqual(len(data["commit_times"]), 2)

    def test_named_milestones_add_a_milestone_grain_and_their_table(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            first = self.make_repo(tmpdir, {})
            config_path = os.path.join(tmpdir, "logbook", "config.json")
            with open(config_path, encoding="utf-8") as fh:
                config = json.load(fh)
            config["milestones"] = [{"name": "a</script>b", "until": first}]
            with open(config_path, "w", encoding="utf-8") as fh:
                json.dump(config, fh)
            main(repo_root=tmpdir, claude_projects_dir="/no/such/dir")
            page = self.read(tmpdir, "dashboard.html")
            self.assertIn('data-grain-choice="milestone"', page)
            self.assertIn("<h2>Named milestones</h2>", page)
            self.assertIn('<span class="kpi-l">Commits</span>', page)
            # A name cannot close the script that embeds it.
            script = page.split("<script>window.MILESTONE_DATA")[1].split("</script>")[0]
            self.assertIn("a<\\/script>b", script)
            self.assertNotIn("day_timezone", json.loads(self.read(tmpdir, "data.json")))

    def test_without_named_milestones_there_is_no_milestone_table_or_grain(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            self.make_repo(tmpdir, {})
            main(repo_root=tmpdir, claude_projects_dir="/no/such/dir")
            page = self.read(tmpdir, "dashboard.html")
            self.assertNotIn("Named milestones", page)
            self.assertNotIn('data-grain-choice="milestone"', page)
            self.assertIn('data-grain-choice="day"', page)


if __name__ == "__main__":
    unittest.main()
