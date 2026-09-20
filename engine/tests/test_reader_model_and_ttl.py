import json
import os
import tempfile
import unittest

from engine.readers.claude_code import load_usage_events


def write_jsonl(path, rows):
    with open(path, "w", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row) + "\n")


def message_row(ts, message_id, usage, model="claude-sonnet-5", include_model=True):
    message = {"id": message_id, "usage": usage}
    if include_model:
        message["model"] = model
    return {"timestamp": ts, "message": message}


class TestModelAndOneHourCacheWrites(unittest.TestCase):
    def load(self, rows):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = os.path.join(tmpdir, "session.jsonl")
            write_jsonl(path, rows)
            return load_usage_events([path])

    def test_event_carries_the_model_and_the_one_hour_cache_writes(self):
        # usage.cache_creation is the shape a real Claude Code
        # transcript carries: the 5-minute and 1-hour split of the
        # message's cache_creation_input_tokens.
        events = self.load([message_row("2026-09-13T10:00:00.000Z", "msg_1", {
            "input_tokens": 5, "output_tokens": 7,
            "cache_read_input_tokens": 100, "cache_creation_input_tokens": 1000,
            "cache_creation": {"ephemeral_5m_input_tokens": 600, "ephemeral_1h_input_tokens": 400},
        })])
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["model"], "claude-sonnet-5")
        self.assertEqual(events[0]["cache_creation"], 1000)
        self.assertEqual(events[0]["cache_creation_1h"], 400)
        self.assertEqual(events[0]["input"], 5)
        self.assertEqual(events[0]["output"], 7)
        self.assertEqual(events[0]["cache_read"], 100)

    def test_no_cache_creation_object_means_zero_one_hour_writes(self):
        events = self.load([message_row("2026-09-13T10:00:00.000Z", "msg_1", {
            "input_tokens": 1, "output_tokens": 1, "cache_creation_input_tokens": 1000,
        })])
        self.assertEqual(events[0]["cache_creation"], 1000)
        self.assertEqual(events[0]["cache_creation_1h"], 0)

    def test_one_hour_writes_are_never_more_than_cache_creation(self):
        events = self.load([message_row("2026-09-13T10:00:00.000Z", "msg_1", {
            "cache_creation_input_tokens": 300,
            "cache_creation": {"ephemeral_5m_input_tokens": 0, "ephemeral_1h_input_tokens": 999},
        })])
        self.assertEqual(events[0]["cache_creation_1h"], 300)

    def test_a_malformed_cache_creation_object_counts_as_zero(self):
        for bad in ("nonsense", 5, ["x"], {"ephemeral_1h_input_tokens": "many"},
                    {"ephemeral_1h_input_tokens": -4}, {"ephemeral_1h_input_tokens": True}):
            events = self.load([message_row("2026-09-13T10:00:00.000Z", "msg_1", {
                "cache_creation_input_tokens": 300, "cache_creation": bad,
            })])
            self.assertEqual(events[0]["cache_creation_1h"], 0, msg=repr(bad))

    def test_a_row_with_no_model_has_model_none(self):
        events = self.load([message_row(
            "2026-09-13T10:00:00.000Z", "msg_1", {"input_tokens": 1}, include_model=False,
        )])
        self.assertIsNone(events[0]["model"])

    def test_dedup_by_message_id_still_keeps_the_last_occurrence_with_its_model_and_ttl(self):
        events = self.load([
            message_row("2026-09-13T10:00:00.000Z", "msg_1", {
                "cache_creation_input_tokens": 1000,
                "cache_creation": {"ephemeral_1h_input_tokens": 100},
            }),
            message_row("2026-09-13T10:00:00.500Z", "msg_1", {
                "cache_creation_input_tokens": 1000,
                "cache_creation": {"ephemeral_1h_input_tokens": 1000},
            }),
        ])
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["cache_creation_1h"], 1000)

    def test_two_models_in_one_transcript_stay_separate_events(self):
        events = self.load([
            message_row("2026-09-13T10:00:00.000Z", "msg_1", {"input_tokens": 10}, model="claude-sonnet-5"),
            message_row("2026-09-13T10:01:00.000Z", "msg_2", {"input_tokens": 20}, model="claude-haiku-4-5-20251001"),
        ])
        self.assertEqual([e["model"] for e in events], ["claude-sonnet-5", "claude-haiku-4-5-20251001"])


if __name__ == "__main__":
    unittest.main()
