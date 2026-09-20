"""Per-model tokens in milestones, the unpriced list, the explicit
--reprice path for a price that was wrong, and the dashboard pieces that
show them."""

import contextlib
import io
import json
import os
import re
import subprocess
import shutil
import sys
import tempfile
import unittest
from unittest.mock import patch

from engine.generate_metrics import (
    _bucket_tokens_by_commit,
    _freeze_previously_recorded_costs,
    bootstrap_if_missing,
    empty_tokens,
    main,
    render_table_rows,
    render_unpriced_section,
    reprice_milestones,
)
from engine.prices import append_entry
from engine.readers.claude_code import encode_project_path

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
GENERATE_METRICS_PATH = os.path.join(HERE, "generate_metrics.py")
TEMPLATE_DIR = os.path.join(os.path.dirname(HERE), "template")

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
SOURCES = [{"name": "s", "url": "u", "checked_at": "2026-09-19"}]
WRONG = {"input_price": 3.0, "output_price": 15.0, "cache_read_price": 0.3, "cache_creation_price": 3.75}
RIGHT = {"input_price": 2.0, "output_price": 10.0, "cache_read_price": 0.2, "cache_creation_price": 2.5,
         "cache_creation_1h_price": 4.0}
OPUS = {"input_price": 5.0, "output_price": 25.0, "cache_read_price": 0.5, "cache_creation_price": 6.25,
        "cache_creation_1h_price": 10.0}


@contextlib.contextmanager
def quiet_tmpdir():
    """A temporary directory whose cleanup never raises: on Windows a
    scanner can briefly hold a just-written file and make a strict
    rmtree fail with "directory not empty", which is noise, not a test
    result."""
    path = tempfile.mkdtemp()
    try:
        yield path
    finally:
        shutil.rmtree(path, ignore_errors=True)


def quiet_main(**kwargs):
    with contextlib.redirect_stdout(io.StringIO()):
        main(**kwargs)


def write_json(path, data):
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(data, fh)


def read_json(path):
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def git(cwd, *args):
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True)


def legacy_milestone(commit, date, tokens, cost_amount=None, note=None):
    """A milestone the way an older engine froze it: four token fields,
    no by_model, no unpriced."""
    return {
        "date": date, "commit": commit, "subject": "subject " + commit,
        "words_delta": 10, "loc_delta": 5, "tokens": tokens,
        "cost_recorded": (
            {"amount": cost_amount, "currency": "USD", "priced_at": "2026-09-13",
             "provider": "anthropic", "model": "claude-sonnet-5"}
            if cost_amount is not None else None
        ),
        "note": note,
    }


