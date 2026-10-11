export const reasonLabel: Record<string, string> = {
  signal: 'シグナル',
  stop_loss: '損切り',
  reversal_close: 'ドテンの決済',
  emergency_close: '緊急停止の決済',
  manual: '手動',
}

export const reasonClass: Record<string, string> = {
  signal: 'connection-badge',
  stop_loss: 'connection-badge bot-state-failed',
  reversal_close: 'connection-badge bot-state-paused',
  emergency_close: 'connection-badge bot-state-failed',
  manual: 'connection-badge',
}

export const sideLabel: Record<string, string> = { buy: '買い', sell: '売り' }
export const positionSideLabel: Record<string, string> = { long: '買い(ロング)', short: '売り(ショート)' }

export const periodLabel = {
  '24h': '直近24時間',
  '7d': '直近7日',
  '30d': '直近30日',
  all: 'すべて',
} as const

export const periodHours: Record<string, number | null> = { '24h': 24, '7d': 168, '30d': 720, all: null }

/** "5日と3時間" / "7時間" / "20分": how long a position was held. */
export function formatHeld(from: string | null, to: string | null): string {
  if (!from || !to) return '-'
  const minutes = Math.max(0, Math.round((new Date(to).getTime() - new Date(from).getTime()) / 60000))
  if (minutes < 60) return `${minutes}分`
  const hours = Math.round(minutes / 60)
  if (hours < 48) return `${hours}時間`
  const days = Math.floor(hours / 24)
  return `${days}日${hours % 24 ? `と${hours % 24}時間` : ''}`
}
