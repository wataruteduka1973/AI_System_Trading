import importlib.util
import socket
import subprocess
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

spec = importlib.util.spec_from_file_location(
    "local_launcher", Path(__file__).resolve().parents[1] / "scripts/start_local.py"
)
launcher = importlib.util.module_from_spec(spec)
spec.loader.exec_module(launcher)


def test_interactive_keys_reject_non_windows(monkeypatch) -> None:
    with monkeypatch.context() as context:
        context.setattr(launcher.sys, "platform", "linux")
        with pytest.raises(RuntimeError, match="requires Windows"):
            launcher.read_key()


def test_non_windows_stop_uses_sigterm(monkeypatch) -> None:
    process = MagicMock()
    process.poll.return_value = None
    with monkeypatch.context() as context:
        context.setattr(launcher.sys, "platform", "linux")
        launcher.stop_processes([process])
    process.send_signal.assert_called_once_with(launcher.signal.SIGTERM)


def test_occupied_port_is_rejected_without_killing_anything(monkeypatch) -> None:
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen()
        port = listener.getsockname()[1]
        monkeypatch.setattr(launcher, "PORTS", (port,))
        with pytest.raises(RuntimeError, match=f"Port {port} is busy"):
            launcher.check_ports()
        assert listener.fileno() != -1


def test_commands_use_project_python_fixed_ports_and_no_reload(tmp_path, monkeypatch) -> None:
    (tmp_path / ".env").touch()
    vite = tmp_path / "frontend/node_modules/vite/bin/vite.js"
    vite.parent.mkdir(parents=True)
    vite.touch()
    monkeypatch.setattr(launcher.shutil, "which", lambda _: "node.exe")
    backend, frontend = launcher.commands(tmp_path)
    assert backend[:4] == [sys.executable, "-m", "uvicorn", "app.main:app"]
    assert "--reload" not in backend
    assert frontend[1] == str(vite)
    assert "--strictPort" in frontend
    assert backend[backend.index("--host") + 1] == "127.0.0.1"
    assert frontend[frontend.index("--host") + 1] == "127.0.0.1"


def test_missing_env_is_actionable(tmp_path) -> None:
    with pytest.raises(RuntimeError, match=".env is missing"):
        launcher.commands(tmp_path)


def test_missing_frontend_dependencies_are_not_installed(tmp_path, monkeypatch) -> None:
    (tmp_path / ".env").touch()
    monkeypatch.setattr(launcher.shutil, "which", lambda _: "node.exe")
    with pytest.raises(RuntimeError, match="npm install"):
        launcher.commands(tmp_path)


@pytest.mark.parametrize("key,restart", [("q", False), ("r", True)])
def test_quit_and_restart_stop_only_owned_processes(tmp_path, monkeypatch, key, restart) -> None:
    processes = [MagicMock(), MagicMock()]
    for process in processes:
        process.poll.return_value = None
    launch = MagicMock(side_effect=processes)
    monkeypatch.setattr(launcher.subprocess, "Popen", launch)
    monkeypatch.setattr(launcher, "check_ports", lambda: None)
    monkeypatch.setattr(launcher, "read_key", lambda: key)
    assert launcher.run_once(tmp_path, [["backend"], ["frontend"]], False) == restart
    for process in processes:
        process.send_signal.assert_called_once()
        process.wait.assert_called_once_with(timeout=10)
        process.kill.assert_not_called()
    assert launch.call_args_list[0].kwargs["cwd"] == tmp_path
    assert launch.call_args_list[1].kwargs["cwd"] == tmp_path / "frontend"
    assert launch.call_args.kwargs["stdin"] == subprocess.DEVNULL


