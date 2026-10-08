# Windows local launcher

Status: `[x]` implemented as `start-local.bat` / `scripts/start_local.py`.

## Scope

`start-local.bat` selects a working project Python 3.13 environment and invokes
`scripts/start_local.py`. One console owns two child processes: Uvicorn and Node/Vite.
No package installs, migrations, credentials changes, database service management or foreign
process termination. Existing polling starts normally with the backend, so real-app startup is
not a harmless read-only verification. This change does not make ingestion restart-resilient.

## Decisions

- Resolve paths from the launcher, not the caller's current directory.
- Prefer .venv, then .venv313; probe backend imports without printing configuration/secrets.
- Bind loopback only. Fail if ports 8000/5173 are busy; Vite uses strictPort.
- Invoke Node/Vite directly, not npm/cmd wrappers; no reload supervisor. This keeps exactly two
  owned processes. R restarts them; Q/Ctrl+C stops them. Child stdin is disabled so Vite cannot
  consume the launcher's keys. Browser opens after readiness checks.
- Signal owned process groups for graceful stop, then kill only an unresponsive owned process.
  Use Q rather than closing the console with X; abrupt host termination cannot guarantee cleanup.
- On launch failure, server exit or readiness timeout, stop the other owned process too.

## Verification (2026-08-30)

Added tests for command construction, busy ports, absent setup, R/Q, partial launch failure,
unexpected server exit, timeout, interruption, forced-stop fallback and check-only behavior.
A real two-child OS-process smoke test verifies cleanup without running the trading app.
Backend full-suite results and changed-file Ruff checks are recorded in the delivery response.

Actual project launch is NOT VERIFIED: both project virtual-environment executables fail even
on --version in the agent environment. The bat reports the failure and starts neither server.
Tests use the working portable Python runtime. No exchange request or existing server stop was
performed. Frontend/UI and database schemas are unchanged; browser UI verification is N/A.

## 2026-10-08の更新

- 起動する子プロセスは、API・画面・市場データWorker・トレーディングWorker・通知Worker(・開発用ログインサーバー)。
  README の記述(3プロセス、通知Workerは含めない)が古かったので直した。
- 起動時に、DBの Alembic リビジョンがコードの最新かを**読み取りだけ**で確認し、古い・未適用・確認できないときは `[WARN]` を出す
  (`--check` でも出す)。適用はしない(方針は変えない)。未適用のマイグレーションで画面やWorkerが静かに壊れるのを避けるため。
  DBに接続できないときも、「正常」とは言わず、確認できなかったと言う。
- `--no-trading-worker`: トレーディングWorkerを起動しない。ログオン時の自動起動で動かしている PC 用。
  二重起動は、評価が `(bot_run, candle)` ごとに冪等なので害は無いが、処理とログが倍になる。
- `scripts/windows/run_trading_worker.cmd` は PATH の `python` を使っていた。プロジェクトの依存が入っていない Python だと、
  起動に失敗して30秒ごとに再起動し続ける。bat と同じく `.venv` → `.venv313` を使うようにし、無ければログに書いて終了する。

検証: `tests/test_local_launcher.py`(34件。リビジョンの比較・読めないときの警告・`--no-trading-worker`・`--check`)。
実際の `--check` はローカル DB で「マイグレーションは最新」と答えた。実際の起動(全プロセス)と、自動起動のタスクの実行は未検証。

