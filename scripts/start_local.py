"""One-console Windows launcher. No database changes or dependency installation."""

import argparse
import os
import shutil
import signal
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
import webbrowser
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PORTS = (8000, 5173)
CONSOLE_TITLE = "AI System Trading - Local"
MOCK_OIDC_ISSUER = "http://127.0.0.1:9000"
MOCK_OIDC_PORT = 9000
MOCK_OIDC_SCRIPT = "scripts/mock_oidc_server.py"
TRADING_WORKER_MODULE = "app.trading.worker"
WORKER_LABELS = {
    "app.market_data.worker": "市場データWorker(ローソク足の自動収集)",
    TRADING_WORKER_MODULE: "トレーディングWorker(ペーパートレードの評価)",
    "app.notifications.worker": "通知Worker(取引停止などの通知)",
    MOCK_OIDC_SCRIPT: "開発用ログインサーバー(mock OIDC)",
}


def worker_label(command: list[str]) -> str:
    return WORKER_LABELS.get(command[-1], command[-1])


def configured_oidc_issuer(root: Path) -> str | None:
    """OIDC_ISSUER as the app will see it: the environment first, then `.env`."""
    if "OIDC_ISSUER" in os.environ:
        return os.environ["OIDC_ISSUER"].strip()
    env_file = root / ".env"
    if not env_file.is_file():
        return None
    for line in env_file.read_text(encoding="utf-8").splitlines():
        key, separator, value = line.partition("=")
        if separator and key.strip() == "OIDC_ISSUER":
            return value.strip().strip("\"'")
    return None


def uses_mock_oidc(root: Path) -> bool:
    """The local mock IdP is started only when `.env` points login at it. Login then
    works out of the box (2026-10-03: with the mock not running, the login button led to
    a JSON error); with a real IdP configured it is never started, since the mock grants
    a session to anyone who can reach 127.0.0.1:9000."""
    return configured_oidc_issuer(root) == MOCK_OIDC_ISSUER


def ports_for(launch_commands: list[list[str]]) -> tuple[int, ...]:
    if any(command[-1] == MOCK_OIDC_SCRIPT for command in launch_commands):
        return (*PORTS, MOCK_OIDC_PORT)
    return PORTS


def set_console_title(title: str) -> None:
    if sys.platform == "win32":
        import ctypes

        ctypes.windll.kernel32.SetConsoleTitleW(title)


def alert_worker_stopped(labels: list[str]) -> None:
    """Makes a stopped Worker hard to miss: the console line scrolls away, but the window
    title stays in the taskbar and a warning dialog pops up (on its own thread, so the
    launcher keeps serving keys). The API and screen keep running either way, which is
    how a stopped market-data Worker went unnoticed for two weeks (2026-09-20 to 10-03)."""
    names = "、".join(labels)
    print(f"\n[WARN] {names} が停止しました。[A]キーで再起動できます。", flush=True)
    set_console_title(f"[!] Worker停止: {names} - {CONSOLE_TITLE}")
    if sys.platform == "win32":
        import ctypes
        import threading

        message = f"{names} が停止しました。\n起動ウィンドウで [A] キーを押すと再起動できます。"
        warning_topmost = 0x30 | 0x40000  # MB_ICONWARNING | MB_TOPMOST
        threading.Thread(
            target=ctypes.windll.user32.MessageBoxW,
            args=(None, message, CONSOLE_TITLE, warning_topmost),
            daemon=True,
        ).start()


def check_ports(ports: tuple[int, ...] | None = None) -> None:
    for port in ports if ports is not None else PORTS:
        with socket.socket() as listener:
            if sys.platform == "win32":
                listener.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
            try:
                listener.bind(("127.0.0.1", port))
            except OSError as exc:
                raise RuntimeError(
                    f"Port {port} is busy. Stop the existing server first; it was not changed."
                ) from exc


