# Current and target architecture

## Status and scope

This document is the architecture source of truth for the current modular-monolith phase.
It distinguishes implemented runtime behavior from the intended target structure. Feature plans in
`docs/plans/` describe delivery sequencing but do not override the boundaries defined here.

**Not fully current (last substantially updated 2026-09-16).** Horizon4-lite backtest
(`app/trading/application/{risk_gate,order_flow,trading_halt,backtest_replay}.py`, ADR 0003) and
Horizon5 groups A/D/E (OIDC login + RBAC in `app/security/`, Outbox/Notification skeleton in
`app/notifications/`, CI dependency/SBOM scanning) have since landed and are not reflected in the
module layout or migration sequence below. `docs/plans/horizon5-implementation-plan.md` and
`docs/architecture-alignment-and-long-term-roadmap.md`'s Horizon 4/5 sections are the current source
of truth for that work until this document is refreshed. The one specific claim below that is now
wrong, not just incomplete, is called out inline rather than silently left standing.

The system remains development-only and permits OANDA Practice and Binance Spot Testnet access.
Live trading and real-money order submission are outside the approved boundary.

## Current runtime architecture

The repository currently runs as a FastAPI backend, a React frontend, PostgreSQL, a local encrypted
secret store, and an independently started durable market-data worker process
(`app/market_data/worker/`, launched via `python -m app.market_data.worker`).

```text
React UI
   |
   v
FastAPI routes                    app/market_data/worker (separate process)
   |---- SQLAlchemy models ---- PostgreSQL ----|
   |---- LocalEncryptedSecretStore ---- encrypted local files
   `---- OANDA/Binance clients ---- practice/testnet APIs
```

Implemented safety boundaries:

- Workspace resources are protected by OIDC-authenticated sessions and per-workspace
  Owner/Operator/Viewer roles (`app/security/`), not the development owner token this document
  originally described -- that token-based `require_owner` mechanism was removed entirely in
  Horizon5 Group A. See `docs/plans/horizon5-implementation-plan.md` Units 1/3/4.
- Exchange credentials are encrypted outside the database; the database stores only `secret_ref`.
- External account references are encrypted, hashed, and masked before being exposed.
- Authentication and communication failures have distinct persisted outcomes.
- Exchange adapters reject non-Practice/non-Testnet endpoints.
- Verification and connection changes emit audit records without credential values.

Current structural limitations:

- Several API route modules still mix HTTP translation and persistence. Connection verification
  and market-data enqueue/coverage/subscription orchestration now have Application boundaries.
- The frontend concentrates API access and feature state in `frontend/src/App.tsx`.

## Architecture decision

Use a modular monolith with independently runnable workers. Do not split into networked
microservices at the current scale. Module boundaries are enforced in code first; deployment
boundaries are introduced only for work that needs an independent lifecycle.

The required dependency direction is:

```text
API or Worker -> Application -> Domain
                      |
                      v
                Infrastructure
```

- **API** translates HTTP input, authentication context, application results, and application errors.
- **Worker** acquires durable jobs and invokes the same application use cases as the API.
- **Application** owns use-case orchestration, transaction intent, state transitions, and audit intent.
- **Domain** owns business rules and values without FastAPI, SQLAlchemy, SDK, or filesystem imports.
- **Infrastructure** implements persistence, secret storage, exchange access, and other I/O ports.

Application modules must not raise `HTTPException` or return FastAPI response schemas. API modules
must not implement exchange-specific verification or account synchronization rules.

## Target modules

```text
app/
  bootstrap/          FastAPI construction and dependency wiring
  shared/             configuration, database, security, and audit infrastructure
  workspaces/         workspace lifecycle and ownership boundary
  connections/        credentials, verification, account sync, disable/delete lifecycle
  exchanges/          exchange ports and OANDA/Binance adapters
  instruments/        instrument discovery and workspace availability
  market_data/        candles, coverage, subscriptions, and backfill use cases
  paper_trading/      future simulation, portfolio, risk, and execution ledger
worker/                independently runnable polling and backfill entry points
```

Each business module may contain `domain`, `application`, `infrastructure`, and `api` packages when
those layers are needed. Empty layers are not created in advance.

## Connection verification boundary

Connection verification is the first extracted application use case.

```text
POST /connections/{id}/verify
   -> API authentication and dependency wiring
   -> VerifyConnectionUseCase
      -> load workspace-scoped connection
      -> enforce Practice/Testnet environment
      -> load credentials by opaque secret reference
      -> call exchange adapter
      -> encrypt/hash/mask external account identity
      -> persist connection outcome, account metadata, and audit record
   -> API result/error translation
