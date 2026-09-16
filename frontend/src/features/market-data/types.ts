export type Timeframe = '1m' | '5m' | '15m' | '30m' | '1h' | '4h' | '1d'

export type BackfillJob = {
  id: string
  instrument_id: string
  timeframe: string
  status: 'queued' | 'running' | 'succeeded' | 'failed'
  rows_written: number
  error_code: string | null
  created_at: string
  next_run_at: string
  consecutive_failures: number
}

export type MarketDataSubscription = {
  id: string
  instrument_id: string
  timeframe: string
  enabled: boolean
  poll_interval_seconds: number
  last_polled_at: string | null
  last_success_at: string | null
  last_error_code: string | null
  next_run_at: string
  consecutive_failures: number
  blocked_reason: string | null
}

export type CandleCoverage = {
  timeframe: Timeframe
  requested_from: string | null
  requested_to: string | null
  actual_from: string | null
  actual_to: string | null
  stored_count: number
  expected_count: number | null
  missing_count: number | null
  coverage_status: 'complete' | 'partial_source_limit' | 'partial_gaps' | 'empty' | 'checking'
  source_limitation: string | null
}
