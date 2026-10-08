/** Display formatting for backtest figures (the API sends Decimals as exact strings). */

export function formatPercent(value: string): string {
  const parsed = Number(value)
  return Number.isFinite(parsed) ? `${(parsed * 100).toFixed(1)}%` : value
}

/** The API sends Decimals as exact strings ("0E+26", 18 decimals); show at most 2. */
export function formatNumber(value: string): string {
  const parsed = Number(value)
  return Number.isFinite(parsed) ? parsed.toLocaleString('ja-JP', { maximumFractionDigits: 2 }) : value
}

export function formatSigned(value: string): string {
  const parsed = Number(value)
  if (!Number.isFinite(parsed)) return value
  return `${parsed > 0 ? '+' : ''}${formatNumber(value)}`
}

export function formatDate(value: string): string {
  return new Date(value).toLocaleDateString('ja-JP', { timeZone: 'Asia/Tokyo' })
}

/** Net P&L as a share of the starting equity, or null when either is unknown or zero. */
export function returnRate(netPnl: string, initialEquity: string | null | undefined): string | null {
  const pnl = Number(netPnl)
  const start = Number(initialEquity)
  if (!initialEquity || !Number.isFinite(pnl) || !Number.isFinite(start) || start === 0) return null
  return formatPercent(String(pnl / start))
}