class TestBucketTokensByModel(unittest.TestCase):
    def rows(self):
        return [{"iso": "2026-09-13T10:05:00+00:00"}, {"iso": "2026-09-13T10:15:00+00:00"}]

    def test_totals_one_hour_writes_and_the_per_model_split(self):
        events = [
            {"ts": "2026-09-13T10:00:00.000Z", "model": "claude-sonnet-5", "input": 10, "output": 1,
             "cache_read": 5, "cache_creation": 100, "cache_creation_1h": 40},
            {"ts": "2026-09-13T10:01:00.000Z", "model": "claude-opus-5", "input": 20, "output": 2,
             "cache_read": 6, "cache_creation": 200, "cache_creation_1h": 0},
            {"ts": "2026-09-13T10:02:00.000Z", "model": "claude-sonnet-5", "input": 1, "output": 1,
             "cache_read": 1, "cache_creation": 1, "cache_creation_1h": 1},
            {"ts": "2026-09-13T10:10:00.000Z", "model": "claude-haiku-4-5-20251001", "input": 7, "output": 0,
             "cache_read": 0, "cache_creation": 0, "cache_creation_1h": 0},
        ]
        first, second = _bucket_tokens_by_commit(self.rows(), events)
        self.assertEqual(
            {k: v for k, v in first.items() if k != "by_model"},
            {"input": 31, "output": 4, "cache_read": 12, "cache_creation": 301, "cache_creation_1h": 41},
        )
        self.assertEqual(first["by_model"]["claude-sonnet-5"], {
            "input": 11, "output": 2, "cache_read": 6, "cache_creation": 101, "cache_creation_1h": 41,
        })
        self.assertEqual(first["by_model"]["claude-opus-5"]["cache_creation"], 200)
        self.assertNotIn("claude-haiku-4-5-20251001", first["by_model"])
        self.assertEqual(list(second["by_model"]), ["claude-haiku-4-5-20251001"])
        self.assertEqual(second["input"], 7)

    def test_the_split_sums_back_to_the_totals_when_every_event_names_a_model(self):
        events = [
            {"ts": "2026-09-13T10:00:00.000Z", "model": "a", "input": 3, "output": 4, "cache_read": 5,
             "cache_creation": 6, "cache_creation_1h": 2},
            {"ts": "2026-09-13T10:01:00.000Z", "model": "b", "input": 30, "output": 40, "cache_read": 50,
             "cache_creation": 60, "cache_creation_1h": 20},
        ]
        bucket, _ = _bucket_tokens_by_commit(self.rows(), events)
        for key in ("input", "output", "cache_read", "cache_creation", "cache_creation_1h"):
            self.assertEqual(sum(t[key] for t in bucket["by_model"].values()), bucket[key], msg=key)

    def test_an_event_with_no_model_counts_in_the_totals_but_not_in_by_model(self):
        events = [{"ts": "2026-09-13T10:00:00.000Z", "model": None, "input": 9, "output": 0,
                   "cache_read": 0, "cache_creation": 0, "cache_creation_1h": 0}]
        bucket, _ = _bucket_tokens_by_commit(self.rows(), events)
        self.assertEqual(bucket["input"], 9)
        self.assertEqual(bucket["by_model"], {})

    def test_an_event_from_a_reader_that_knows_no_model_or_ttl_still_buckets(self):
        events = [{"ts": "2026-09-13T10:00:00.000Z", "input": 9, "output": 1, "cache_read": 2, "cache_creation": 3}]
        bucket, _ = _bucket_tokens_by_commit(self.rows(), events)
        self.assertEqual(bucket["input"], 9)
        self.assertEqual(bucket["cache_creation_1h"], 0)
        self.assertEqual(bucket["by_model"], {})

    def test_a_fresh_empty_record_is_never_shared(self):
        a, b = empty_tokens(), empty_tokens()
        a["by_model"]["x"] = {}
        self.assertEqual(b["by_model"], {})


class TestFreezeCarriesUnpricedAndAuditTrail(unittest.TestCase):
    def test_a_frozen_milestone_keeps_its_unpriced_list_and_repricings_and_drops_a_fresh_one(self):
        with quiet_tmpdir() as tmpdir:
            path = os.path.join(tmpdir, "data.json")
            frozen = legacy_milestone("aaaaaaa", "2026-09-13", {"input": 5, "output": 0, "cache_read": 0, "cache_creation": 0}, 0.01)
            frozen["unpriced"] = [{"model": "old-model", "tokens": 5, "reason": "old reason"}]
            frozen["repricings"] = [{"repriced_at": "2026-09-19T00:00:00+00:00", "previous_cost": None}]
            write_json(path, {"generated_at": "x", "milestones": [frozen]})

            fresh = legacy_milestone("aaaaaaa", "2026-09-13", {"input": 999, "output": 0, "cache_read": 0, "cache_creation": 0}, 9.99)
            fresh["unpriced"] = [{"model": "new-model", "tokens": 1, "reason": "new reason"}]
            _freeze_previously_recorded_costs([fresh], path)
            self.assertEqual(fresh["cost_recorded"]["amount"], 0.01)
            self.assertEqual(fresh["tokens"]["input"], 5)
            self.assertEqual(fresh["unpriced"][0]["model"], "old-model")
            self.assertEqual(len(fresh["repricings"]), 1)

    def test_a_frozen_milestone_with_nothing_unpriced_does_not_inherit_a_fresh_unpriced_list(self):
        with quiet_tmpdir() as tmpdir:
            path = os.path.join(tmpdir, "data.json")
            frozen = legacy_milestone("aaaaaaa", "2026-09-13", {"input": 5, "output": 0, "cache_read": 0, "cache_creation": 0}, 0.01)
            write_json(path, {"generated_at": "x", "milestones": [frozen]})
            fresh = legacy_milestone("aaaaaaa", "2026-09-13", {"input": 5, "output": 0, "cache_read": 0, "cache_creation": 0}, 0.02)
            fresh["unpriced"] = [{"model": "m", "tokens": 1, "reason": "r"}]
            _freeze_previously_recorded_costs([fresh], path)
            self.assertNotIn("unpriced", fresh)
            self.assertNotIn("repricings", fresh)


