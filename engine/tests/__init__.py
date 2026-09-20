"""Test package. One environment workaround lives here, on purpose, so
that no individual test has to carry it.

On Windows, deleting a temporary directory right after a test wrote and
os.replace()d files in it can fail with

    OSError: [WinError 145] The directory is not empty

even though the test itself passed. The likely mechanism (a hypothesis:
it could not be reproduced in isolation): a just-deleted or just-replaced
file stays in "delete pending" state for a moment while Windows Defender's
real-time scan or the search indexer still has it open, and rmdir refuses
until it lets go. It is sporadic and load dependent (it showed up in full
`python -m unittest discover -s engine/tests -t .` runs, in different tests
each time, never when a test file ran alone), and it says nothing about
the code under test. A failed cleanup of a scratch directory must not fail
the test that used it, so on Windows every tempfile.TemporaryDirectory()
created by these tests ignores cleanup errors by default (Python 3.10+,
where the parameter exists). The worst case is a leftover directory in
the user's temp folder.

An explicit ignore_cleanup_errors=False still wins. Other platforms keep
the strict standard behaviour.
"""

import sys
import tempfile

if sys.platform == "win32" and sys.version_info >= (3, 10):
    _StrictTemporaryDirectory = tempfile.TemporaryDirectory

    class _TolerantTemporaryDirectory(_StrictTemporaryDirectory):
        def __init__(self, *args, **kwargs):
            # ignore_cleanup_errors is the 4th positional parameter
            # (suffix, prefix, dir, ignore_cleanup_errors); only fill it
            # in when the caller did not pass it either way.
            if len(args) < 4:
                kwargs.setdefault("ignore_cleanup_errors", True)
            super().__init__(*args, **kwargs)

    tempfile.TemporaryDirectory = _TolerantTemporaryDirectory
