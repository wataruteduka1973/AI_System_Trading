export type WorkspaceInstrument = {
  id: string
  exchange_code: string
  market_code: string
  symbol: string
  quote_asset: string
  price_scale: number
  tick_size: string
  step_size: string
  min_quantity: string | null
  min_notional: string | null
  status: string
  rules_synced_at: string | null
}
