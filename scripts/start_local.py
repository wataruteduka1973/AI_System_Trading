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
            if os.name == "nt":
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
    ]


def stop_processes(processes: list[subprocess.Popen]) -> None:
    """Only stop handles created by this invocation; never kill by name or port."""
    for process in reversed(processes):
        if process.poll() is not None:
            continue
        try:
            process.send_signal(signal.CTRL_BREAK_EVENT if os.name == "nt" else signal.SIGTERM)
            process.wait(timeout=10)
        except (OSError, subprocess.TimeoutExpired):
            if process.poll() is None:
                process.kill()
                process.wait(timeout=5)


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
    import msvcrt

    return msvcrt.getwch().lower() if msvcrt.kbhit() else ""


def run_once(root: Path, launch_commands: list[list[str]], open_browser: bool) -> bool:
    check_ports()
    processes: list[subprocess.Popen] = []
    try:
        for command, directory in zip(launch_commands, (root, root / "frontend"), strict=True):
            processes.append(
                subprocess.Popen(
                    command,
                    cwd=directory,
                    stdin=subprocess.DEVNULL,
                    creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0,
                )
            )
        print("\n[R] Restart both servers   [Q] Stop and exit   [Ctrl+C] Stop", flush=True)
        deadline = time.monotonic() + 60
        is_ready = False
        while True:
            if any(process.poll() is not None for process in processes):
                raise RuntimeError(
                    "A server exited. See the output above; both servers are stopping."
                )
            key = read_key()
            if key in {"r", "q"}:
                return key == "r"
            if not is_ready:
                is_ready = ready()
                if is_ready:
                    print(
                        "\nReady: http://localhost:5173   API: http://localhost:8000/docs",
                        flush=True,
                    )
                    if open_browser:
                        webbrowser.open("http://localhost:5173")
                elif time.monotonic() >= deadline:
                    raise RuntimeError("Startup timed out. Both servers are stopping.")
            time.sleep(0.2)
    finally:
        stop_processes(processes)


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
        if os.name != "nt":
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
