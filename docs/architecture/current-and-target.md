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

- API route modules contain HTTP translation, orchestration, persistence, state transitions, and
  audit behavior.
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
