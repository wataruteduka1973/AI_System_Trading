export type ConnectionSummary = {
  id: string
  exchange_id: string
  label: string
  environment: string
  status: string
  credentials_status: 'saved' | 'missing'
  credentials_updated_at: string | null
  verification_outcome: 'not_verified' | 'success' | 'authentication_failed' | 'communication_failed'
}

export type WorkspaceAccount = {
  id: string
  exchange_code: 'oanda' | 'binance'
  connection_label: string
  connection_status: string
  account_ref_masked: string
  alias: string | null
  currency: string
  status: string
  selected: boolean
}

export type OandaVerification = {
  status: string
  accounts: Array<{
    account_ref_masked: string
    alias: string | null
    currency: string
    usd_jpy_tradeable: boolean
  }>
}

export type BinanceVerification = {
  status: string
  accounts: Array<{
    account_ref_masked: string
    account_type: string
    permissions: string[]
    can_trade: boolean
    nonzero_asset_count: number
    btc_jpy_tradeable: boolean
  }>
}
