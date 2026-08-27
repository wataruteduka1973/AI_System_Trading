# Market data, chart, and real-time implementation roadmap

Last reviewed: 2026-08-27

## Purpose

This document is the current implementation roadmap for market-data acquisition and visualization.
It distinguishes implemented runtime behavior from target design and defines the order, entry
conditions, and completion criteria for the remaining work.

The system remains paper-only. Account verification and any future order execution are restricted to
OANDA Practice and Binance Spot Testnet. No production order endpoint is introduced by this roadmap.

## Status legend

- `[x]` implemented and verified in the repository
- `[~]` partially implemented; listed completion work remains
- `[ ]` not implemented
- `Approval gate` requires an explicit product/data-source decision before implementation

## Product and page boundaries

- Development page: API/DB status, owner-token entry, and Workspace selection.
- Connection page: credentials, verification results, account selection, disable/delete, and
  instrument-rule synchronization.
- OANDA market page: OANDA instruments, historical candles, coverage, and future real-time status.
- Binance market page: Binance instruments, historical candles, Testnet limitations, coverage, and
  future real-time status.

Routes implemented in Phase 0:

```text
/                                        -> development status / entry page
/workspaces/:workspaceId/connections     -> connection and account management
/workspaces/:workspaceId/markets/oanda   -> OANDA market visualization
/workspaces/:workspaceId/markets/binance -> Binance market visualization
```

The Workspace ID may appear in the URL. Owner tokens and exchange credentials must never appear in
URLs, browser storage, logs, ordinary database columns, or API responses.

## Current implementation baseline

### Connection and instrument foundation

- [x] Workspace-scoped OANDA Practice and Binance Spot Testnet connections.
- [x] Encrypted local credential storage and masked account identifiers.
- [x] Credential replacement, re-verification, disable, and delete operations.
- [x] Distinct saved, authentication-success, authentication-failure, and communication-failure
  states.
- [x] Workspace account selection and verified-connection enforcement.
- [x] Read-only OANDA USD/JPY and Binance BTC/JPY instrument-rule synchronization.
- [x] Audit events without credential or secret-reference disclosure.

### Candle ingestion and coverage

- [x] Manual backfill jobs for supported timeframes.
- [x] Periodic backend collection subscriptions.
- [x] Final-candle upsert storage.
- [x] Requested and actually stored ranges are calculated separately.
- [x] Coverage statuses include `complete`, `partial_source_limit`, `partial_gaps`, and `empty`.
- [x] Binance Testnet periodic-reset limitations are visible and are not reported as one year
  acquired.
- [x] Coverage results are stored in `backfill_job.validation_result` for new completed jobs.
- [~] Detailed ingestion accounting, gap persistence, duplicate-job rejection, and cursor pagination
  remain in Phase A.

### Chart and page architecture

- [x] React Router 8.3 declarative routing and shared navigation shell.
- [x] Separate connection, OANDA market, Binance market, and not-found routes.
- [x] Exchange-specific instrument filtering and source/environment notices.
- [x] Lightweight Charts 5.2 candlestick series replaced the raw SVG close-price line.
- [x] JST time axis, precision-aware price axis, crosshair, and OHLCV display.
- [x] Requested and stored coverage are shown on the market page.
- [~] The chart still loads only the latest API page and does not request older rows while scrolling.
- [~] Displayed range, chart-specific tests, and complete tooltip provenance remain in Phase B.

React Router 8.3 replaced the originally proposed 7.x line because the available 7.x release
produced unresolved high-severity dependency audit findings on 2026-08-26. The chosen 8.3 release
passed the dependency audit and provides the required declarative APIs.

## Architecture boundaries

### Historical data

```text
Exchange REST -> normalized final candles -> PostgreSQL -> paginated HTTP API -> chart
```

- Candle responses never contain credentials or secret references.
- Every stored candle retains its source and quality status.
- Testnet source limitations are expected and must remain visible.
- A public historical source may not silently overwrite or mix with Testnet data.

### Future real-time data

