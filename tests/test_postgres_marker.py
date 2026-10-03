"""CI's worker-storage job is the only place PostgreSQL-backed tests actually run
(everywhere else they skip without WORKER_TEST_DATABASE_URL), and it selects them
with `-m postgres`. A test module that reads the variable but lacks the marker would
skip everywhere and never run -- this keeps that from happening silently."""

from pathlib import Path

TESTS = Path(__file__).resolve().parent


def test_every_module_using_the_worker_test_database_is_marked_postgres() -> None:
    unmarked = [
        path.name
        for path in sorted(TESTS.glob("test_*.py"))
        if path.name != Path(__file__).name
        and "WORKER_TEST_DATABASE_URL" in (text := path.read_text(encoding="utf-8"))
        and "pytest.mark.postgres" not in text
    ]
    assert unmarked == []
