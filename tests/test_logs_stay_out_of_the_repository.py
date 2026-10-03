"""Tests must never write into the real `logs/` directory. `log_dir` defaults to the
relative `Path("logs")`, `app/main.py` configures logging on import, and the worker
entry points replace the root logger's handlers when their `main()` runs -- so a test
run from the repository root used to append test traffic to the same `backend.log` /
`worker.log` the running app writes (`tests/conftest.py` now points LOG_DIR elsewhere)."""

import logging
from pathlib import Path

from app.core.config import settings

REPOSITORY = Path(__file__).resolve().parents[1]


def test_the_test_run_logs_outside_the_repository() -> None:
    assert not settings.log_dir.resolve().is_relative_to(REPOSITORY)


def test_no_log_handler_writes_into_the_repository() -> None:
    import app.main  # noqa: F401  (configures logging on import)

    files = [
        Path(handler.baseFilename).resolve()
        for handler in logging.getLogger().handlers
        if isinstance(handler, logging.FileHandler)
    ]
    assert files, "expected app.main to have configured a file handler"
    assert not [path for path in files if path.is_relative_to(REPOSITORY)]
