/** Mirrors `app.schemas.decision_history` (GET .../bots/{id}/decisions). */

export type Outcome = 'hold' | 'ordered' | 'closed' | 'denied' | 'halted' | 'paused'

export type FailedCheck = {
  name: string
  /** The check's own figures, without the `passed` flag. */
  detail: Record<string, unknown>
}

export type DecisionItem = {
  signal_id: string
  evaluated_at: string
  bar_open_time: string | null
  bar_close_time: string | null
  /** The strategy's signal. */
  action: 'buy' | 'sell' | 'hold' | string
  outcome: Outcome | string
  rationale: Record<string, unknown>
  risk_outcome: 'allow' | 'allow_with_adjustment' | 'deny' | string | null
  risk_reason_code: string | null
  failed_checks: FailedCheck[]
  approved_quantity: string | null
  order_id: string | null
  order_side: string | null
  fill_price: string | null
  fill_quantity: string | null
}

export type DecisionHalt = {
  id: string
  scope_type: 'bot' | 'account' | 'workspace' | string
  level: 'warning' | 'entry_halted' | 'all_trading_halted' | 'emergency_stopped' | string
  reason_code: string
  status: string
  halted_at: string
  released_at: string | null
}

export type DecisionHistory = {
  items: DecisionItem[]
  summary: { evaluations: number; counts: Record<string, number> }
  /** The halts on the bot, its account or the workspace that overlap the time this page covers. */
  halts: DecisionHalt[]
  next_before: string | null
  next_before_id: string | null
}

export type DecisionPeriod = '7d' | '30d' | '90d' | 'all'

export type DecisionFilters = {
  botId: string
  period: DecisionPeriod
  /** Empty = every outcome. */
  outcomes: Outcome[]
}

export const defaultDecisionFilters: DecisionFilters = { botId: '', period: '30d', outcomes: [] }
