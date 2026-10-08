/** Amounts arrive as decimal strings with 18 places ("166667.000000000000000000"); the screen
 * shows them the way a person reads money. Display only -- never fed back to the API. */

const group = (digits: number) =>
  new Intl.NumberFormat('ja-JP', { minimumFractionDigits: 0, maximumFractionDigits: digits })

export function formatAmount(value: string | null | undefined, digits = 2): string {
  if (value === null || value === undefined || value === '') return '—'
  const number = Number(value)
  return Number.isFinite(number) ? group(digits).format(number) : value
}

/** "+1,234.5" / "-20" / "0": the sign is always shown, so a gain and a loss differ by more than colour. */
export function formatSigned(value: string | null | undefined, digits = 2): string {
  if (value === null || value === undefined || value === '') return '—'
  const number = Number(value)
  if (!Number.isFinite(number)) return value
  const text = group(digits).format(Math.abs(number))
  if (number > 0 && text !== '0') return `+${text}`
  if (number < 0 && text !== '0') return `-${text}`
  return text
}

export function formatPercent(value: string | null | undefined, digits = 2): string {
  const text = formatSigned(value, digits)
  return text === '—' ? text : `${text}%`
}

/** CSS class for a profit/loss figure. */
export function pnlClass(value: string | null | undefined): string {
  const number = Number(value)
  if (!Number.isFinite(number) || number === 0) return ''
  return number > 0 ? 'pnl-positive' : 'pnl-negative'
}

/** "BTCUSDT 4h" style label for an account that has no name of its own: the bots on it say what it
 * is for; the id's start tells apart two that are alike. */
export function accountLabel(
  account: { id: string; base_currency: string; bot_names?: string[] },
): string {
  const bots = account.bot_names && account.bot_names.length > 0 ? account.bot_names.join('・') : 'Botなし'
  return `${account.base_currency}口座 ${account.id.slice(0, 8)}(${bots})`
}
