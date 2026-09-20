"""--enrich --dry-run: re-deriving the per-model and per-TTL token split for
milestones frozen before it existed, and reporting what would change,
without writing anything."""

import contextlib
import copy
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from engine.cost import price_milestone
from engine.enrich import format_report, needs_enrichment, plan_enrichment
from engine.generate_metrics import _load_frozen_data, main, read_token_buckets
from engine.git_source import commits
from engine.readers.claude_code import encode_project_path

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
GENERATE_METRICS_PATH = os.path.join(HERE, "generate_metrics.py")

DATE = "2026-09-13"
SOURCES = [{"name": "s", "url": "u", "checked_at": "2026-09-19"}]
SONNET = {"input_price": 2.0, "output_price": 10.0, "cache_read_price": 0.2, "cache_creation_price": 2.5,
          "cache_creation_1h_price": 4.0}
OPUS = {"input_price": 5.0, "output_price": 25.0, "cache_read_price": 0.5, "cache_creation_price": 6.25,
        "cache_creation_1h_price": 10.0}
CONFIG = {
    "milestone_folder": "logbook",
    "content_globs": ["*.md"],
    "code_globs": [],
    "exclude_globs": ["logbook/"],
    "transcript_reader": "claude_code",
    "price_provider": "anthropic",
    "price_model": "claude-sonnet-5",
    "currency": "USD",
}


def series(model, prices, date=DATE):
    return {
        "provider": "anthropic", "model": model, "currency": "USD",
        "entries": [{"effective_date": date, **prices, "sources": SOURCES}],
    }


LEDGER = [series("claude-sonnet-5", SONNET), series("claude-opus-5", OPUS)]


def pricer(ledger=LEDGER, mode="per_model"):
    """The real price_milestone, bound the way generate_metrics binds it."""
    def price(tokens, date):
        return price_milestone(tokens, ledger, "anthropic", "claude-sonnet-5", date, "USD", mode)
    return price


def counts(input=0, output=0, cache_read=0, cache_creation=0, cache_creation_1h=0):
    return {"input": input, "output": output, "cache_read": cache_read,
            "cache_creation": cache_creation, "cache_creation_1h": cache_creation_1h}


# One sonnet message (1M input, 1M cache writes of which 400k are 1-hour)
# and one opus message (1M output).
SONNET_MESSAGE = counts(input=1_000_000, cache_creation=1_000_000, cache_creation_1h=400_000)
OPUS_MESSAGE = counts(output=1_000_000)
BUCKET = {
    **counts(input=1_000_000, output=1_000_000, cache_creation=1_000_000, cache_creation_1h=400_000),
    "by_model": {"claude-sonnet-5": dict(SONNET_MESSAGE), "claude-opus-5": dict(OPUS_MESSAGE)},
}
LEGACY_TOKENS = {"input": 1_000_000, "output": 1_000_000, "cache_read": 0, "cache_creation": 1_000_000}
# What the current ledger says LEGACY_TOKENS cost with one series and every
# write at the 5-minute rate: 2.00 + 10.00 + 2.50.
LEGACY_COST = 14.5


def recorded(amount, date=DATE):
    return {"amount": amount, "currency": "USD", "priced_at": date,
            "provider": "anthropic", "model": "claude-sonnet-5"}


def legacy(commit="aaaaaaa", tokens=None, amount=LEGACY_COST, date=DATE, **extra):
    """A milestone the way an older engine froze it: four token fields, no
    by_model."""
    milestone = {
        "date": date, "commit": commit, "subject": "subject " + commit,
        "words_delta": 10, "loc_delta": 5,
        "tokens": dict(LEGACY_TOKENS) if tokens is None else tokens,
        "cost_recorded": recorded(amount, date) if amount is not None else None,
        "note": None,
    }
    milestone.update(extra)
    return milestone


def write_json(path, data):
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(data, fh)


def quiet_main(**kwargs):
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        code = main(**kwargs)
    return code, out.getvalue()


