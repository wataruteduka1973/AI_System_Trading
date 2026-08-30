# Windows local launcher

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