def commands(root: Path) -> list[list[str]]:
    if not (root / ".env").is_file():
        raise RuntimeError(".env is missing. Complete the setup in README.md first.")
    node = shutil.which("node")
    if node is None:
        conventional = Path(os.environ.get("PROGRAMFILES", r"C:\Program Files")) / "nodejs/node.exe"
        node = str(conventional) if conventional.is_file() else None
    if node is None:
        raise RuntimeError("Node.js was not found. Install Node.js 22 and reopen this launcher.")
    vite = root / "frontend/node_modules/vite/bin/vite.js"
    if not vite.is_file():
        raise RuntimeError("Frontend dependencies are missing. Run npm install in frontend first.")
    launch = [
        [sys.executable, "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", "8000"],
        [node, str(vite), "--host", "127.0.0.1", "--port", "5173", "--strictPort"],
        [sys.executable, "-m", "app.market_data.worker"],
        [sys.executable, "-m", "app.trading.worker"],
        [sys.executable, "-m", "app.notifications.worker"],
    ]
    if uses_mock_oidc(root):
        # Non-critical like the workers: if it stops, login stops but trading does not.
        launch.append([sys.executable, MOCK_OIDC_SCRIPT])
    return launch


def without_trading_worker(launch_commands: list[list[str]]) -> list[list[str]]:
    """For a PC where the trading worker already runs by itself (the logon task of
    scripts/windows/register_trading_worker_task.ps1). A second one is harmless -- an evaluation is
    idempotent per bar -- but it only doubles the work and the log."""
    return [command for command in launch_commands if command[-1] != TRADING_WORKER_MODULE]


def read_revisions(root: Path) -> tuple[str | None, str | None]:
    """`(current revision of the database, newest revision of the code)`; read-only."""
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))
    from alembic.config import Config
    from alembic.script import ScriptDirectory
    from app.core.config import settings
    from sqlalchemy import create_engine, text

    config = Config(str(root / "alembic.ini"))
    config.set_main_option("script_location", str(root / "alembic"))
    head = ScriptDirectory.from_config(config).get_current_head()
    engine = create_engine(settings.database_url, connect_args={"connect_timeout": 3})
    try:
        with engine.connect() as connection:
            current = connection.scalar(text("SELECT version_num FROM alembic_version"))
    finally:
        engine.dispose()
    return current, head


def database_revision_warning(root: Path) -> str | None:
    """Is the database at the newest Alembic revision? A missing migration is how the workers and
    the screens break quietly (the spread history table, for one), and the launcher never migrates
    by itself, so it says so. `None` when it is current. A database that cannot be reached is
    reported too -- the check does not know, rather than assuming all is well."""
    try:
        current, head = read_revisions(root)
    except Exception as exc:
        return (
            f"DBのマイグレーションの状態を確認できませんでした({type(exc).__name__})。"
            "PostgreSQLが起動していて、.envのDATABASE_URLが正しいか確認してください。"
        )
    if current == head:
        return None
    return (
        f"DBのマイグレーションが最新ではありません(現在 {current or '未適用'}、最新 {head})。"
        "このランチャーは適用しません。README の手順で `alembic upgrade head` を実行してください。"
        "未適用のままだと、画面やWorkerが失敗することがあります。"
    )


def stop_processes(processes: list[subprocess.Popen], timeout: int = 10) -> None:
    """Only stop handles created by this invocation; never kill by name or port."""
    for process in reversed(processes):
        if process.poll() is not None:
            continue
        try:
            if sys.platform == "win32":
                stop_signal = signal.CTRL_BREAK_EVENT
            else:
                stop_signal = signal.SIGTERM
            process.send_signal(stop_signal)
            process.wait(timeout=timeout)
        except (OSError, subprocess.TimeoutExpired):
            if process.poll() is None:
                process.kill()
                process.wait(timeout=5)


def start_group(
    processes: list[subprocess.Popen],
    commands_: list[list[str]],
    directories: tuple[Path, ...],
    creation_flags: int,
) -> None:
    """Append newly started processes to `processes` as they launch, one at a time, so a
    failure partway through still leaves the caller able to stop whatever did start."""
    for command, directory in zip(commands_, directories, strict=True):
        processes.append(
            subprocess.Popen(
                command,
                cwd=directory,
                stdin=subprocess.DEVNULL,
                creationflags=creation_flags,
            )
        )


def ready() -> bool:
    # Ignore configured HTTP proxies for local readiness checks.
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    for url in ("http://127.0.0.1:8000/api/v1/health", "http://127.0.0.1:5173/"):
        try:
            with opener.open(url, timeout=0.5) as response:
                if response.status != 200:
                    return False
        except (OSError, urllib.error.URLError):
            return False
    return True