class TestNeedsEnrichment(unittest.TestCase):
    def test_a_recorded_milestone_without_the_split_is_a_candidate(self):
        self.assertTrue(needs_enrichment(legacy()))

    def test_a_milestone_with_no_recorded_cost_is_not(self):
        self.assertFalse(needs_enrichment(legacy(amount=None)))

    def test_a_milestone_that_already_has_by_model_is_not_even_when_it_is_empty(self):
        self.assertFalse(needs_enrichment(legacy(tokens={**LEGACY_TOKENS, "by_model": {}})))
        self.assertFalse(needs_enrichment(legacy(tokens=dict(BUCKET))))

    def test_a_milestone_whose_tokens_are_all_zero_is_not(self):
        zero = {"input": 0, "output": 0, "cache_read": 0, "cache_creation": 0}
        self.assertFalse(needs_enrichment(legacy(tokens=zero)))


class TestPlanEnrichment(unittest.TestCase):
    def plan_one(self, milestone=None, bucket=None, price=None):
        milestone = milestone or legacy()
        buckets = {milestone["commit"]: BUCKET if bucket is None else bucket}
        return plan_enrichment([milestone], buckets, price or pricer())[0]

    def test_a_match_is_enriched_and_the_four_legacy_counters_are_not_rewritten(self):
        result = self.plan_one()
        self.assertEqual(result["status"], "enrich")
        tokens = result["tokens"]
        for key in ("input", "output", "cache_read", "cache_creation"):
            self.assertEqual(tokens[key], LEGACY_TOKENS[key], msg=key)
        self.assertEqual(tokens["cache_creation_1h"], 400_000)
        self.assertEqual(sorted(tokens["by_model"]), ["claude-opus-5", "claude-sonnet-5"])
        self.assertEqual(
            list(tokens), ["input", "output", "cache_read", "cache_creation", "cache_creation_1h", "by_model"],
            "the same key order a freshly recorded milestone has",
        )

    def test_the_new_cost_is_priced_per_model_and_the_change_is_split_by_cause(self):
        result = self.plan_one()
        # sonnet: 2.00 + 600k x 2.50 + 400k x 4.00 = 5.10; opus: 1M x 25 = 25.00
        self.assertAlmostEqual(result["cost_recorded"]["amount"], 30.1, places=4)
        self.assertEqual(result["unpriced"], [])
        self.assertAlmostEqual(result["previous_amount"], LEGACY_COST, places=4)
        self.assertAlmostEqual(result["delta_one_hour"], 0.6, places=4)
        self.assertAlmostEqual(result["delta_model"], 15.0, places=4)
        self.assertAlmostEqual(
            result["delta_one_hour"] + result["delta_model"],
            result["cost_recorded"]["amount"] - result["previous_amount"], places=4,
        )

    def test_one_hour_writes_alone_move_the_cost_by_exactly_the_price_gap(self):
        tokens = {"input": 0, "output": 0, "cache_read": 0, "cache_creation": 1_000_000}
        only_one_hour = counts(cache_creation=1_000_000, cache_creation_1h=1_000_000)
        bucket = {**only_one_hour, "by_model": {"claude-sonnet-5": dict(only_one_hour)}}
        result = self.plan_one(legacy(tokens=tokens, amount=2.5), bucket)
        self.assertAlmostEqual(result["delta_one_hour"], 1_000_000 * (4.0 - 2.5) / 1e6, places=6)
        self.assertEqual(result["delta_model"], 0.0)
        self.assertAlmostEqual(result["cost_recorded"]["amount"], 4.0, places=4)

    def test_a_counter_that_differs_refuses_the_milestone_and_says_which(self):
        for counter in ("input", "output", "cache_read", "cache_creation"):
            with self.subTest(counter=counter):
                frozen = dict(LEGACY_TOKENS)
                frozen[counter] += 1
                result = self.plan_one(legacy(tokens=frozen))
                self.assertEqual(result["status"], "refused")
                self.assertEqual(result["reason"], "counters_differ")
                self.assertEqual(result["differences"], {counter: (frozen[counter], BUCKET[counter])})
                self.assertNotIn("tokens", result)

    def test_a_commit_that_is_not_in_the_history_any_more_is_refused(self):
        result = plan_enrichment([legacy(commit="zzzzzzz")], {"aaaaaaa": BUCKET}, pricer())[0]
        self.assertEqual(result["status"], "refused")
        self.assertEqual(result["reason"], "commit_not_in_history")

    def test_only_candidates_are_planned(self):
        milestones = [
            legacy("aaaaaaa"),
            legacy("bbbbbbb", amount=None),
            legacy("ccccccc", tokens={**LEGACY_TOKENS, "by_model": {}}),
        ]
        buckets = {m["commit"]: BUCKET for m in milestones}
        plan = plan_enrichment(milestones, buckets, pricer())
        self.assertEqual([r["commit"] for r in plan], ["aaaaaaa"])

    def test_planning_changes_neither_the_milestones_nor_the_buckets(self):
        milestones = [legacy("aaaaaaa"), legacy("bbbbbbb", tokens={**LEGACY_TOKENS, "input": 5})]
        buckets = {"aaaaaaa": copy.deepcopy(BUCKET), "bbbbbbb": copy.deepcopy(BUCKET)}
        before = copy.deepcopy((milestones, buckets))
        plan_enrichment(milestones, buckets, pricer())
        self.assertEqual((milestones, buckets), before)

    def test_a_model_with_no_series_stays_unpriced_and_the_milestone_becomes_partial(self):
        tokens = {"input": 1_001_000, "output": 0, "cache_read": 0, "cache_creation": 1_000_000}
        bucket = {
            **counts(input=1_001_000, cache_creation=1_000_000, cache_creation_1h=400_000),
            "by_model": {"claude-sonnet-5": dict(SONNET_MESSAGE), "claude-mystery-9": counts(input=1000)},
        }
        result = self.plan_one(legacy(tokens=tokens, amount=4.502), bucket)
        self.assertTrue(result["becomes_partial"])
        self.assertEqual([u["model"] for u in result["unpriced"]], ["claude-mystery-9"])
        self.assertAlmostEqual(result["cost_recorded"]["amount"], 5.1, places=4)

    def test_a_milestone_that_was_already_partial_does_not_count_as_becoming_partial(self):
        tokens = {"input": 1_001_000, "output": 0, "cache_read": 0, "cache_creation": 1_000_000}
        bucket = {
            **counts(input=1_001_000, cache_creation=1_000_000, cache_creation_1h=400_000),
            "by_model": {"claude-sonnet-5": dict(SONNET_MESSAGE), "claude-mystery-9": counts(input=1000)},
        }
        already = legacy(tokens=tokens, amount=4.502, unpriced=[{"model": "x", "tokens": 1, "reason": "r"}])
        self.assertFalse(self.plan_one(already, bucket)["becomes_partial"])

    def test_flat_mode_prices_one_series_so_only_the_cache_ttl_moves_the_cost(self):
        result = self.plan_one(price=pricer(mode="flat"))
        # 2.00 + 10.00 + 600k x 2.50 + 400k x 4.00
        self.assertAlmostEqual(result["cost_recorded"]["amount"], 15.1, places=4)
        self.assertAlmostEqual(result["delta_one_hour"], 0.6, places=4)
        self.assertEqual(result["delta_model"], 0.0)

    def test_a_recorded_cost_that_already_differs_from_the_ledger_is_flagged(self):
        self.assertFalse(self.plan_one()["ledger_drift"])
        self.assertTrue(self.plan_one(legacy(amount=18.0))["ledger_drift"])


