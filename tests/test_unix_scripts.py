"""The shell scripts for macOS and Linux (`start-local.sh`, `scripts/unix/`). They are run for real
where a POSIX shell is the platform's own; on Windows only the syntax is checked."""

import os
import shutil
import signal
import stat
import subprocess
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = [ROOT / "start-local.sh", ROOT / "scripts/unix/run_trading_worker.sh"]
posix_only = pytest.mark.skipif(sys.platform == "win32", reason="runs the scripts for real")


@pytest.mark.skipif(shutil.which("sh") is None, reason="no POSIX shell")
@pytest.mark.parametrize("script", SCRIPTS, ids=lambda path: path.name)
def test_the_scripts_are_valid_shell(script: Path) -> None:
    subprocess.run(["sh", "-n", str(script)], check=True)


@posix_only
@pytest.mark.parametrize("script", SCRIPTS, ids=lambda path: path.name)
def test_the_scripts_are_executable(script: Path) -> None:
    assert script.stat().st_mode & stat.S_IXUSR


def _project_copy(tmp_path: Path, script: Path) -> Path:
    """The script in a throw-away project tree, so it never touches the real environment."""
    target = tmp_path / script.relative_to(ROOT)
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy(script, target)
    target.chmod(0o755)
    return target


@posix_only
def test_the_launcher_script_refuses_without_a_project_python(tmp_path: Path) -> None:
    script = _project_copy(tmp_path, ROOT / "start-local.sh")
    result = subprocess.run([str(script)], capture_output=True, text=True, check=False)
    assert result.returncode == 1
    assert "No working project Python 3.13 environment" in result.stderr


@posix_only
def test_the_launcher_script_runs_the_launcher_with_its_arguments(tmp_path: Path) -> None:
    script = _project_copy(tmp_path, ROOT / "start-local.sh")
    python = tmp_path / ".venv/bin/python"
    python.parent.mkdir(parents=True)
    # Passes the version probe, then reports how it was called instead of starting anything.
    python.write_text(
        '#!/bin/sh\n[ "$1" = "-c" ] && exit 0\necho "called with: $*"\n', encoding="utf-8"
    )
    python.chmod(0o755)
    result = subprocess.run(
        [str(script), "--check", "--no-browser"], capture_output=True, text=True, check=False
    )
    assert result.returncode == 0
    assert "scripts/start_local.py --check --no-browser" in result.stdout


@posix_only
def test_the_worker_script_restarts_the_worker_and_stops_cleanly(tmp_path: Path) -> None:
    script = _project_copy(tmp_path, ROOT / "scripts/unix/run_trading_worker.sh")
    python = tmp_path / ".venv/bin/python"
    python.parent.mkdir(parents=True)
    python.write_text("#!/bin/sh\nexit 3\n", encoding="utf-8")
    python.chmod(0o755)
    log = tmp_path / "logs/trading-worker-autostart.log"

    process = subprocess.Popen([str(script)], env={**os.environ})
    try:
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline and not (
            log.is_file() and "exited with 3; restarting in 30s" in log.read_text()
        ):
            time.sleep(0.1)
        assert "exited with 3; restarting in 30s" in log.read_text()
        process.send_signal(signal.SIGTERM)
        assert process.wait(timeout=10) == 0
    finally:
        if process.poll() is None:
            process.kill()
    assert "stopped" in log.read_text()