def read_key() -> str:
    if sys.platform != "win32":
        raise RuntimeError("This interactive launcher requires Windows.")
    import msvcrt

    return msvcrt.getwch().lower() if msvcrt.kbhit() else ""


def run_once(
    root: Path,
    launch_commands: list[list[str]],
    open_browser: bool,
    critical_count: int = 2,
    worker_stop_timeout: int = 45,
) -> bool:
    """The first `critical_count` commands (API, frontend) are required and restart together
    on [R]; the remaining commands (the Worker) only restart with the rest on [A] and get a
    longer stop grace period (`worker_stop_timeout`) since a page in flight can take longer."""
    check_ports(ports_for(launch_commands))
    directories = (root, root / "frontend") + (root,) * (len(launch_commands) - 2)
    if sys.platform == "win32":
        creation_flags = subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        creation_flags = 0
    critical_processes: list[subprocess.Popen] = []
    worker_processes: list[subprocess.Popen] = []
    try:
        start_group(
            critical_processes,
            launch_commands[:critical_count],
            directories[:critical_count],
            creation_flags,
        )
        start_group(
            worker_processes,
            launch_commands[critical_count:],
            directories[critical_count:],
            creation_flags,
        )
        print(
            "\n[R] API/画面のみ再起動   [A] すべて再起動(Worker含む)   "
            "[Q] 停止して終了   [Ctrl+C] 停止",
            flush=True,
        )
        set_console_title(CONSOLE_TITLE)
        deadline = time.monotonic() + 60
        is_ready = False
        worker_commands = launch_commands[critical_count:]
        stopped_workers: set[int] = set()
        while True:
            if any(process.poll() is not None for process in critical_processes):
                raise RuntimeError(
                    "A server exited. See the output above; the other processes are stopping."
                )
            newly_stopped = [
                index
                for index, process in enumerate(worker_processes)
                if index not in stopped_workers and process.poll() is not None
            ]
            if newly_stopped:
                stopped_workers.update(newly_stopped)
                alert_worker_stopped([worker_label(worker_commands[i]) for i in newly_stopped])
            worker_exited = bool(stopped_workers)
            key = read_key()
            if key == "q":
                return False
            if key == "a":
                return True
            if key == "r":
                stop_processes(critical_processes)
                critical_processes = []
                start_group(
                    critical_processes,
                    launch_commands[:critical_count],
                    directories[:critical_count],
                    creation_flags,
                )
                is_ready, deadline = False, time.monotonic() + 60
                print("\nAPI/画面を再起動しました。", flush=True)
                continue
            if not is_ready:
                is_ready = ready()
                if is_ready:
                    worker_state = "停止（要確認、[A]で再起動）" if worker_exited else "稼働中"
                    print(
                        "\nReady: http://localhost:5173   API: http://localhost:8000/docs   "
                        f"Worker: {worker_state}",
                        flush=True,
                    )
                    if open_browser:
                        webbrowser.open("http://localhost:5173")
                elif time.monotonic() >= deadline:
                    raise RuntimeError("Startup timed out. Both servers are stopping.")
            time.sleep(0.2)
    finally:
        stop_processes(critical_processes)
        stop_processes(worker_processes, timeout=worker_stop_timeout)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="Check setup/ports without starting")
    parser.add_argument("--no-browser", action="store_true", help="Do not open a browser")
    parser.add_argument(
        "--no-trading-worker",
        action="store_true",
        help="Do not start the trading worker (it already runs as the logon task)",
    )
    args = parser.parse_args()
    try:
        launch_commands = commands(ROOT)
        if args.no_trading_worker:
            launch_commands = without_trading_worker(launch_commands)
        check_ports(ports_for(launch_commands))
        warning = database_revision_warning(ROOT)
        if args.check:
            print("Local setup and ports OK. No servers started.")
            print(f"[WARN] {warning}" if warning else "Database migrations are up to date.")
            return 0
        if warning:
            print(f"[WARN] {warning}", flush=True)
        if sys.platform != "win32":
            raise RuntimeError("This interactive launcher requires Windows.")
        print("Starting local servers. PostgreSQL must already be running.", flush=True)
        while run_once(ROOT, launch_commands, not args.no_browser):
            print("Restarting...", flush=True)
        return 0
    except KeyboardInterrupt:
        print("\nStopped.")
        return 0
    except (OSError, RuntimeError) as exc:
        print(f"[ERROR] {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
