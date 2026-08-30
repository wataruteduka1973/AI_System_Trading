# Market screen and interrupted backfill recovery

## Scope (2026-08-30)

### Follow-up: instrument-wide collection and credential errors

Automatic collection controls now operate on all seven timeframes, independent of chart selection.
The UI uses a single PUT /workspaces/{workspace_id}/market-data-subscriptions request with
instrument_id and enabled. All seven rows and their audit records commit together under an
instrument/Workspace advisory lock. The original singular endpoint remains compatible and uses the
same lock. Manual historical acquisition remains scoped to the selected display timeframe.

Live diagnosis found InvalidToken when decrypting the selected Binance connection with the current
SECRET_ENCRYPTION_KEY. This is separate from the previously recovered orphan job. A changed or
mismatched key cannot be repaired by retrying the download. Restore the correct key securely or
replace and reverify exchange credentials in connection management; never paste secrets in chat.
Do not regenerate the encryption key merely to rotate DEV_OWNER_TOKEN.

Backfill and collection enable now validate local credential readability before creating a job or
enabling subscriptions. Stop does not require decryptability. Worker errors use credentials_unreadable
or credentials_missing; the UI shows recovery guidance instead of an unexplained configuration_error.
No ciphertext, encryption key, credentials, or live subscription setting was changed in this follow-up.

Verification: backend 61 tests, frontend 11 tests, changed Python lint/format, frontend lint and
TypeScript/build passed. A fake-response browser fixture verified both bulk buttons, the credential
error guidance and stop after enable failure; no console errors. Fixture removed after verification.
At that verification point, actual exchange acquisition was blocked pending credential recovery.
Subsequently the user confirmed successful Binance acquisition (2026-08-30); this is user-reported
verification, not a new agent-triggered exchange request. OANDA real-data checks are deferred at
the user's request because an API key cannot currently be generated.

Repair chart initialization after loading, stale HTTP responses after timeframe changes,
timeframe-specific backfill status, and interrupted backfills. Preserve existing candles,
API defaults, Workspace authorization and Practice/Testnet-only boundaries.

## Design and order

1. Regression test the loading -> data -> ready chart lifecycle; initialize only when its DOM exists.
2. Fence market responses by selection generation and request order, including older-page loads.
3. Filter job status by timeframe; serialize submissions and distinguish polling from manual jobs.
4. Protect each running backfill with a PostgreSQL session advisory lock on a dedicated connection.
   Before duplicate rejection, recover overlapping jobs older than five minutes only if the same
   lock can be acquired transactionally. Mark failed with worker_interrupted and audit; never delete.
5. Show per-timeframe collection status and an explicit all-timeframes stop action for this instrument.

Data flow: UI selection -> scoped read APIs -> guarded state update -> mounted chart.
Backfill creation -> overlap serialization -> orphan recovery -> queued -> ownership lock -> work.

## Risks and verification

All web processes must run the new lock-aware code before recovery is used; legacy workers do not
hold these locks. A missing lock means no active new-version owner, not merely a slow job.
Session locks require a pinned connection and release in finally. This is not a durable queue;
automatic retries and independent workers remain Horizon 1 work.
Stopping polling does not cancel an already executing request or a manual backfill.

Tests: loading lifecycle, stale responses, timeframe filtering, active-lock protection, orphan audit,
backend regression, frontend tests/lint/build, and browser UI verification where authentication allows.

## Status

Implemented. Backend 56 tests and frontend 11 tests pass. Changed Python files pass Ruff lint/format;
frontend ESLint and TypeScript/production build pass. PostgreSQL session-lock exclusion and release
were checked with two live connections. No schema change was required.

Browser verification used an isolated fake-response fixture with the actual App/CandleChart:
1m -> 5m -> 1d retained a rendered chart, and all-timeframes stop changed the visible active list to
none. The temporary fixture was removed. Authenticated live-market UI and a new exchange backfill
are NOT VERIFIED; no real token was entered in the browser and no new acquisition was triggered.

One legacy 1m job started on 2026-08-25 was rechecked on 2026-08-30. No other DB transaction was
active; the identified orphan was changed to failed/worker_interrupted with an audit record.
Existing candles and live subscription settings were preserved.

Restart all API processes to load the ownership-lock implementation before requesting another
backfill, then reload the frontend. Running old and new worker code together is unsupported.
User changes to requirements.txt and Definition of Done are preserved. No credential changes.
Existing repository-wide formatting discrepancies outside this change remain out of scope.
