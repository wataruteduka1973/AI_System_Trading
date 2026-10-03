"""Test-session setup that must run before any `app` module is imported.

`app.core.config.settings` is built on import, and `log_dir` defaults to the relative
`Path("logs")` -- the same directory the running app writes. Point it at a throwaway
directory so test traffic never lands in the real `backend.log` / `worker.log`
(regression: `tests/test_logs_stay_out_of_the_repository.py`). An environment variable
wins over `.env`, and subprocesses started by tests inherit it.
"""

import atexit
import os
import shutil
import tempfile

_TEST_LOG_DIR = tempfile.mkdtemp(prefix="ai-system-trading-test-logs-")
os.environ["LOG_DIR"] = _TEST_LOG_DIR
# Handlers may still hold the files open at exit on Windows; leftovers are only temp files.
atexit.register(shutil.rmtree, _TEST_LOG_DIR, ignore_errors=True)