class TestFormatReport(unittest.TestCase):
    def report(self, milestones, buckets):
        plan = plan_enrichment(milestones, buckets, pricer())
        return format_report(plan, len(milestones), "USD", 2, 3)

    def test_it_says_nothing_was_written_and_gives_the_totals_and_the_causes(self):
        text = self.report([legacy()], {"aaaaaaa": BUCKET})
        self.assertIn("nothing was written", text)
        self.assertIn("read 2 transcript files, 3 usage events", text)
        self.assertIn("would enrich: 1", text)
        self.assertIn("refused: 0", text)
        self.assertIn("14.5000 -> 30.1000 USD (+15.6000)", text)
        self.assertIn("1-hour cache writes: +0.6000", text)
        self.assertIn("model mix: +15.0000", text)
        self.assertIn("would become partial (some tokens unpriced): 0", text)

    def test_each_refused_milestone_shows_frozen_against_re_derived(self):
        off_by_one = legacy("bbbbbbb", tokens={**LEGACY_TOKENS, "input": 1_000_001})
        gone = legacy("ccccccc")
        text = self.report([legacy("aaaaaaa"), off_by_one, gone], {"aaaaaaa": BUCKET, "bbbbbbb": BUCKET})
        self.assertIn("would enrich: 1", text)
        self.assertIn("refused: 2", text)
        self.assertIn("bbbbbbb: input frozen 1,000,001, re-derived 1,000,000", text)
        self.assertIn("ccccccc: commit not found in this repository's git history", text)

    def test_it_warns_when_recorded_costs_already_differ_from_the_ledger(self):
        text = self.report([legacy(amount=18.0)], {"aaaaaaa": BUCKET})
        self.assertIn("run --reprice first", text)
        self.assertNotIn("run --reprice first", self.report([legacy()], {"aaaaaaa": BUCKET}))

    def test_with_no_candidates_it_says_so(self):
        text = self.report([legacy(amount=None)], {})
        self.assertIn("0 of 1 milestones", text)
        self.assertIn("would enrich: 0", text)
        self.assertNotIn("->", text)