def test_second_launch_failure_cleans_up_first_process(tmp_path, monkeypatch) -> None:
    first = MagicMock()
    first.poll.return_value = None
    monkeypatch.setattr(launcher, "check_ports", lambda: None)
    monkeypatch.setattr(
        launcher.subprocess, "Popen", MagicMock(side_effect=[first, OSError("launch failed")])
    )
    with pytest.raises(OSError, match="launch failed"):
        launcher.run_once(tmp_path, [["backend"], ["frontend"]], False)
    first.send_signal.assert_called_once()


def test_unexpected_exit_stops_other_server(tmp_path, monkeypatch) -> None:
    failed, remaining = MagicMock(), MagicMock()
    failed.poll.return_value = 1
    remaining.poll.return_value = None
    monkeypatch.setattr(launcher, "check_ports", lambda: None)
    monkeypatch.setattr(launcher.subprocess, "Popen", MagicMock(side_effect=[failed, remaining]))
    with pytest.raises(RuntimeError, match="A server exited"):
        launcher.run_once(tmp_path, [["backend"], ["frontend"]], False)
    failed.send_signal.assert_not_called()
    remaining.send_signal.assert_called_once()


def test_unresponsive_owned_process_is_killed_after_grace_period() -> None:
    process = MagicMock()
    process.poll.return_value = None
    process.wait.side_effect = [subprocess.TimeoutExpired("owned", 10), None]
    launcher.stop_processes([process])
    process.kill.assert_called_once()
    assert process.wait.call_args.kwargs == {"timeout": 5}


@pytest.mark.parametrize("reason", ["interrupt", "timeout"])
def test_interruption_or_startup_timeout_cleans_up_both_servers(tmp_path, monkeypatch, reason):
    processes = [MagicMock(), MagicMock()]
    for process in processes:
        process.poll.return_value = None
    monkeypatch.setattr(launcher, "check_ports", lambda: None)
    monkeypatch.setattr(launcher.subprocess, "Popen", MagicMock(side_effect=processes))
    monkeypatch.setattr(launcher, "ready", lambda: False)
    if reason == "interrupt":
        monkeypatch.setattr(launcher, "read_key", MagicMock(side_effect=KeyboardInterrupt))
        expected = KeyboardInterrupt
    else:
        monkeypatch.setattr(launcher, "read_key", lambda: "")
        monkeypatch.setattr(launcher.time, "monotonic", MagicMock(side_effect=[0, 61]))
        expected = RuntimeError
    with pytest.raises(expected):
        launcher.run_once(tmp_path, [["backend"], ["frontend"]], False)
    for process in processes:
        process.send_signal.assert_called_once()


def test_check_only_does_not_start_servers(monkeypatch) -> None:
    monkeypatch.setattr(sys, "argv", ["start_local.py", "--check"])
    monkeypatch.setattr(launcher, "commands", lambda _: [])
    monkeypatch.setattr(launcher, "check_ports", lambda: None)
    run = MagicMock()
    monkeypatch.setattr(launcher, "run_once", run)
    assert launcher.main() == 0
    run.assert_not_called()


def test_real_child_processes_exit_on_quit(tmp_path, monkeypatch) -> None:
    """Exercise real OS process creation/cleanup without starting the app or ingestion."""
    (tmp_path / "frontend").mkdir()
    owned = []
    popen = subprocess.Popen

    def spawn(*args, **kwargs):
        process = popen(*args, **kwargs)
        owned.append(process)
        return process

    monkeypatch.setattr(launcher.subprocess, "Popen", spawn)
    monkeypatch.setattr(launcher, "check_ports", lambda: None)
    monkeypatch.setattr(launcher, "ready", lambda: True)
    keys = iter(["", "q"])
    monkeypatch.setattr(launcher, "read_key", lambda: next(keys))
    command = [sys.executable, "-c", "import time; time.sleep(30)"]
    try:
        assert not launcher.run_once(tmp_path, [command, command], False)
    finally:
        for process in owned:
            if process.poll() is None:
                process.kill()
            process.wait(timeout=5)
    assert len(owned) == 2
    assert all(process.poll() is not None for process in owned)