class TestRepriceMilestones(unittest.TestCase):
    def ledger(self, *entries):
        return [{"provider": "anthropic", "model": "claude-sonnet-5", "currency": "USD", "entries": list(entries)}]

    def entry(self, prices, date="2026-09-13"):
        return {"effective_date": date, **prices, "sources": SOURCES}

    def test_recomputes_only_the_cost_from_the_frozen_tokens_and_records_the_old_one(self):
        tokens = {"input": 1_000_000, "output": 1_000_000, "cache_read": 0, "cache_creation": 0}
        m = legacy_milestone("aaaaaaa", "2026-09-13", tokens, 18.0, note="kept")
        ledger = self.ledger(self.entry(WRONG), {**self.entry(RIGHT), "corrects": "2026-09-13"})
        config = dict(CONFIG)
        changed = reprice_milestones([m], config, ledger, "2026-09-20T01:00:00+00:00")
        self.assertEqual(changed, 1)
        self.assertEqual(m["cost_recorded"]["amount"], 12.0)
        self.assertEqual(m["tokens"], tokens, "tokens are never touched")
        self.assertEqual((m["words_delta"], m["loc_delta"], m["note"]), (10, 5, "kept"))
        self.assertEqual(len(m["repricings"]), 1)
        self.assertEqual(m["repricings"][0]["repriced_at"], "2026-09-20T01:00:00+00:00")
        self.assertEqual(m["repricings"][0]["previous_cost"]["amount"], 18.0)

    def test_running_it_again_with_nothing_new_changes_nothing(self):
        tokens = {"input": 1_000_000, "output": 0, "cache_read": 0, "cache_creation": 0}
        m = legacy_milestone("aaaaaaa", "2026-09-13", tokens, 3.0)
        ledger = self.ledger(self.entry(WRONG), {**self.entry(RIGHT), "corrects": "2026-09-13"})
        self.assertEqual(reprice_milestones([m], CONFIG, ledger, "t1"), 1)
        self.assertEqual(reprice_milestones([m], CONFIG, ledger, "t2"), 0)
        self.assertEqual(len(m["repricings"]), 1, "a no-op run adds no audit entry")

    def test_a_second_correction_appends_to_the_audit_trail_instead_of_replacing_it(self):
        tokens = {"input": 1_000_000, "output": 0, "cache_read": 0, "cache_creation": 0}
        m = legacy_milestone("aaaaaaa", "2026-09-13", tokens, 3.0)
        first = self.ledger(self.entry(WRONG), {**self.entry(RIGHT), "corrects": "2026-09-13"})
        reprice_milestones([m], CONFIG, first, "t1")
        second = self.ledger(
            self.entry(WRONG), {**self.entry(RIGHT), "corrects": "2026-09-13"},
            {**self.entry({**RIGHT, "input_price": 2.5}), "corrects": "2026-09-13"},
        )
        reprice_milestones([m], CONFIG, second, "t2")
        self.assertEqual(m["cost_recorded"]["amount"], 2.5)
        self.assertEqual([r["previous_cost"]["amount"] for r in m["repricings"]], [3.0, 2.0])

    def test_a_normal_price_change_leaves_past_milestones_alone(self):
        # A later effective_date is a CHANGE, not a correction: the
        # milestone dated before it is still priced by the entry in
        # effect on its own date, so reprice has nothing to do.
        tokens = {"input": 1_000_000, "output": 0, "cache_read": 0, "cache_creation": 0}
        m = legacy_milestone("aaaaaaa", "2026-09-13", tokens, 2.0)
        ledger = self.ledger(self.entry(RIGHT), self.entry({**RIGHT, "input_price": 99.0}, date="2026-10-01"))
        self.assertEqual(reprice_milestones([m], CONFIG, ledger, "t"), 0)
        self.assertEqual(m["cost_recorded"]["amount"], 2.0)
        self.assertNotIn("repricings", m)

    def test_a_missing_model_series_added_later_fills_the_unpriced_gap_and_keeps_the_record(self):
        tokens = {
            "input": 1_000_000, "output": 0, "cache_read": 0, "cache_creation": 0, "cache_creation_1h": 0,
            "by_model": {
                "claude-sonnet-5": {"input": 1_000_000, "output": 0, "cache_read": 0, "cache_creation": 0, "cache_creation_1h": 0},
            },
        }
        tokens["input"] = 2_000_000
        tokens["by_model"]["claude-opus-5"] = {
            "input": 1_000_000, "output": 0, "cache_read": 0, "cache_creation": 0, "cache_creation_1h": 0,
        }
        m = legacy_milestone("aaaaaaa", "2026-09-13", tokens, 2.0)
        m["unpriced"] = [{"model": "claude-opus-5", "tokens": 1_000_000, "reason": "no price series"}]
        ledger = self.ledger(self.entry(RIGHT)) + [
            {"provider": "anthropic", "model": "claude-opus-5", "currency": "USD", "entries": [self.entry(OPUS)]},
        ]
        self.assertEqual(reprice_milestones([m], CONFIG, ledger, "t"), 1)
        self.assertEqual(m["cost_recorded"]["amount"], 7.0)
        self.assertNotIn("unpriced", m)
        self.assertEqual(m["repricings"][0]["previous_unpriced"][0]["model"], "claude-opus-5")
        self.assertEqual(m["repricings"][0]["previous_cost"]["amount"], 2.0)


