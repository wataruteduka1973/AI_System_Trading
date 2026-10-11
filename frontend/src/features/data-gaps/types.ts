/** Mirrors `app.schemas.market_data` (GET .../market-data-gaps, .../summary). */

export type GapStatus = 'open' | 'ignored' | 'resolved' | string

/** What a finished backfill reported about the stretch it was asked to fill. */
export type GapValidationResult = {
  coverage_status?: string
  missing_count?: number | null
  internal_gap_count?: number
  rows_written?: number
  rows_inserted?: number
  source_rows_received?: number
  empty_source_window_count?: number
  source_limitation?: string | null
}

export type GapJob = {
  id: string
  status: 'queued' | 'running' | 'succeeded' | 'failed' | string
  attempts: number
  rows_written: number
  validation_result: GapValidationResult
  error_code: string | null
  started_at: string | null
  finished_at: string | null
  created_at: string
  next_run_at: string
  consecutive_failures: number
}

export type MarketDataGap = {
  id: string
  instrument_id: string
  symbol: string
  exchange_code: string
  timeframe: string
  from_time: string
  to_time: string
  expected_count: number | null
  missing_count: number | null
  reason_code: string
  status: GapStatus
  detected_at: string
  resolved_at: string | null
  can_backfill: boolean
  latest_job: GapJob | null
  affected_bots: string[]
}

export type GapPage = {
  total: number
  items: MarketDataGap[]
}

export type GapSummary = {
  instrument_id: string
  symbol: string
  exchange_code: string
  timeframe: string
  open_count: number
  open_missing: number
  ignored_count: number
  resolved_count: number
}

export type GapFilters = {
  /** Show the gaps recorded as ignored (the source itself lacks those candles). */
  showIgnored: boolean
  showResolved: boolean
  instrumentId: string
  timeframe: string
}

export const emptyGapFilters: GapFilters = {
  showIgnored: false,
  showResolved: false,
  instrumentId: '',
  timeframe: '',
}
