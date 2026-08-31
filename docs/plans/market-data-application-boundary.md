# Market-data Application boundary

## Scope and design (2026-08-30)

Horizon 1 first slice: extract backfill enqueue, requested coverage range resolution, and
subscription changes into `app/market_data/application/use_cases.py`. Preserve HTTP paths,
response schemas, database schema, advisory-lock keys, audit fields and Practice/Testnet boundaries.
API routes retain authentication, dependency wiring, HTTP error translation and background dispatch.
Application functions own workspace access checks, validation, transaction intent and audit writes.
Use plain command dataclasses and application error codes; no FastAPI/schema imports.
SQLAlchemy and existing market-data services remain transitional dependencies, matching connection
verification. No new repository framework, queue service or package dependency is needed.

Data flow: authenticated API -> command -> scoped Application use case -> existing DB/services ->
result -> HTTP schema. Credential preflight is injected and remains local-only. Stop skips preflight.
Backfill commit precedes background dispatch; this slice does not yet close that crash window.

## Order and verification

1. Extract Application functions and input/error types; adapt routes without changing contracts.
2. Test direct use cases (all seven frames, rollback, workspace access, validation, audit), then
   existing API/service regressions and frontend tests/lint/typecheck/build.
3. Review import boundaries, migration absence and package build; update architecture/roadmap.

Risks: losing workspace scope, changing overlap lock order, partial subscription commits, leaking
credential exceptions or changing coverage fallback. Preserve existing regression tests and add
failure-injection tests. UI is unchanged, so browser verification is N/A for this slice.

## State transitions and next slice

- Backfill: queued (job + audit committed together) -> running -> succeeded / failed.
  Overlap rejection rolls back. Existing orphan recovery marks failed/worker_interrupted; it does
  not retry automatically. Owner locks and execution remain in the existing service.
- Subscription: absent/disabled -> enabled, enabled -> disabled; all frames commit atomically.
  Stop does not cancel an in-flight request or manual backfill. Poll failures retain enabled state
  and persist a safe error code; successful polling updates success metadata.
- Next: independent Worker, durable acquisition, heartbeat/lease, bounded retry, stale recovery
  and graceful shutdown; test concurrent workers and process termination against PostgreSQL.

OANDA authenticated real-data verification is explicitly deferred by the user because an API key
cannot currently be generated (NOT VERIFIED). This does not block this approved refactor.
Binance acquisition was confirmed by the user, not re-executed by the agent in this slice.

## Result

Implementation is in place. Backend 80 tests and frontend 11 tests pass. Added direct Application
tests cover scope, range/input validation, all-frame audit writes, legacy single-frame behavior,
preflight/write/commit failure rollback, and no dispatch after failed enqueue. API tests continue
to exercise existing HTTP semantics through TestClient with mocked persistence.

Repository Ruff lint, changed-file formatting, frontend lint/TypeScript/Vite build, and Python
wheel/sdist build passed. Before/after market-data OpenAPI schemas were compared in memory and are
identical. Migration head remains 20260825_0004; no schema/data migration or live-data write occurred.

Verification limits:
- Whole-repository format check still fails on six pre-existing unrelated files: instruments route,
  Binance/OANDA adapters, catalog models, test_binance and test_catalog_api. Not changed by this slice.
- Backend standalone type checking is NOT VERIFIED: no configured checker in pyproject/CI.
- New PostgreSQL failure/concurrency integration and authenticated browser/exchange rechecks are
  NOT VERIFIED in this slice; transaction failures here use mocks. UI unchanged, browser QA N/A.
- Third-party websocket/TestClient deprecation warnings remain (four warnings).

Do not label repository-wide DoD or Horizon 1 complete. The next implementation slice remains
independent durable Worker execution, including real PostgreSQL multi-process/crash tests.

2026-08-31: Worker design and the following DB/lease primitive slice are implemented; see
`durable-market-data-worker.md`. Revision 0005 is tested only on a dedicated database, with no
runtime cutover or user-database migration. Checkpointed page execution is now implemented as
`ExecuteMarketDataPage`, with access revalidation and fenced persistence in infrastructure.
Legacy API/sync/poller are unchanged; independent dispatch and cutover remain the next unit.

2026-08-31 quality follow-up: the six formatting mismatches above are resolved. Mypy is now
configured for app/src/scripts and runs in CI, with Linux and Windows branches checked.
These earlier verification limits are historical; see `python-quality-checks.md` for current results.
