"""Runs the backend tests on Linux with a real PostgreSQL, in Docker (docker-compose.test.yml).

    python scripts/test_in_docker.py [pytest arguments]

The same locked dependency versions as CI, every test including the PostgreSQL ones, in one
session (CI runs them in separate jobs). The database is created empty for each run and removed
afterwards: the PostgreSQL tests refuse a database that already holds rows. Needs Docker; macOS is
not testable this way (Docker runs Linux), the macOS CI job covers it.
"""

import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
COMPOSE = ["docker", "compose", "-f", str(ROOT / "docker-compose.test.yml")]


def run_command(pytest_args: list[str]) -> list[str]:
    command = [*COMPOSE, "run", "--rm", "--build", "tests"]
    return [*command, "python", "-m", "pytest", *pytest_args] if pytest_args else command


def main(argv: list[str]) -> int:
    if shutil.which("docker") is None:
        print(
            "[ERROR] Docker was not found. Install Docker Desktop (see README.md).", file=sys.stderr
        )
        return 1
    reset = [*COMPOSE, "down", "--volumes", "--remove-orphans"]
    subprocess.run(reset, check=False, cwd=ROOT)
    try:
        return subprocess.run(run_command(argv), check=False, cwd=ROOT).returncode
    finally:
        subprocess.run(reset, check=False, cwd=ROOT)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
