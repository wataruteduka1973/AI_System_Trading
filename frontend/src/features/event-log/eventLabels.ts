import { reasonLabel as haltReasonLabel } from '../trading/haltLabels'

export const severityLabel: Record<string, string> = {
  critical: '重大',
  error: 'エラー',
  warning: '警告',
  info: '情報',
  debug: 'デバッグ',
}

export const severityClass: Record<string, string> = {
  critical: 'connection-badge bot-state-failed',
  error: 'connection-badge bot-state-failed',
  warning: 'connection-badge bot-state-paused',
  info: 'connection-badge',
  debug: 'connection-badge',
}

/** The categories the database allows (`system_event.category`). */
export const categoryLabel: Record<string, string> = {
  market_data: '市場データ',
  connection: '接続',
  strategy: '戦略',
  model: 'モデル',
  risk: 'リスク・取引停止',
  order: '注文',
  fill: '約定',
  system: 'システム',
  security: 'セキュリティ',
  notification: '通知',
}

const extraReasons: Record<string, string> = {
  worker_stalled: 'Workerの停止',
  evaluation_failed: 'Botの評価の失敗',
  delivery_failed: '通知を届けられなかった',
}

/** A cause in words: the halt causes the Bot management screen already names, then the rest; an
 * unknown code is shown as it is. */
export function eventReasonLabel(code: string): string {
  return extraReasons[code] ?? haltReasonLabel(code)
}

export const periodLabel = {
  '1h': '直近1時間',
  '24h': '直近24時間',
  '7d': '直近7日',
  all: 'すべて',
} as const

export const periodHours: Record<string, number | null> = { '1h': 1, '24h': 24, '7d': 168, all: null }
