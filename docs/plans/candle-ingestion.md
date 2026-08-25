# Candle ingestion implementation plan

## Purpose

Persist confirmed OANDA `USD_JPY` and Binance Spot Testnet `BTCJPY` candles without
duplicates, support a manual one-year backfill, and keep enabled feeds caught up every minute.

## Scope

- Timeframes: `1m`, `5m`, `15m`, `30m`, `1h`, `4h`, `1d`.
- Manual backfill: one selected instrument/timeframe, up to 365 days, executed as a FastAPI
  background task and tracked in the existing `backfill_job` table.
- Automatic update: persistent workspace/instrument/timeframe subscription polled by a
  server-side worker every 60 seconds.
- Resume behavior: polling starts after the most recent stored candle, so downtime is
  backfilled on the next successful run.
- Storage: PostgreSQL upsert on `(instrument_id, timeframe, open_time)`; only final candles.
- Read API: latest saved candles and ingestion status for chart/UI use.

## Safety and boundaries

- Only instruments exposed through a selected, active, verified workspace account are valid.
- Only market-data SDK methods are called. No order endpoint is introduced.
- Credentials and decrypted account references stay inside the backend adapter call.
- Worker errors are reduced to error codes; secret-bearing exception payloads are not stored.

## Changes

- Map `candle` and `backfill_job` and add a small `market_data_subscription` table.
- Add migration `20260825_0004` for persistent automatic-update state.
- Extend exchange adapters with candle reads and strict payload parsing.
- Add candle ingestion service, background polling worker, API and frontend controls.
- Add unit/API tests, static checks, build verification and runtime smoke checks.

## Risks

- A one-year `1m` backfill is large and can take many paginated requests. It runs outside the
  request/response path and exposes progress.
- OANDA market closures mean time is not continuous across weekends. Resume uses the last
  stored candle rather than inventing candles for closed periods.
- The in-process worker is suitable for the current single-process deployment. A distributed
  deployment must later move polling to a singleton worker with a database lease.
