import type { TradingHalt } from './types'

export const levelLabel: Record<TradingHalt['level'], string> = {
  warning: '警告',
  entry_halted: '新規建玉の停止',
  all_trading_halted: '全取引の停止',
  emergency_stopped: '緊急停止',
}

/** What each level lets through (取引停止マトリクス). */
export const levelEffect: Record<TradingHalt['level'], string> = {
  warning: '取引は止まりません。',
  entry_halted: '新規・増し玉は止まります。決済はできます。',
  all_trading_halted: '新規は止まり、決済もリスクを減らすものに限られます。',
  emergency_stopped: '新規・増し玉は止まります。',
}

type ReasonInfo = { label: string; release: string }

const reasons: Record<string, ReasonInfo> = {
  user_emergency_stop: {
    label: '利用者の緊急停止',
    release: 'Ownerだけが解除できます。解除してもBotは停止したままなので、必要なら開始してください。',
  },
  ledger_mismatch: {
    label: '注文・台帳の不整合',
    release:
      '記録を照合して直したうえで、Ownerが解除します。不整合が残っていると解除できません。',
  },
  consecutive_loss_limit: {
    label: '連敗の上限',
    release: 'Ownerだけが解除できます。解除すると、そこから連敗を数え直します。',
  },
  peak_drawdown_limit: {
    label: '最大ドローダウンの上限',
    release: 'Ownerだけが解除できます。解除すると、そこから資産の最高値を数え直します。',
  },
  daily_loss_dd_limit: {
    label: '日次・週次の損失またはドローダウンの上限',
    release: '基準に戻ると自動で緩和します。Ownerは1段ずつ手動でも緩和できます。',
  },
  data_delay: {
    label: 'データ遅延',
    release: 'データが補完されると自動で緩和します。Ownerは1段ずつ手動でも緩和できます。',
  },
}

export function reasonLabel(code: string): string {
  return reasons[code]?.label ?? code
}

export function releaseGuide(code: string): string {
  return reasons[code]?.release ?? 'Ownerが解除できます。'
}

/** Which release endpoint a halt goes through: an `emergency_stopped` halt has its own; the
 * rest are released (or stepped down) by `/release`. */
export function releasePath(halt: TradingHalt): 'emergency-release' | 'release' {
  return halt.level === 'emergency_stopped' ? 'emergency-release' : 'release'
}

export function scopeLabel(
  halt: TradingHalt,
  names: { bots: Record<string, string>; accounts: Record<string, string> },
): string {
  if (halt.scope_type === 'workspace') return 'ワークスペース全体'
  const id = halt.scope_id ?? ''
  if (halt.scope_type === 'bot') return `Bot: ${names.bots[id] ?? id.slice(0, 8)}`
  if (halt.scope_type === 'account') return `口座: ${names.accounts[id] ?? id.slice(0, 8)}`
  return halt.scope_type
}
