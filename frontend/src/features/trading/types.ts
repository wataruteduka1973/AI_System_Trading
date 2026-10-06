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

type LatestSignal = {
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

/** Mirrors `app.schemas.trading_halts.TradingHaltRead`. */
export type TradingHalt = {
  id: string
  scope_type: string
  scope_id: string | null
  level: 'warning' | 'entry_halted' | 'all_trading_halted' | 'emergency_stopped'
  reason_code: string
  status: string
  halted_at: string
  released_at: string | null
}

/** Mirrors `app.schemas.trading.EmergencyStopRead`. */
export type EmergencyStopResult = {
  halt_id: string
  scope_type: string
  scope_id: string | null
  level: string
  already_active: boolean
  stopped_bot_ids: string[]
  bot_stop_failures: { bot_id: string; code: string }[]
  closing_order_ids: string[]
  close_failures: { position_id: string; code: string }[]
}
