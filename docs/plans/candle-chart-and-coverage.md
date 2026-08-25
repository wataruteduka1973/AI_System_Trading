# Candle chart and historical coverage implementation plan

## Objective

- Show real OHLC candles with concrete time and price axes.
- Show timestamp and OHLCV values under a crosshair.
- Distinguish the requested historical range from the range actually returned by the source.
- Never describe a partial response as "one year acquired".

## Agreed product boundary

The existing screen remains a development and connection-validation screen. Binance Spot Testnet's
currently available range is acceptable there; it does not need to simulate a full production
history.

Production-grade market visualization is separated from connection administration:

- Connection page: workspace/account selection, credential state, verification, disable/delete,
  instrument-rule sync, and a small validation-data preview.
- OANDA market page: OANDA instruments, historical candles, real-time updates, coverage, and market
  status.
- Binance market page: Binance instruments, historical candles, real-time updates, coverage, and
  market status.

The three requested corrections are implemented only after the page/data boundaries below are
approved. No public/live execution endpoint is introduced by this design.

## Navigation and page architecture

Adopt React Router 7 in declarative mode. The current application is a client-rendered Vite SPA, so
framework mode and server rendering are unnecessary.

```text
/                                      -> development status / entry page
/workspaces/:workspaceId/connections   -> connection and Testnet/Practice validation
/workspaces/:workspaceId/markets/oanda -> OANDA market visualization
/workspaces/:workspaceId/markets/binance -> Binance market visualization
```

Use a shared application shell containing workspace selection and navigation. Exchange pages are
separate route modules but compose shared primitives:

```text
AppShell
├── ConnectionManagementPage
└── ExchangeMarketPage
    ├── MarketHeader
    ├── InstrumentTimeframeSelector
    ├── CoverageSummary
    ├── CandlestickChart
    ├── RealtimeStatus
    └── MarketDataTable
```

`ExchangeMarketPage` receives an exchange descriptor rather than duplicating chart and fetching
logic. Exchange-specific symbol rules, market calendars, data source badges, and error messages stay
in adapter/configuration modules.

## Data-source and execution boundaries

- Account verification and all future order execution remain OANDA Practice / Binance Spot Testnet.
- The existing Testnet/Practice validation preview continues to use its selected connection.
- Historical and real-time sources for the market pages are selected independently from execution.
- Every candle carries visible source and environment provenance.
- Production public market data may be read without enabling production order APIs or storing
  production trading credentials.
- Mixing sources under one candle business key requires an approved precedence rule and audit entry.

The initial market pages may start with Testnet/Practice data, but must show the source limitation.
A full-history public adapter remains a later approval gate.

## Real-time update design

Browser clients do not connect directly to exchanges. The backend owns exchange connections,
normalization, reconnection, rate limits, and auditing.

```text
Exchange REST -> historical backfill -> PostgreSQL final candles
Exchange stream -> backend normalizer -> provisional candle update -> browser WebSocket
                                      -> finalized candle upsert -> PostgreSQL
```

- One backend stream is shared by subscribers for the same exchange/symbol/timeframe.
- Provisional updates modify only the current visual candle.
- Only finalized candles are persisted as final data.
- Reconnect resumes from the latest stored close time and runs a bounded REST gap fill.
- Heartbeat, reconnecting, delayed, and disconnected states are visible on the market page.
- The chart uses `series.update(...)` for current/new candles rather than replacing all data.
- Browser WebSocket authentication uses a short-lived, one-time stream ticket issued through the
  existing authenticated HTTP API; the owner token is never placed in a URL.

Proposed endpoints:

```text
POST /workspaces/{workspace_id}/market-stream-tickets
WS   /ws/v1/market-stream?ticket=<one-time-ticket>
```

The ticket is scoped to workspace, exchange, symbol, and timeframe and expires within one minute.

## Confirmed current state

The UI draws a normalized close-price polyline in a raw SVG. It has no time labels, price labels,
candle bodies, crosshair, or tooltip. The API initially returns at most 500 stored rows, which is a
display limit and is separate from ingestion.

Database inspection on 2026-08-25 confirmed that Binance BTCJPY data starts around 2026-08-05 for
every timeframe. A 365-day 1D job requested 2025-08-25 through 2026-08-25 but stored only 20 rows.
Other timeframes cover the same roughly 20-day source range. The loop traversed the requested period
and marked the job `succeeded`, but did not validate the actual first/last candle or continuity.

This matches the official Binance Spot Test Network behavior: Testnet is reset to a blank state
approximately monthly. A one-year request therefore does not imply one year of available history.

## Library decision

Use TradingView Lightweight Charts 5.2.x directly from React instead of maintaining the custom SVG.
It provides a native candlestick series, time/price scales, crosshair, zoom, pan, and TypeScript APIs.
Direct integration avoids depending on a separately maintained React wrapper.

```text
lightweight-charts ^5.2.0
```

Pin the resolved version in `package-lock.json` and run license and production-build checks.

## UX specification

- Render OHLC candles, not a close-only line.
- Horizontal axis: date/time labels in Asia/Tokyo.
- Vertical axis: price values using instrument precision and quote currency.
- Crosshair tooltip: timestamp, open, high, low, close, volume, source, and quality status.
- Header: exchange, symbol, timeframe, displayed first/last timestamp, displayed row count.
- Responsive resize using `ResizeObserver`, with cleanup on component unmount.
- Zoom and horizontal scrolling load older rows on demand.

Show these ranges separately:

1. Requested range, such as 2025-08-25 through 2026-08-25.
2. Stored range, such as 2026-08-05 through 2026-08-24.
3. Displayed range, the subset currently loaded into the chart.

