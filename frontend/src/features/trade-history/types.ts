/** Mirrors `app.schemas.trading` (GET .../fills, GET .../position-history). Amounts are decimal
 * strings in the instrument's quote asset. */

export type FillReason = 'signal' | 'stop_loss' | 'reversal_close' | 'emergency_close' | 'manual' | string

export type FillRow = {
  fill_id: string
  order_id: string
  executed_at: string
  symbol: string
  quote_asset: string
  side: 'buy' | 'sell' | string
  order_type: string
  order_status: string
  client_order_id: string
  quantity: string
  price: string
  notional: string
  fee_amount: string
  /** What closing (part of) a position made on this fill, before fees; 0 for an opening one. */
  realized_pnl: string
  reason: FillReason
  bot_id: string | null
  bot_name: string | null
}

export type FillTotals = {
  quote_asset: string
  fills: number
  fees: string
  realized_pnl: string
}

export type FillPage = {
  items: FillRow[]
  /** Over everything the filters match, not only this page. */
  totals: FillTotals[]
  next_before: string | null
  next_before_id: string | null
}

export type PositionRow = {
  id: string
  symbol: string
  quote_asset: string
  bot_id: string | null
  bot_name: string | null
  side: 'long' | 'short' | string
  status: 'open' | 'closed' | string
  /** 0 once closed; the fills hold the sizes. */
  quantity: string
  average_entry_price: string | null
  realized_pnl: string
  stop_price: string | null
  opened_at: string | null
  closed_at: string | null
}

export type PositionPage = {
  total: number
  items: PositionRow[]
}

export type HistoryTab = 'fills' | 'positions'
export type HistoryPeriod = '24h' | '7d' | '30d' | 'all'

export type HistoryFilters = {
  botId: string
  side: '' | 'buy' | 'sell'
  period: HistoryPeriod
  /** Positions only: show the closed ones too (open ones are always shown). */
  showClosed: boolean
}

export const defaultHistoryFilters: HistoryFilters = {
  botId: '',
  side: '',
  period: '30d',
  showClosed: true,
}
