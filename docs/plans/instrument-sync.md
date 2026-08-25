# Instrument sync implementation plan

## Purpose

Synchronize the minimum executable market rules for OANDA `USD_JPY` and Binance Spot
Testnet `BTCJPY` before any candle ingestion or paper-order implementation.

## Scope and data flow

1. An authenticated owner selects an active external account for each exchange.
2. `POST /api/v1/workspaces/{workspace_id}/instruments/sync` resolves only selections
   backed by verified connections.
3. The adapter reads instrument metadata from the official practice/testnet SDK endpoint.
4. The service upserts the existing `fx.instrument` row and writes a sanitized audit entry.
5. `GET /api/v1/workspaces/{workspace_id}/instruments` returns only instruments for
   exchanges selected by that workspace.

Secrets and decrypted external account references remain inside the backend call and are
never persisted in audit payloads or returned by the API. No order endpoint is called.

## Changes

- Extend the existing OANDA and Binance adapters with instrument-rule reads.
- Map the existing `instrument` table in SQLAlchemy; no schema migration is required.
- Add workspace-scoped list/sync API schemas and routes.
- Add a small frontend panel for synchronization status and rule display.
- Add adapter and API regression tests.

## Verification

- Ruff, pytest, frontend ESLint and production build.
- Alembic head check and `git diff --check`.
- Browser smoke test when the local backend and frontend can be started.

## Risks

- Testnet availability and account permissions can make live sync fail; the API reports this
  without changing connection verification state.
- Exchange payload formats vary. Parsers reject incomplete/non-positive trading rules rather
  than inventing defaults.
