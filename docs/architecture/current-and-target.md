# Current and target architecture

## Status and scope

This document is the architecture source of truth for the current modular-monolith phase.
It distinguishes implemented runtime behavior from the intended target structure. Feature plans in
`docs/plans/` describe delivery sequencing but do not override the boundaries defined here.

The system remains development-only and permits OANDA Practice and Binance Spot Testnet access.
Live trading and real-money order submission are outside the approved boundary.

## Current runtime architecture

The repository currently runs as a FastAPI backend, a React frontend, PostgreSQL, a local encrypted
secret store, and an in-process market-data polling worker.

```text
React UI
   |
   v
FastAPI routes
   |---- SQLAlchemy models ---- PostgreSQL
   |---- LocalEncryptedSecretStore ---- encrypted local files
   `---- OANDA/Binance clients ---- practice/testnet APIs

FastAPI lifespan ---- in-process polling worker
```

Implemented safety boundaries:

- Workspace resources are protected by the development owner token.
- Exchange credentials are encrypted outside the database; the database stores only `secret_ref`.
- External account references are encrypted, hashed, and masked before being exposed.
- Authentication and communication failures have distinct persisted outcomes.
- Exchange adapters reject non-Practice/non-Testnet endpoints.
- Verification and connection changes emit audit records without credential values.

Current structural limitations:

- Several API route modules still mix HTTP translation and persistence. Connection verification
  and market-data enqueue/coverage/subscription orchestration now have Application boundaries.
- `app/api/routes/catalog.py`, `app/models/catalog.py`, and `app/schemas/catalog.py` group multiple
  business capabilities under the historical catalog name.
- The market-data worker starts inside the web process, so multiple web processes can duplicate
  polling and process restarts can interrupt work.
- Backfill dispatch uses process-local background execution rather than durable job acquisition.
- The frontend concentrates API access and feature state in `frontend/src/App.tsx`.
- `src/ai_system_trading` is a packaging shell while the runtime implementation is in `app`.

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
- `app/api/routes/catalog.py` maps results and errors to the existing public API contract.

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
Read-only candle/job/subscription listing still lives in the route. Actual backfill execution,
process-local dispatch and polling remain unchanged: this extraction is not durable Worker delivery.
See `docs/plans/market-data-application-boundary.md` for transitions and verification limits.

## Transaction and secret consistency

PostgreSQL and the filesystem secret store cannot participate in one atomic transaction. Operations
that create or rotate secrets must therefore define compensating behavior:

1. Write the new encrypted secret.
2. Persist its opaque reference and audit intent.
3. Delete the new secret if database persistence fails.
4. Delete the old secret only after the new reference is committed.

Credential values, decrypted account references, and `secret_ref` values must never appear in API
responses, audit payloads, normal logs, or job error messages.

## Worker target

Polling and backfill execution will move to a separately started worker in a later slice. Before more
than one worker can run, jobs/subscriptions need database-backed acquisition with a lease, retry
count, and stale-lease recovery. The web application may enqueue work but must not own its lifecycle.

No external queue service is required for the first worker extraction.

The 2026-08-31 detailed design is documented in
`docs/design/modules/durable-market-data-worker.md` and
`docs/design/database/durable-market-data-worker.md`, with acceptance tests and sequencing in
`docs/plans/durable-market-data-worker.md`. It specifies feed-scoped leases, fenced commits,
page checkpoints and a stop-the-world legacy cutover. These are design decisions, not implemented
runtime behavior; the in-process worker limitations above still apply.

The following storage-only slice adds revision `20260831_0005` and isolated Worker mappings plus
lease primitives under `app/market_data/infrastructure/`. It does not wire them into API/lifespan.
Legacy ORM SQL stays compatible with revision 0004. Storage fencing, concurrent acquisition and
migration preservation were tested on a dedicated PostgreSQL instance; the production/local-user
database was not migrated. The next slice adds `ExecuteMarketDataPage`, detached access snapshots,
fenced page persistence and resumable final validation. SDK calls execute outside transactions.
It reuses legacy candle persistence/quality helpers through capability infrastructure, without
changing legacy runtime wiring. Independent dispatch, heartbeat supervision and cutover remain next.

## Migration sequence

1. Extract connection verification without changing API or database contracts. **Implemented.**
2. Extract credential rotation and connection lifecycle use cases.
3. Split connection models and schemas from the historical catalog modules.
4. Introduce durable worker acquisition and move polling/backfill out of FastAPI lifespan.
5. Split frontend API access and state by connection and market-data features.
6. Resolve the `app` versus `src/ai_system_trading` packaging duplication.
7. Add the paper-trading module only after market-data durability and risk-halt contracts exist.

## Change rules

- Preserve paper-only behavior unless a separately approved security architecture change says
  otherwise.
- Preserve workspace scoping through every application and persistence operation.
- Treat design plans as target evidence, not proof of implemented runtime behavior.
- Avoid new infrastructure, generic repositories, or abstractions without an immediate use case.
- Public API or database breaking changes require an explicit decision and migration plan.
