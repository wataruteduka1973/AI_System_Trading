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

/** Mirrors `app.schemas.trading.BotOverviewResponse` (GET .../bot-overview). Amounts are decimal
 * strings, in the bot's `quote_asset`. */
export type PositionOverview = {
  side: 'long' | 'short' | string
  quantity: string
  average_entry_price: string | null
  mark_price: string | null
  unrealized_pnl: string
  stop_price: string | null
  opened_at: string | null
}

export type BotOverview = {
  bot_id: string
  name: string
  symbol: string
  exchange_code: string
  timeframe: string
  desired_state: string
  actual_state: string
  account_id: string
  quote_asset: string
  deposits: string
  cash: string
  equity: string
  return_pct: string | null
  realized_pnl: string
  fees_paid: string
  closed_trades: number
  position: PositionOverview | null
}

export type AccountOverview = {
  id: string
  base_currency: string
  mode: string
  status: string
  bot_names: string[]
  balances: Record<string, string>
}

export type BotOverviewResponse = {
  bots: BotOverview[]
  accounts: AccountOverview[]
}