```

Implemented in this slice:

- `app/connections/application/verify_connection.py` owns the verification orchestration.
- Application-specific result data classes do not depend on HTTP response models.
- `ConnectionVerificationError` carries a stable application error code without HTTP semantics.
- `app/api/routes/connections.py` maps results and errors to the existing public API contract.

Transitional dependencies that remain intentionally:

- The use case currently receives a SQLAlchemy `Session`, concrete exchange clients, and the local
  secret store. Repository and port interfaces will be introduced only when the next extraction
  demonstrates a concrete need.
- Credential replacement still coordinates secret rotation in the route before invoking the shared
  verification use case. It should become a separate application use case in a later slice.

## Market-data Application boundary (2026-08-30)

`app/market_data/application/use_cases.py` owns `enqueue_backfill`, `get_coverage` and
`update_subscriptions`. They accept plain inputs/command dataclasses, enforce workspace scope,
and return ORM entities or coverage data without importing FastAPI or response schemas.
Application error codes are translated to the existing HTTP contract by the route.
Credential preflight is injected; API wiring constructs the existing ingestion service and
translates secret errors. Disabling collection never needs credential decryption.

Enqueue and subscription writes include their audit records in one transaction and roll back
on failure. Both legacy single-frame and bulk changes use the same instrument/workspace lock.
Coverage range validation and latest-job fallback moved out of the route; calculation still
delegates to the existing service shared with backfill execution.

SQLAlchemy/services are intentional transitional dependencies, as with connection verification.
Read-only candle/job/subscription listing still lives in the route. Backfill execution and polling
now run in the separate Worker process described under "Worker" below, not in the API process.
See `docs/plans/market-data-application-boundary.md` for the Application-extraction transitions
and `docs/plans/durable-market-data-worker.md` for the Worker delivery that followed it.

## Transaction and secret consistency

PostgreSQL and the filesystem secret store cannot participate in one atomic transaction. Operations
that create or rotate secrets must therefore define compensating behavior:

1. Write the new encrypted secret.
2. Persist its opaque reference and audit intent.
3. Delete the new secret if database persistence fails.
4. Delete the old secret only after the new reference is committed.

Credential values, decrypted account references, and `secret_ref` values must never appear in API
responses, audit payloads, normal logs, or job error messages.

## Worker (implemented, 2026-09-15/16)

Polling and backfill execution run in a separately started worker process
(`app/market_data/worker/`, entry point `python -m app.market_data.worker`), not inside the FastAPI
lifespan. Jobs/subscriptions use database-backed acquisition with a feed-scoped lease
(`app/market_data/infrastructure/leases.py`), retry count, and stale-lease recovery. The web
application only enqueues work (`app/api/routes/market_data.py`); it no longer dispatches or polls.
No external queue service was needed.

Design docs `docs/design/modules/durable-market-data-worker.md` and
`docs/design/database/durable-market-data-worker.md`, with acceptance tests and sequencing in
`docs/plans/durable-market-data-worker.md`, describe the feed-scoped leases, fenced commits, page
checkpoints, candidate discovery, fair round-robin scheduling, and the legacy cutover as
implemented and verified (dedicated PostgreSQL plus the local-user database). The Worker refuses
to start unless the database is at the required Alembic revision (`20260831_0005`). Fair scheduling
means a long backfill never starves a polling subscription on the same or other feeds
(`app/market_data/worker/runner.py`). `scripts/start_local.py` starts the Worker as a third,
non-critical process; `[R]` restarts only the API/frontend, `[A]` restarts everything including the
Worker (its own 45s stop grace period), and the API/frontend never claim the Worker is running when
it has exited.

## Migration sequence

1. Extract connection verification without changing API or database contracts. **Implemented.**
2. Extract credential rotation and connection lifecycle use cases.
3. Split connection/instrument/market-data models and schemas from the historical catalog modules.
   **Implemented (2026-09-16):** `app/models/catalog.py` and `app/schemas/catalog.py` are split into
   `workspace.py`/`audit.py`/`connections.py`/`instruments.py`/`market_data.py` (models) and
   `base.py`/`workspace.py`/`connections.py`/`instruments.py`/`market_data.py` (schemas);
   `app/api/routes/catalog.py` is split into `workspaces.py` and `connections.py`. API paths,
   response shapes, and the database schema are unchanged.
4. Introduce durable worker acquisition and move polling/backfill out of FastAPI lifespan.
   **Implemented (2026-09-15/16).**
5. Split frontend API access and state by connection and market-data features.
6. Resolve the `app` versus `src/ai_system_trading` packaging duplication.
   **Implemented (2026-09-16): `src/ai_system_trading` removed; `app` is the only package.**
7. Add the paper-trading module only after market-data durability and risk-halt contracts exist.

## Change rules

- Preserve paper-only behavior unless a separately approved security architecture change says
  otherwise.
- Preserve workspace scoping through every application and persistence operation.
- Treat design plans as target evidence, not proof of implemented runtime behavior.
- Avoid new infrastructure, generic repositories, or abstractions without an immediate use case.
- Public API or database breaking changes require an explicit decision and migration plan.