Coverage labels are `complete`, `partial_source_limit`, `partial_gaps`, `empty`, and `checking`.
For Testnet, show that periodic resets limit historical availability.

## Backend changes

Replace the integer-only ingestion result with an `IngestionReport` containing:

- requested start/end
- actual first/last open time
- rows received and rows inserted or updated
- empty source windows
- expected/stored/missing candle counts
- bounded gap samples
- coverage status and safe reason code

Persist the report in `backfill_job.validation_result`. Keep job status `succeeded` when the request
completed technically, but use `coverage_status` to distinguish complete and partial data.

Validation rules:

- Validate the stored range after upsert, not only provider response counts.
- Binance expected counts use continuous time; OANDA must account for its trading-week closure.
- Record actionable discontinuities in `market_data_gap`.
- Reject concurrent duplicate jobs for the same workspace, instrument, timeframe, and range.

Preserve the existing endpoint and add cursor pagination and coverage:

```text
GET /workspaces/{workspace_id}/instruments/{instrument_id}/candles
    ?timeframe=1d&limit=500&before=<timestamp>

GET /workspaces/{workspace_id}/instruments/{instrument_id}/candle-coverage
    ?timeframe=1d&requested_from=<timestamp>&requested_to=<timestamp>
```

Neither response contains credentials or secret references.

## Historical source policy

Execution and account operations remain on OANDA Practice and Binance Spot Testnet.

1. First report Testnet coverage accurately.
2. If one-year Binance history is required, use a separate read-only public historical adapter only
   after verifying exact symbol and interval availability.
3. Store source provenance on every candle and define precedence before mixing sources.
4. Never enable production trading credentials or production order endpoints.

The public-data adapter is a separate approval gate. Chart and coverage work does not depend on it.

## Delivery sequence

### Approved implementation-order amendment (2026-08-25)

At the user's direction, coverage correctness and chart replacement are implemented before page
separation. Phase 0 remains the next structural step after this work. The separate one-year public
data adapter is not added during Testnet connection validation: the UI reports the requested and
actually stored ranges, and Phase C remains an explicit approval gate.

### Phase 0: page separation

- Add React Router 7 in declarative mode and the shared application shell.
- Move current account/connection features to `ConnectionManagementPage` without behavior changes.
- Add empty OANDA and Binance market route modules with source/environment notices.
- Preserve workspace selection across navigation without persisting the owner token.
- Add route, direct-navigation, refresh, and responsive-navigation tests.

### Phase A: coverage correctness

- Add `IngestionReport` and post-ingestion validation.
- Store coverage results on each backfill job.
- Add coverage and cursor-pagination API schemas.
- Display actual stored coverage instead of requested days.
- Test complete, source-limited, empty, duplicate, and internal-gap cases.

### Phase B: chart replacement

- Add Lightweight Charts 5.2.x.
- Replace the SVG with a lifecycle-safe React candlestick component.
- Add axes, crosshair tooltip, JST/precision formatting, zoom, and pan.
- Load older candles on demand instead of rendering a full one-minute year.
- Add component tests and browser interaction checks.

The connection page keeps only a compact validation preview. The complete axes, crosshair, history
navigation, and real-time controls live on the exchange market pages.

### Phase B2: real-time market updates

- Add backend exchange-stream adapters and normalized provisional candle events.
- Add short-lived WebSocket stream tickets and workspace authorization.
- Add reconnect, heartbeat, deduplication, bounded gap-fill, and final-candle persistence.
- Connect chart `update()` behavior and visible connection-state indicators.
- Test disconnect/reconnect, duplicate events, late events, rollover, and workspace isolation.

### Phase C: optional one-year Binance history

- Verify exact BTCJPY public historical availability.
- Approve source precedence and provenance.
- Implement the read-only historical adapter and gap backfill.
- Re-run coverage validation and expose the actual final range.

## Acceptance criteria

- Both axes show exact time and price values.
- Hovering a candle shows exact JST timestamp and OHLCV.
- A 20-row 1D result displays roughly 20 days stored even when 365 days were requested.
- Incomplete successful jobs are labeled `partial_source_limit`, not complete.
- Pagination navigates past 500 rows without loading an entire one-minute year.
- Duplicate requests do not run concurrently.
- Workspace isolation, paper-only execution, audit, and secret non-disclosure remain intact.
- Backend tests, PostgreSQL integration, frontend checks, and browser visual verification pass.
- OANDA and Binance have distinct, directly addressable market URLs.
- Reloading a market URL restores the page without exposing or persisting the owner token.
- A stream disconnect is visible and automatically reconciles missing final candles after reconnect.

## Risks

- Testnet history can disappear during periodic resets; locally collected data is the durable record.
- One year of one-minute continuous data is about 525,600 rows per instrument and must be paged.
- OANDA needs a market calendar to avoid false weekend gaps.
- Mixing Testnet and public data without visible provenance is prohibited.

## Approval checklist before implementation

The following decisions are proposed for approval:

1. Keep the current screen focused on connection and Testnet/Practice validation.
2. Create separate workspace-scoped OANDA and Binance market URLs.
3. Share one chart/page implementation while keeping exchange adapters separate.
4. Use React Router 7 declarative mode and Lightweight Charts 5.2.x.
5. Route real-time data through the backend; never expose exchange credentials to the browser.
6. Keep order execution paper-only even if a later market page reads public production data.
7. Implement the approved amendment first: coverage correctness and chart replacement, then page
   separation and real-time stream. One-year public history remains optional and separately approved.

Implementation starts only after these seven points are accepted or amended.
