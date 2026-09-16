import asyncio
import signal
import sys
from types import SimpleNamespace

import pytest
from app.market_data.worker import __main__ as worker_main


class _FakeConnection:
    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        return False


class _FakeEngine:
    def connect(self):
        return _FakeConnection()


def _fake_migration_context(heads):
    class _MC:
        @staticmethod
        def configure(connection):
            return SimpleNamespace(get_current_heads=lambda: heads)

    return _MC


def test_check_schema_revision_accepts_the_required_revision(monkeypatch):
    monkeypatch.setattr(worker_main, "engine", _FakeEngine())
    monkeypatch.setattr(
        worker_main, "MigrationContext", _fake_migration_context(("20260831_0005",))
    )
    worker_main._check_schema_revision()


def test_check_schema_revision_rejects_an_older_revision(monkeypatch):
    monkeypatch.setattr(worker_main, "engine", _FakeEngine())
    monkeypatch.setattr(
        worker_main, "MigrationContext", _fake_migration_context(("20260825_0004",))
    )
    with pytest.raises(RuntimeError, match="20260825_0004"):
        worker_main._check_schema_revision()


def test_check_schema_revision_rejects_no_revision_at_all(monkeypatch):
    monkeypatch.setattr(worker_main, "engine", _FakeEngine())
    monkeypatch.setattr(worker_main, "MigrationContext", _fake_migration_context(()))
    with pytest.raises(RuntimeError, match="none"):
        worker_main._check_schema_revision()


def test_posix_signal_handlers_set_the_stop_event(monkeypatch):
    monkeypatch.setattr(sys, "platform", "linux")
    registered: dict[int, object] = {}

    async def scenario() -> None:
        loop = asyncio.get_running_loop()
        monkeypatch.setattr(
            loop, "add_signal_handler", lambda sig, cb: registered.__setitem__(sig, cb)
        )
        stop = asyncio.Event()
        worker_main._install_signal_handlers(loop, stop)
        assert set(registered) == {signal.SIGINT, signal.SIGTERM}
        registered[signal.SIGINT]()
        await asyncio.sleep(0)
        assert stop.is_set()

    asyncio.run(scenario())


@pytest.mark.skipif(sys.platform != "win32", reason="SIGBREAK only exists on Windows")
def test_win32_signal_handlers_set_the_stop_event(monkeypatch):
    monkeypatch.setattr(sys, "platform", "win32")
    registered: dict[int, object] = {}
    monkeypatch.setattr(signal, "signal", lambda sig, cb: registered.__setitem__(sig, cb))

    async def scenario() -> None:
        loop = asyncio.get_running_loop()
        stop = asyncio.Event()
        worker_main._install_signal_handlers(loop, stop)
        assert set(registered) == {signal.SIGINT, signal.SIGTERM, signal.SIGBREAK}
        registered[signal.SIGINT](signal.SIGINT, None)
        await asyncio.sleep(0)
        assert stop.is_set()

    asyncio.run(scenario())


def test_main_checks_revision_then_runs_the_worker_loop(monkeypatch):
    monkeypatch.setattr(worker_main, "_check_schema_revision", lambda: None)
    calls = []

    async def fake_run() -> None:
        calls.append(True)

    monkeypatch.setattr(worker_main, "_run", fake_run)
    monkeypatch.setattr(sys, "argv", ["worker"])
    assert worker_main.main() == 0
    assert calls == [True]


def test_main_normalize_flag_skips_the_worker_loop(monkeypatch):
    monkeypatch.setattr(worker_main, "_check_schema_revision", lambda: None)
    normalize_calls = []
    monkeypatch.setattr(
        worker_main, "_run_normalization", lambda: normalize_calls.append(True) or 0
    )
    run_calls = []
    monkeypatch.setattr(worker_main.asyncio, "run", lambda coro: run_calls.append(coro))
    monkeypatch.setattr(sys, "argv", ["worker", "--normalize-legacy-jobs"])
    assert worker_main.main() == 0
    assert normalize_calls == [True]
    assert run_calls == []


def test_main_returns_error_code_without_running_when_revision_check_fails(monkeypatch, capsys):
    def fail() -> None:
        raise RuntimeError("schema mismatch")

    monkeypatch.setattr(worker_main, "_check_schema_revision", fail)
    monkeypatch.setattr(sys, "argv", ["worker"])
    assert worker_main.main() == 1
    assert "schema mismatch" in capsys.readouterr().err
