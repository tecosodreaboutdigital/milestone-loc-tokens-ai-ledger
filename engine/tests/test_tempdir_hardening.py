"""The Windows temp-directory workaround in engine/tests/__init__.py."""

import os
import shutil
import sys
import tempfile
import unittest


@unittest.skipUnless(
    sys.platform == "win32" and sys.version_info >= (3, 10),
    "the workaround only applies on Windows with Python 3.10 or newer",
)
class TestTolerantTemporaryDirectory(unittest.TestCase):
    def hold_a_file_open_in(self, directory):
        # An open file blocks deleting its directory on Windows, the
        # same failure a scanner briefly holding a file causes.
        return open(os.path.join(directory, "held.txt"), "w")

    def test_a_failed_cleanup_does_not_raise_by_default(self):
        held = None
        directory = None
        try:
            with tempfile.TemporaryDirectory() as tmpdir:
                directory = tmpdir
                held = self.hold_a_file_open_in(tmpdir)
        finally:
            if held is not None:
                held.close()
            if directory is not None:
                shutil.rmtree(directory, ignore_errors=True)

    def test_an_explicit_ignore_cleanup_errors_false_still_raises(self):
        held = None
        directory = None
        try:
            with self.assertRaises(OSError):
                with tempfile.TemporaryDirectory(ignore_cleanup_errors=False) as tmpdir:
                    directory = tmpdir
                    held = self.hold_a_file_open_in(tmpdir)
        finally:
            if held is not None:
                held.close()
            if directory is not None:
                shutil.rmtree(directory, ignore_errors=True)

    def test_a_normal_directory_is_still_removed(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = tmpdir
            with open(os.path.join(tmpdir, "f.txt"), "w") as fh:
                fh.write("x")
        self.assertFalse(os.path.exists(path))

    def test_it_is_still_a_temporary_directory_class(self):
        self.assertTrue(issubclass(tempfile.TemporaryDirectory, object))
        with tempfile.TemporaryDirectory(prefix="tolerant-") as tmpdir:
            self.assertTrue(os.path.basename(tmpdir).startswith("tolerant-"))


if __name__ == "__main__":
    unittest.main()