```text
Exchange stream -> backend normalizer -> provisional WebSocket update -> browser chart
                                      -> finalized candle upsert -> PostgreSQL
```

- The browser never connects to an exchange with account credentials.
- One backend stream is shared for the same exchange, symbol, and timeframe where practical.
- Provisional events update only the current visual candle.
- Only finalized candles are persisted as final.
- Reconnection runs a bounded REST gap fill from the latest stored close time.
- Browser WebSocket authorization uses a short-lived, one-time ticket. The owner token is never
  placed in a WebSocket URL.

Proposed endpoints:

```text
POST /workspaces/{workspace_id}/market-stream-tickets
WS   /ws/v1/market-stream?ticket=<one-time-ticket>
```

The ticket is scoped to Workspace, exchange, symbol, and timeframe and expires within one minute.

## Delivery roadmap

### Phase 0 — page separation and navigation: complete

- [x] Add React Router 8.3 in declarative mode.
- [x] Add the shared application shell and Workspace-scoped navigation.
- [x] Separate connection management from OANDA and Binance market pages.
- [x] Preserve Workspace navigation without persisting the owner token.
- [x] Add route, navigation, unknown-route, and responsive-navigation coverage.
- [x] Verify direct market URL and 404 behavior in a browser without console errors.

Completion evidence: frontend lint, five route/navigation tests, production build, dependency audit,
backend regression tests, and unauthenticated browser route checks passed during Phase 0 delivery.

### Phase A — coverage correctness and historical API completion: next

Status: `[~]` foundation implemented; completion work remains.

#### Deliverables

- [ ] Introduce an `IngestionReport` containing:
  - requested start/end
  - actual first/last candle time
  - source rows received
  - rows inserted or updated
  - empty source windows
  - expected/stored/missing counts
  - bounded gap samples
  - coverage status and safe reason code
- [ ] Persist actionable discontinuities in `market_data_gap`.
- [ ] Account for OANDA trading-week closures so weekends do not become false gaps.
- [ ] Reject concurrent duplicate backfills for the same Workspace, instrument, timeframe, and
  overlapping range.
- [ ] Add cursor pagination to the candle API:

```text
GET /workspaces/{workspace_id}/instruments/{instrument_id}/candles
    ?timeframe=1d&limit=500&before=<timestamp>
```

- [ ] Preserve the current coverage endpoint and add explicit requested-range parameters where
  needed.
- [ ] Add complete, source-limited, empty, duplicate-job, internal-gap, and Workspace-isolation
  tests.

#### Entry condition

- Phase 0 routes remain stable and the current backfill/coverage regression suite passes.

#### Completion criteria

- Technical job success and data completeness are separate states.
- A 365-day request returning roughly 20 Testnet daily candles is `partial_source_limit`.
- Internal missing candles are `partial_gaps`, not source limitations.
- Duplicate overlapping jobs do not run concurrently.
- More than 500 stored rows can be traversed without loading the entire dataset at once.

### Phase B — chart interaction and large-history completion

Status: `[~]` candlestick replacement implemented; incremental history remains.

#### Deliverables

- [ ] Extract the chart from the application container into a dedicated, testable component.
- [ ] Show requested, stored, and currently displayed ranges separately.
- [ ] Include source and quality status in the crosshair details.
- [ ] Load older candles when the user scrolls toward the left boundary.
- [ ] Merge paginated rows without duplicate timestamps or viewport jumps.
- [ ] Show initial-loading, loading-older, empty, and API-failure states.
- [ ] Verify OANDA and Binance price precision and timezone formatting.
- [ ] Add component tests and browser checks for crosshair, pan, zoom, incremental loading, and
  responsive layout.

#### Entry condition

- Phase A cursor pagination and stable candle ordering are complete.

#### Completion criteria

- The chart can navigate beyond the latest 500 rows.
- A one-minute year is never sent to or rendered by the browser in one response.
- Loading older rows keeps the visible logical position stable.
- Exact JST timestamp and OHLCV/source/quality values are available for a selected candle.

### Phase B2 — real-time market updates

Status: `[ ]` not implemented.

#### Deliverables

