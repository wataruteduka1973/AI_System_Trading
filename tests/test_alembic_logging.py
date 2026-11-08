import ast
from pathlib import Path

ENV_PY = Path(__file__).resolve().parents[1] / "alembic" / "env.py"


def test_running_a_migration_does_not_silence_the_application_loggers() -> None:
    """`logging.config.fileConfig` disables every logger that exists when it is called unless told
    not to. alembic/env.py calls it on each run, so a migration run in a test process muted the
    logging of every test after it (the log-file tests failed only when the PostgreSQL tests ran
    in the same session). The call is checked in the source because running the migrations needs a
    database."""
    calls = [
        node
        for node in ast.walk(ast.parse(ENV_PY.read_text(encoding="utf-8")))
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "fileConfig"
    ]
    assert calls, "alembic/env.py no longer configures logging; update this test"
    for call in calls:
        keywords = {keyword.arg: keyword.value for keyword in call.keywords}
        assert isinstance(keywords.get("disable_existing_loggers"), ast.Constant)
        assert keywords["disable_existing_loggers"].value is False