class EnrichRepo(unittest.TestCase):
    """One real git repository with one commit, a logbook frozen the way an
    older engine wrote it, and transcripts under a separate projects dir."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = tmp.name
        self.repo = os.path.join(self.root, "repo")
        os.makedirs(self.repo)
        self.git("init")
        self.git("config", "user.email", "t@example.com")
        self.git("config", "user.name", "T")
        with open(os.path.join(self.repo, "README.md"), "w", encoding="utf-8") as fh:
            fh.write("hello world from the only milestone")
        self.git("add", "README.md")
        self.git("commit", "-m", "Only milestone")

        row = commits(self.repo)[0]
        self.iso = row["iso"]
        self.commit = row["hash"][:7]
        self.date = self.iso[:10]
        self.projects_dir = os.path.join(self.root, "claude_projects")
        self.own_dir = os.path.join(self.projects_dir, encode_project_path(self.repo))
        os.makedirs(self.own_dir)
        os.makedirs(os.path.join(self.repo, "logbook"))
        write_json(os.path.join(self.repo, "logbook", "config.json"), CONFIG)
        self.ledger_path = os.path.join(self.root, "prices.json")
        write_json(self.ledger_path, [series("claude-sonnet-5", SONNET, self.date),
                                      series("claude-opus-5", OPUS, self.date)])

    def git(self, *args):
        subprocess.run(["git", *args], cwd=self.repo, check=True, capture_output=True)

    def message(self, message_id, model, **usage):
        full = {"input_tokens": 0, "output_tokens": 0, "cache_read_input_tokens": 0,
                "cache_creation_input_tokens": 0, **usage}
        return {"timestamp": self.iso, "message": {"id": message_id, "model": model, "usage": full}}

    def sonnet_message(self):
        return self.message(
            "msg_1", "claude-sonnet-5", input_tokens=1_000_000, cache_creation_input_tokens=1_000_000,
            cache_creation={"ephemeral_5m_input_tokens": 600_000, "ephemeral_1h_input_tokens": 400_000},
        )

    def opus_message(self):
        return self.message("msg_2", "claude-opus-5", output_tokens=1_000_000)

    def write_rows(self, path, rows):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            for row in rows:
                fh.write(json.dumps(row) + "\n")

    def write_own_transcript(self, rows):
        self.write_rows(os.path.join(self.own_dir, "session.jsonl"), rows)

    def write_linked_subagent_transcript(self, linked_root, rows):
        """A subagent transcript filed under ANOTHER project's directory,
        that names this repository's own path."""
        directory = os.path.join(self.projects_dir, encode_project_path(linked_root), "parent-1", "subagents")
        marker = {"type": "user", "cwd": self.repo}
        self.write_rows(os.path.join(directory, "agent-1.jsonl"), [marker, *rows])

    def write_frozen(self, tokens=None, amount=LEGACY_COST):
        milestone = legacy(self.commit, tokens=tokens, amount=amount, date=self.date)
        write_json(os.path.join(self.repo, "logbook", "data.json"),
                   {"generated_at": "2026-09-14T13:32:47+00:00", "milestones": [milestone]})

    def snapshot(self):
        """Every file's bytes and mtime, except git's own bookkeeping."""
        state = {}
        for base in (self.repo, self.projects_dir):
            for folder, dirs, files in os.walk(base):
                dirs[:] = [d for d in dirs if d != ".git"]
                for name in files:
                    path = os.path.join(folder, name)
                    with open(path, "rb") as fh:
                        state[os.path.relpath(path, self.root)] = (fh.read(), os.stat(path).st_mtime_ns)
        return state

    def dry_run(self, **kwargs):
        with patch("engine.generate_metrics.SHIPPED_PRICES", self.ledger_path):
            return quiet_main(
                repo_root=self.repo, claude_projects_dir=self.projects_dir, enrich=True, dry_run=True, **kwargs
            )


