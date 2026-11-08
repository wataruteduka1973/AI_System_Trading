import importlib.util
from pathlib import Path

import pytest

spec = importlib.util.spec_from_file_location(
    "test_in_docker", Path(__file__).resolve().parents[1] / "scripts/test_in_docker.py"
)
runner = importlib.util.module_from_spec(spec)
spec.loader.exec_module(runner)


def test_by_default_the_container_runs_its_own_default_command() -> None:
    assert runner.run_command([])[-4:] == ["run", "--rm", "--build", "tests"]


def test_pytest_arguments_are_passed_through_to_the_container() -> None:
    command = runner.run_command(["-k", "ledger", "-x"])
    assert command[command.index("tests") + 1 :] == ["python", "-m", "pytest", "-k", "ledger", "-x"]


def test_without_docker_it_says_so_and_runs_nothing(monkeypatch, capsys) -> None:
    monkeypatch.setattr(runner.shutil, "which", lambda _: None)
    ran = []
    monkeypatch.setattr(runner.subprocess, "run", lambda *args, **kwargs: ran.append(args))
    assert runner.main([]) == 1
    assert "Docker was not found" in capsys.readouterr().err
    assert ran == []


@pytest.mark.parametrize("exit_code", [0, 3])
def test_the_database_is_reset_before_and_after_and_the_test_result_is_returned(
    monkeypatch, exit_code
) -> None:
    monkeypatch.setattr(runner.shutil, "which", lambda _: "docker")
    calls = []

    def fake_run(command, **kwargs):
        calls.append(command)

        class Result:
            returncode = exit_code if "run" in command else 0

        return Result()

    monkeypatch.setattr(runner.subprocess, "run", fake_run)
    assert runner.main([]) == exit_code
    assert ["down" in call for call in calls] == [True, False, True]
    assert "run" in calls[1]
