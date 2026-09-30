/** Mirrors `app.schemas.trading` (app/api/routes/trading.py). */

export type TradingAccount = {
  id: string
  workspace_id: string
  connection_id: string | null
  mode: string
  base_currency: string
  status: string
  created_at: string
}

export type TradingBot = {
  id: string
  workspace_id: string
  name: string
  execution_mode: string
  strategy_mode: string
  account_id: string
  instrument_id: string
  timeframe: string
  desired_state: 'stopped' | 'running' | 'paused' | 'failed'
  actual_state: 'stopped' | 'running' | 'paused' | 'failed'
  live_trading_enabled: boolean
  version: number
  created_at: string
}

export type LatestSignal = {
  id: string
  action: string
  created_at: string
}

export type BotRunSummary = {
  id: string
  bot_id: string
  status: string
  code_version: string
  started_at: string
  stopped_at: string | null
  stop_reason: string | null
  heartbeat_at: string | null
  latest_signal: LatestSignal | null
}