class TestReadTokenBuckets(EnrichRepo):
    def test_it_returns_the_rows_the_buckets_and_what_it_read(self):
        self.write_own_transcript([self.sonnet_message(), self.opus_message()])
        rows, buckets, files, events = read_token_buckets(self.repo, self.projects_dir)
        self.assertEqual([r["hash"][:7] for r in rows], [self.commit])
        self.assertEqual(len(buckets), 1)
        self.assertEqual(
            {k: buckets[0][k] for k in ("input", "output", "cache_read", "cache_creation", "cache_creation_1h")},
            {k: BUCKET[k] for k in ("input", "output", "cache_read", "cache_creation", "cache_creation_1h")},
        )
        self.assertEqual(sorted(buckets[0]["by_model"]), ["claude-opus-5", "claude-sonnet-5"])
        self.assertEqual((files, events), (1, 2))


class TestEnrichDryRun(EnrichRepo):
    def test_a_match_is_reported_and_nothing_at_all_is_written(self):
        self.write_own_transcript([self.sonnet_message(), self.opus_message()])
        self.write_frozen()
        before = self.snapshot()
        code, out = self.dry_run()
        self.assertEqual(code, 0)
        self.assertIn("would enrich: 1", out)
        self.assertIn("14.5000 -> 30.1000 USD (+15.6000)", out)
        self.assertEqual(self.snapshot(), before, "bytes and mtimes of every file are unchanged")

    def test_a_counter_that_does_not_match_is_refused_and_the_exit_code_says_so(self):
        self.write_own_transcript([self.sonnet_message(), self.opus_message()])
        self.write_frozen(tokens={**LEGACY_TOKENS, "input": 1_000_001})
        before = self.snapshot()
        code, out = self.dry_run()
        self.assertEqual(code, 3)
        self.assertIn("refused: 1", out)
        self.assertIn("%s: input frozen 1,000,001, re-derived 1,000,000" % self.commit, out)
        self.assertEqual(self.snapshot(), before)

    def test_a_missing_transcript_refuses_instead_of_pricing_a_guess(self):
        self.write_frozen()
        code, out = self.dry_run()
        self.assertEqual(code, 3)
        self.assertIn("would enrich: 0", out)
        self.assertIn("input frozen 1,000,000, re-derived 0", out)

    def test_linked_project_widens_what_is_re_derived_and_only_when_named(self):
        linked_root = os.path.join(self.root, "sibling")
        self.write_own_transcript([self.sonnet_message()])
        self.write_linked_subagent_transcript(linked_root, [self.opus_message()])
        self.write_frozen()

        code, out = self.dry_run()
        self.assertEqual(code, 3, "without the flag the opus tokens are missing, so it refuses")
        self.assertIn("would enrich: 0", out)

        code, out = self.dry_run(linked_projects=[linked_root])
        self.assertEqual(code, 0)
        self.assertIn("would enrich: 1", out)
        self.assertIn("read 2 transcript files, 2 usage events", out)

    def test_without_a_data_json_it_stops_and_names_its_own_flag(self):
        self.write_own_transcript([self.sonnet_message()])
        with self.assertRaises(SystemExit) as raised:
            self.dry_run()
        self.assertIn("--enrich", str(raised.exception))
        self.assertNotIn("--reprice", str(raised.exception))

    def test_it_never_creates_a_logbook_folder(self):
        with tempfile.TemporaryDirectory() as bare:
            with self.assertRaises(FileNotFoundError):
                quiet_main(repo_root=bare, enrich=True, dry_run=True)
            self.assertEqual(os.listdir(bare), [])

    def test_enrich_without_dry_run_is_refused_for_now_and_touches_nothing(self):
        self.write_own_transcript([self.sonnet_message(), self.opus_message()])
        self.write_frozen()
        before = self.snapshot()
        with self.assertRaises(SystemExit) as raised:
            quiet_main(repo_root=self.repo, claude_projects_dir=self.projects_dir, enrich=True)
        self.assertIn("--dry-run", str(raised.exception))
        self.assertEqual(self.snapshot(), before)