class TestRepriceCommand(unittest.TestCase):
    def make_logbook(self, tmpdir, milestones, generated_at="2026-09-14T13:32:47+00:00"):
        os.makedirs(os.path.join(tmpdir, "logbook"))
        write_json(os.path.join(tmpdir, "logbook", "config.json"), CONFIG)
        write_json(os.path.join(tmpdir, "logbook", "data.json"), {"generated_at": generated_at, "milestones": milestones})
        ledger_path = os.path.join(tmpdir, "prices.json")
        write_json(ledger_path, [{
            "provider": "anthropic", "model": "claude-sonnet-5", "currency": "USD",
            "entries": [{"effective_date": "2026-09-13", **WRONG, "sources": SOURCES}],
        }])
        return ledger_path

    def two_legacy_milestones(self):
        return [
            legacy_milestone("aaaaaaa", "2026-09-13",
                             {"input": 1_000_000, "output": 1_000_000, "cache_read": 0, "cache_creation": 0}, 18.0, note="n1"),
            legacy_milestone("bbbbbbb", "2026-09-14",
                             {"input": 0, "output": 0, "cache_read": 1_000_000, "cache_creation": 0}, 0.3),
        ]

    def test_reprice_corrects_frozen_costs_rewrites_the_dashboard_and_needs_no_git_or_transcript(self):
        with quiet_tmpdir() as tmpdir:
            ledger_path = self.make_logbook(tmpdir, self.two_legacy_milestones())
            # No git repository here at all, and reading a transcript or
            # git history is made to fail loudly: reprice must not.
            with patch("engine.generate_metrics.SHIPPED_PRICES", ledger_path), \
                 patch("engine.generate_metrics.build_milestones", side_effect=AssertionError("reprice read git")), \
                 patch("engine.generate_metrics.find_session_jsonl", side_effect=AssertionError("reprice read a transcript")):
                append_entry(ledger_path, "anthropic", "claude-sonnet-5", "USD", "2026-09-13", RIGHT, SOURCES,
                             corrects="2026-09-13", note="wrong first time")
                quiet_main(repo_root=tmpdir, reprice=True)

            data = read_json(os.path.join(tmpdir, "logbook", "data.json"))
            first, second = data["milestones"]
            self.assertEqual(first["cost_recorded"]["amount"], 12.0)
            self.assertEqual(second["cost_recorded"]["amount"], 0.2)
            self.assertEqual(first["repricings"][0]["previous_cost"]["amount"], 18.0)
            self.assertEqual(second["repricings"][0]["previous_cost"]["amount"], 0.3)
            # Everything else is exactly what was frozen.
            self.assertEqual(first["tokens"], {"input": 1_000_000, "output": 1_000_000, "cache_read": 0, "cache_creation": 0})
            self.assertEqual(first["note"], "n1")
            self.assertEqual(first["subject"], "subject aaaaaaa")
            self.assertEqual(data["generated_at"], "2026-09-14T13:32:47+00:00")
            self.assertIn("last_repriced_at", data)

            with open(os.path.join(tmpdir, "logbook", "dashboard.html"), encoding="utf-8") as fh:
                page = fh.read()
            self.assertIn('<span class="kpi-n">$12.20</span><span class="kpi-l">Cost recorded</span>', page)
            with open(os.path.join(tmpdir, "logbook", "thumb-cost.svg"), encoding="utf-8") as fh:
                self.assertIn("$12.20", fh.read())

    def test_reprice_twice_is_a_no_op_the_second_time(self):
        with quiet_tmpdir() as tmpdir:
            ledger_path = self.make_logbook(tmpdir, self.two_legacy_milestones())
            with patch("engine.generate_metrics.SHIPPED_PRICES", ledger_path):
                append_entry(ledger_path, "anthropic", "claude-sonnet-5", "USD", "2026-09-13", RIGHT, SOURCES,
                             corrects="2026-09-13")
                quiet_main(repo_root=tmpdir, reprice=True)
                once = read_json(os.path.join(tmpdir, "logbook", "data.json"))
                quiet_main(repo_root=tmpdir, reprice=True)
                twice = read_json(os.path.join(tmpdir, "logbook", "data.json"))
            self.assertEqual(once["milestones"], twice["milestones"])
            self.assertEqual(once["last_repriced_at"], twice["last_repriced_at"])

    def test_reprice_without_a_data_json_stops_with_a_message_and_writes_nothing(self):
        with quiet_tmpdir() as tmpdir:
            os.makedirs(os.path.join(tmpdir, "logbook"))
            write_json(os.path.join(tmpdir, "logbook", "config.json"), CONFIG)
            with self.assertRaises(SystemExit) as raised:
                quiet_main(repo_root=tmpdir, reprice=True)
            self.assertIn("data.json", str(raised.exception))
            self.assertEqual(sorted(os.listdir(os.path.join(tmpdir, "logbook"))), ["config.json"])

    def test_reprice_never_creates_a_logbook_folder(self):
        with quiet_tmpdir() as tmpdir:
            with self.assertRaises(FileNotFoundError):
                quiet_main(repo_root=tmpdir, reprice=True)
            self.assertEqual(os.listdir(tmpdir), [])

    def test_the_cli_flag_works_as_a_direct_script_and_refuses_transcript_flags(self):
        with quiet_tmpdir() as tmpdir:
            self.make_logbook(tmpdir, self.two_legacy_milestones())
            # The shipped ledger prices sonnet-5 at $2/$10 from 2026-09-13.
            ok = subprocess.run(
                [sys.executable, GENERATE_METRICS_PATH, "--repo", tmpdir, "--reprice"],
                cwd=tempfile.gettempdir(), capture_output=True, text=True,
            )
            self.assertEqual(ok.returncode, 0, msg=ok.stderr)
            self.assertIn("repriced 2 of 2 milestones", ok.stdout)
            refused = subprocess.run(
                [sys.executable, GENERATE_METRICS_PATH, "--repo", tmpdir, "--reprice", "--linked-project", tmpdir],
                cwd=tempfile.gettempdir(), capture_output=True, text=True,
            )
            self.assertNotEqual(refused.returncode, 0)
            self.assertIn("--reprice", refused.stderr)


