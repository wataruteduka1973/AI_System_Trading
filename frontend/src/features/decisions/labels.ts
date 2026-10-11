import type { DecisionHalt, DecisionItem, Outcome } from './types'

export const outcomeOrder: Outcome[] = ['ordered', 'closed', 'denied', 'halted', 'paused', 'hold']

export const outcomeLabel: Record<string, string> = {
  hold: '様子見(hold)',
  ordered: '発注',
  closed: '建玉を決済',
  denied: 'リスク判定で拒否',
  halted: '停止中で見送り',
  paused: '一時停止中で見送り',
}

/** Colours of the timeline cells and the bar (CSS classes in App.css). */
export const outcomeClass: Record<string, string> = {
  hold: 'outcome-hold',
  ordered: 'outcome-ordered',
  closed: 'outcome-closed',
  denied: 'outcome-denied',
  halted: 'outcome-halted',
  paused: 'outcome-paused',
}

/** Outcomes that explain why a bot did NOT trade on a buy/sell signal. */
export const notTradedOutcomes: Outcome[] = ['denied', 'halted', 'paused']

export const actionLabel: Record<string, string> = { buy: '買い', sell: '売り', hold: 'hold' }

export const checkLabel: Record<string, string> = {
  binance_no_short: '空売りは不可(Binance は買いのみで、売りは決済のときだけ)',
  per_trade_loss: '1回の取引の想定損失が上限を超える',
  all_open_risk: '建玉全体の想定損失が上限を超える',
  daily_loss: '日次の損失が上限を超えている',
  weekly_loss: '週次の損失が上限を超えている',
  peak_drawdown: '資産が最高値から下がりすぎている(最大ドローダウン)',
  consecutive_losses: '連敗が上限を超えている',
  order_interval: '前の注文から間隔が短すぎる',
  data_delay: 'データが遅れている',
  quantity_calculation: '注文量が小さすぎる(資金に対して最小の数量に届かない)',
  binance_btc_holding_cap: 'BTCの保有上限を超える',
  equity: '資産を算出できない',
  reward_risk: '損益比が基準に届かない',
}

export const riskReasonLabel: Record<string, string> = {
  hard_limit_breach: '絶対上限(hard limit)を超えた',
  initial_threshold_breach: '通常の上限を超えた',
}

export const periodLabel = {
  '7d': '直近7日',
  '30d': '直近30日',
  '90d': '直近90日',
  all: 'すべて',
} as const

export const periodHours: Record<string, number | null> = { '7d': 168, '30d': 720, '90d': 2160, all: null }

export const haltLevelLabel: Record<string, string> = {
  warning: '警告',
  entry_halted: '新規建玉の停止',
  all_trading_halted: '全取引の停止',
  emergency_stopped: '緊急停止',
}

export const haltScopeLabel: Record<string, string> = {
  bot: 'このBot',
  account: 'この口座',
  workspace: 'ワークスペース全体',
}

/** Whether `halt` was in force at `at` and stopped entries (a `warning` stops nothing). */
export function haltBlockedAt(halt: DecisionHalt, at: string): boolean {
  if (halt.level === 'warning') return false
  const time = new Date(at).getTime()
  const from = new Date(halt.halted_at).getTime()
  const to = halt.released_at ? new Date(halt.released_at).getTime() : Number.POSITIVE_INFINITY
  return time >= from && time < to
}

/** The halts that were stopping entries when the bot evaluated this bar. */
export function haltsAt(halts: DecisionHalt[], item: DecisionItem): DecisionHalt[] {
  return halts.filter((halt) => haltBlockedAt(halt, item.evaluated_at))
}