class TestLoadFrozenDataNamesTheFlag(unittest.TestCase):
    def test_the_message_names_the_flag_it_was_called_for(self):
        with tempfile.TemporaryDirectory() as tmp:
            missing = os.path.join(tmp, "data.json")
            with self.assertRaises(SystemExit) as raised:
                _load_frozen_data(missing)
            self.assertIn("--reprice", str(raised.exception))
            with self.assertRaises(SystemExit) as raised:
                _load_frozen_data(missing, "--enrich")
            self.assertIn("--enrich", str(raised.exception))
            self.assertNotIn("--reprice", str(raised.exception))


class TestEnrichCommandLine(EnrichRepo):
    def run_script(self, *args):
        return subprocess.run(
            [sys.executable, GENERATE_METRICS_PATH, "--repo", self.repo, *args],
            cwd=tempfile.gettempdir(), capture_output=True, text=True,
        )

    def test_enrich_and_reprice_exclude_each_other(self):
        result = self.run_script("--enrich", "--dry-run", "--reprice")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("--enrich", result.stderr)
        self.assertIn("--reprice", result.stderr)

    def test_dry_run_alone_means_nothing_and_says_so(self):
        result = self.run_script("--dry-run")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("--dry-run", result.stderr)
        self.assertIn("--enrich", result.stderr)

    def test_as_a_direct_script_the_exit_code_is_zero_on_a_match_and_three_on_a_refusal(self):
        self.write_own_transcript([self.sonnet_message(), self.opus_message()])
        args = ["--enrich", "--dry-run", "--claude-projects-dir", self.projects_dir]

        self.write_frozen()
        ok = self.run_script(*args)
        self.assertEqual(ok.returncode, 0, msg=ok.stderr)
        self.assertIn("would enrich: 1", ok.stdout)

        self.write_frozen(tokens={**LEGACY_TOKENS, "output": 7})
        refused = self.run_script(*args)
        self.assertEqual(refused.returncode, 3, msg=refused.stderr)
        self.assertIn("refused: 1", refused.stdout)


if __name__ == "__main__":
    unittest.main()