class TestEndToEndCorrectionWorkflow(unittest.TestCase):
    """One real git repository and one transcript with three models, the
    whole life of a wrong price: recorded, found wrong, corrected."""

    def setUp(self):
        tmpdir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmpdir, True)
        self.tmpdir = tmpdir
        git(tmpdir, "init")
        git(tmpdir, "config", "user.email", "t@example.com")
        git(tmpdir, "config", "user.name", "T")
        with open(os.path.join(tmpdir, "README.md"), "w", encoding="utf-8") as fh:
            fh.write("hello world from the only milestone")
        git(tmpdir, "add", "README.md")
        git(tmpdir, "commit", "-m", "Only milestone")
        bootstrap_if_missing(tmpdir, "logbook")
        write_json(os.path.join(tmpdir, "logbook", "config.json"), CONFIG)

        from engine.git_source import commits
        iso = commits(tmpdir)[0]["iso"]
        self.commit_date = iso[:10]
        self.projects_dir = os.path.join(tmpdir, "claude_projects")
        project_dir = os.path.join(self.projects_dir, encode_project_path(tmpdir))
        os.makedirs(project_dir)
        rows = [
            {"timestamp": iso, "message": {"id": "msg_1", "model": "claude-sonnet-5", "usage": {
                "input_tokens": 1_000_000, "output_tokens": 0, "cache_read_input_tokens": 0,
                "cache_creation_input_tokens": 1_000_000,
                "cache_creation": {"ephemeral_5m_input_tokens": 600_000, "ephemeral_1h_input_tokens": 400_000}}}},
            {"timestamp": iso, "message": {"id": "msg_2", "model": "claude-opus-5", "usage": {
                "input_tokens": 0, "output_tokens": 1_000_000, "cache_read_input_tokens": 0,
                "cache_creation_input_tokens": 0}}},
            {"timestamp": iso, "message": {"id": "msg_3", "model": "claude-mystery-9", "usage": {
                "input_tokens": 1000, "output_tokens": 0, "cache_read_input_tokens": 0,
                "cache_creation_input_tokens": 0}}},
        ]
        with open(os.path.join(project_dir, "session.jsonl"), "w", encoding="utf-8") as fh:
            for row in rows:
                fh.write(json.dumps(row) + "\n")

        # The ledger as it was when the price was wrong: the wrong
        # sonnet entry, no 1-hour price, and no opus series at all.
        self.ledger_path = os.path.join(tmpdir, "prices.json")
        write_json(self.ledger_path, [{
            "provider": "anthropic", "model": "claude-sonnet-5", "currency": "USD",
            "entries": [{"effective_date": self.commit_date, **WRONG, "sources": SOURCES}],
        }])

    def data(self):
        return read_json(os.path.join(self.tmpdir, "logbook", "data.json"))

    def test_tokens_split_by_model_are_priced_per_model_with_gaps_reported(self):
        tmpdir = self.tmpdir
        with patch("engine.generate_metrics.SHIPPED_PRICES", self.ledger_path):
            quiet_main(repo_root=tmpdir, claude_projects_dir=self.projects_dir)
        m = self.data()["milestones"][0]

        self.assertEqual(m["tokens"]["input"], 1_001_000)
        self.assertEqual(m["tokens"]["cache_creation"], 1_000_000)
        self.assertEqual(m["tokens"]["cache_creation_1h"], 400_000)
        self.assertEqual(sorted(m["tokens"]["by_model"]), ["claude-mystery-9", "claude-opus-5", "claude-sonnet-5"])
        self.assertEqual(m["tokens"]["by_model"]["claude-sonnet-5"]["cache_creation_1h"], 400_000)

        # Sonnet priced at ITS series (wrong price at this point): input
        # 3.00 + 600k five-minute writes at 3.75. Its 400k 1-hour writes
        # (no 1-hour price), all of opus (no series) and all of the
        # mystery model (no series) are unpriced, not priced at sonnet's rate.
        self.assertEqual(m["cost_recorded"]["amount"], 5.25)
        self.assertEqual(list(m["cost_recorded"]["by_model"]), ["claude-sonnet-5"])
        unpriced = {u["model"]: u for u in m["unpriced"]}
        self.assertEqual(sorted(unpriced), ["claude-mystery-9", "claude-opus-5", "claude-sonnet-5"])
        self.assertEqual(unpriced["claude-opus-5"]["tokens"], 1_000_000)
        self.assertEqual(unpriced["claude-sonnet-5"]["tokens"], 400_000)

        with open(os.path.join(tmpdir, "logbook", "dashboard.html"), encoding="utf-8") as fh:
            page = fh.read()
        self.assertIn("<h2>Unpriced usage</h2>", page)
        self.assertIn("claude-opus-5", page)
        self.assertIn("(partial)", page)
        self.assertIn('<span class="kpi-n">1</span><span class="kpi-l">Unpriced milestones</span>', page)
        self.assertIn('"default_series": "anthropic::claude-sonnet-5"', page)
        # The live price panel receives the flat counters only, 1-hour
        # writes included.
        row_tokens = re.search(r"data-milestone-tokens='([^']*)'", page).group(1)
        self.assertEqual(json.loads(row_tokens), {
            "input": 1_001_000, "output": 1_000_000, "cache_read": 0,
            "cache_creation": 1_000_000, "cache_creation_1h": 400_000,
        })

    def test_wrong_price_found_then_corrected_by_an_appended_entry_and_reprice(self):
        tmpdir = self.tmpdir
        with patch("engine.generate_metrics.SHIPPED_PRICES", self.ledger_path):
            quiet_main(repo_root=tmpdir, claude_projects_dir=self.projects_dir)
            self.assertEqual(self.data()["milestones"][0]["cost_recorded"]["amount"], 5.25)

            # Found wrong: append the correction (never edit), add the
            # missing opus series, then correct explicitly.
            append_entry(self.ledger_path, "anthropic", "claude-sonnet-5", "USD", self.commit_date, RIGHT, SOURCES,
                         corrects=self.commit_date, note="recorded the cancelled increase")
            append_entry(self.ledger_path, "anthropic", "claude-opus-5", "USD", self.commit_date, OPUS, SOURCES)

            # A normal run changes nothing: costs are frozen.
            quiet_main(repo_root=tmpdir, claude_projects_dir=self.projects_dir)
            self.assertEqual(self.data()["milestones"][0]["cost_recorded"]["amount"], 5.25)

            quiet_main(repo_root=tmpdir, reprice=True)
            m = self.data()["milestones"][0]
            # sonnet: input 2.00 + 600k*2.50 + 400k*4.00 = 5.10; opus: 1M out * 25 = 25.00
            self.assertEqual(m["cost_recorded"]["amount"], 30.1)
            self.assertEqual([u["model"] for u in m["unpriced"]], ["claude-mystery-9"])
            self.assertEqual(m["repricings"][0]["previous_cost"]["amount"], 5.25)
            self.assertEqual(len(m["repricings"][0]["previous_unpriced"]), 3)

            # And afterwards a normal run keeps the corrected cost and the audit trail.
            quiet_main(repo_root=tmpdir, claude_projects_dir=self.projects_dir)
            after = self.data()["milestones"][0]
            self.assertEqual(after["cost_recorded"]["amount"], 30.1)
            self.assertEqual(after["repricings"], m["repricings"])
            self.assertEqual([u["model"] for u in after["unpriced"]], ["claude-mystery-9"])
            self.assertIn("last_repriced_at", self.data())


