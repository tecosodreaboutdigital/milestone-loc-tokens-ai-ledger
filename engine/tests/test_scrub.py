import unittest

from engine.scrub import scrub_text


class TestScrubText(unittest.TestCase):
    def test_removes_home_directory(self):
        result = scrub_text(
            "found at /home/alice/projects/thing.py",
            home_dir="/home/alice",
            username="alice",
            hostname="alices-laptop",
        )
        self.assertNotIn("/home/alice", result)
        self.assertIn("~", result)

    def test_removes_windows_home_directory_both_slash_styles(self):
        result = scrub_text(
            r"C:\Users\alice\Dropbox\thing.py and C:/Users/alice/Dropbox/thing.py",
            home_dir=r"C:\Users\alice",
            username="alice",
            hostname="alices-laptop",
        )
        self.assertNotIn("Users\\alice", result)
        self.assertNotIn("Users/alice", result)

    def test_removes_username_case_insensitively(self):
        result = scrub_text(
            "run by Alice on this machine",
            home_dir="/home/alice",
            username="alice",
            hostname="alices-laptop",
        )
        self.assertNotIn("Alice", result)
        self.assertIn("[user]", result)

    def test_removes_hostname(self):
        result = scrub_text(
            "session on alices-laptop today",
            home_dir="/home/alice",
            username="alice",
            hostname="alices-laptop",
        )
        self.assertNotIn("alices-laptop", result)
        self.assertIn("[host]", result)

    def test_none_passes_through(self):
        self.assertIsNone(scrub_text(None, home_dir="/home/alice", username="alice", hostname="h"))

    def test_leaves_unrelated_text_untouched(self):
        result = scrub_text(
            "Regenerate the project log, milestones M64 to M67",
            home_dir="/home/alice",
            username="alice",
            hostname="alices-laptop",
        )
        self.assertEqual(result, "Regenerate the project log, milestones M64 to M67")


if __name__ == "__main__":
    unittest.main()