- [ ] Add OANDA and Binance backend stream adapters and normalized provisional candle events.
- [ ] Add short-lived stream tickets and Workspace authorization.
- [ ] Add heartbeat, reconnect, delayed, and disconnected states.
- [ ] Deduplicate events and handle late events and timeframe rollover.
- [ ] Reconcile missing final candles through bounded REST gap fill after reconnect.
- [ ] Update the chart with `series.update(...)` rather than replacing all chart data.
- [ ] Persist only finalized candles as final data.
- [ ] Test disconnect/reconnect, duplicates, late events, rollover, ticket expiry, and Workspace
  isolation.

#### Entry condition

- Phase A coverage/gap detection and Phase B incremental chart loading are complete.

#### Completion criteria

- The current candle updates without a full chart reload.
- Stream state is visible to the user.
- A disconnect automatically reconnects and reconciles missing finalized candles.
- No owner token or exchange credential is exposed to the WebSocket URL or browser payload.

### Phase C — optional one-year Binance historical source

Status: `Approval gate`; not required for Testnet connection validation.

#### Decision required before implementation

- Confirm that one-year Binance BTC/JPY history is a product requirement.
- Verify exact public symbol and interval availability.
- Approve source provenance, precedence, and conflict behavior.
- Decide whether public data fills gaps only or forms a separate dataset.

#### Deliverables after approval

- [ ] Add a separate read-only public historical adapter.
- [ ] Record source provenance on every candle.
- [ ] Audit source-mixing or precedence decisions without secrets.
- [ ] Backfill approved gaps and re-run Phase A validation.
- [ ] Keep order execution and account credentials on Testnet/Practice only.

#### Completion criteria

- The UI identifies the source and final available range accurately.
- Public data cannot enable production order execution.
- Conflicts are deterministic, tested, and auditable.

### Phase D — paper-trading foundation

Status: `[ ]` future design phase; implementation requires a separate approved specification.

Phase D starts only after reliable historical and real-time market data are complete. Proposed scope:

- paper-order model and state machine
- simulated fills, fees, spread, and slippage
- positions, balances, and margin representation
- Workspace-scoped risk limits and emergency stop
- order/fill audit events
- deterministic replay and test fixtures

External production orders remain out of scope. Before implementation, define order types, fill
rules, portfolio accounting, risk limits, idempotency, and acceptance tests in a separate design
document.

## Global quality and security requirements

Every phase must preserve:

- Workspace isolation for reads, writes, streams, and background jobs.
- OANDA Practice / Binance Spot Testnet execution boundaries.
- No credentials, plaintext secrets, or `secret_ref` values in logs, ordinary DB columns, audit
  payloads, URLs, or API responses.
- Safe error codes that distinguish authentication, communication, configuration, and data-quality
  failures.
- Auditability for credential, connection, source-precedence, subscription, and future order state
  changes.
- Backend lint/tests, frontend lint/tests/typecheck/build, dependency audit, migration checks where
  applicable, and browser verification for UI changes.

## Known risks

- Binance Testnet may reset periodically; locally collected data is the durable Testnet record.
- One year of one-minute continuous data is about 525,600 rows per instrument and requires cursor
  pagination and bounded browser rendering.
- OANDA requires a trading calendar to avoid false weekend and holiday gaps.
- Mixing Testnet and public data without explicit provenance and precedence is prohibited.
- Background polling and future streams must not create duplicate writes or exceed provider limits.

## Immediate next implementation slice

The next approved development slice should complete the Phase A/B dependency boundary:

1. Add cursor pagination and deterministic candle ordering.
2. Add duplicate-backfill rejection and persistent internal-gap reporting.
3. Complete `IngestionReport` and OANDA calendar-aware coverage validation.
4. Add chart-side incremental history loading and displayed-range reporting.
5. Add API, service, component, Workspace-isolation, and browser regression tests.

After this slice meets the Phase A and Phase B completion criteria, proceed to Phase B2 real-time
market updates. Phase C remains optional and requires explicit approval. Phase D requires its own
requirements and design review.