class TestUnpricedRendering(unittest.TestCase):
    def milestone(self, **extra):
        m = legacy_milestone("abc1234", "2026-09-13", {"input": 5, "output": 0, "cache_read": 0, "cache_creation": 0}, 1.5)
        m.update(extra)
        return m

    def test_a_partial_milestone_is_marked_and_its_reasons_are_escaped(self):
        m = self.milestone(unpriced=[{"model": "<b>evil</b>", "tokens": 1234, "reason": "no \"series\" & more"}])
        rows = render_table_rows([m])
        self.assertIn("(partial)", rows)
        self.assertNotIn("<b>evil</b>", rows)
        self.assertIn("&lt;b&gt;evil&lt;/b&gt;", rows)
        self.assertIn("1,234 tokens", rows)
        section = render_unpriced_section([m])
        self.assertNotIn("<b>evil</b>", section)
        self.assertIn("&lt;b&gt;evil&lt;/b&gt;", section)
        self.assertIn("M1", section)
        self.assertIn("--reprice", section)

    def test_a_milestone_with_nothing_priced_shows_a_dash_and_its_reason(self):
        m = self.milestone(cost_recorded=None, unpriced=[{"model": None, "tokens": 5, "reason": "why"}])
        rows = render_table_rows([m])
        self.assertIn('<td title="Unpriced: no model id: 5 tokens, why">-</td>', rows)

    def test_a_fully_priced_dashboard_has_no_unpriced_section_and_the_old_cost_cell(self):
        m = self.milestone()
        self.assertEqual(render_unpriced_section([m]), "")
        self.assertIn("<td>$1.5000</td>", render_table_rows([m]))
        self.assertNotIn("(partial)", render_table_rows([m]))

    def test_the_row_tokens_attribute_is_flat_and_tolerates_a_milestone_frozen_by_an_older_engine(self):
        m = self.milestone()
        row_tokens = re.search(r"data-milestone-tokens='([^']*)'", render_table_rows([m])).group(1)
        self.assertEqual(json.loads(row_tokens), {
            "input": 5, "output": 0, "cache_read": 0, "cache_creation": 0, "cache_creation_1h": 0,
        })


class TestTemplateWiring(unittest.TestCase):
    def test_every_element_id_dashboard_js_looks_up_exists_in_the_template(self):
        with open(os.path.join(TEMPLATE_DIR, "dashboard.js"), encoding="utf-8") as fh:
            script = fh.read()
        with open(os.path.join(TEMPLATE_DIR, "dashboard.html"), encoding="utf-8") as fh:
            page = fh.read()
        looked_up = set(re.findall(r'getElementById\("([^"]+)"\)', script))
        self.assertIn("price-cache-creation-1h", looked_up)
        self.assertIn("price-cache-creation-1h-range", looked_up)
        for element_id in looked_up:
            self.assertIn('id="%s"' % element_id, page, msg=element_id)


if __name__ == "__main__":
    unittest.main()
