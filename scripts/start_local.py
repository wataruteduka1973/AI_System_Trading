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


def check_ports() -> None:
    for port in PORTS:
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
    return [
        [sys.executable, "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", "8000"],
        [node, str(vite), "--host", "127.0.0.1", "--port", "5173", "--strictPort"],
        [sys.executable, "-m", "app.market_data.worker"],
        [sys.executable, "-m", "app.trading.worker"],
    ]


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
    check_ports()
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
        deadline = time.monotonic() + 60
        is_ready = False
        worker_exited = False
        while True:
            if any(process.poll() is not None for process in critical_processes):
                raise RuntimeError(
                    "A server exited. See the output above; the other processes are stopping."
                )
            if (
                worker_processes
                and not worker_exited
                and any(process.poll() is not None for process in worker_processes)
            ):
                worker_exited = True
                print(
                    "\n[WARN] Workerプロセスが終了しました（自動取得は停止中）。"
                    "APIと画面は継続します。[A]キーで再起動できます。",
                    flush=True,
                )
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
    args = parser.parse_args()
    try:
        launch_commands = commands(ROOT)
        check_ports()
        if args.check:
            print("Local setup and ports OK. No servers started; database not checked.")
            return 0
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
