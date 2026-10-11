import { backfillStatusLabel, marketErrorLabel } from '../market-data/labels'
import type { GapJob, MarketDataGap } from './types'

export const gapStatusLabel: Record<string, string> = {
  open: '要対応',
  ignored: '無視(元データに無い)',
  resolved: '解消済み',
}

export const gapStatusClass: Record<string, string> = {
  open: 'connection-badge bot-state-failed',
  ignored: 'connection-badge',
  resolved: 'connection-badge bot-state-running',
}

export const exchangeLabel: Record<string, string> = {
  oanda: 'OANDA',
  binance: 'Binance',
  binance_public: '公開履歴',
}

/** "40時間" / "1日と3時間" / "25分": how long the missing stretch is. */
export function formatSpan(from: string, to: string): string {
  const minutes = Math.round((new Date(to).getTime() - new Date(from).getTime()) / 60000)
  if (minutes < 60) return `${minutes}分`
  const hours = Math.round(minutes / 60)
  if (hours < 48) return `${hours}時間`
  return `${Math.floor(hours / 24)}日${hours % 24 ? `と${hours % 24}時間` : ''}`
}

/** What the last fill attempt did, in words, for a gap that has one. `stillOpen` makes the
 * re-validation honest: a finished job that left the gap open found nothing more to fetch. */
export function describeJob(job: GapJob, gapStatus: string): { headline: string; detail: string[] } {
  const headline = `補完: ${backfillStatusLabel(job.status as 'queued')} `.trim()
  const detail: string[] = []
  if (job.status === 'queued' && job.consecutive_failures > 0) {
    detail.push(
      `次回の再試行 ${new Date(job.next_run_at).toLocaleString('ja-JP')}(連続失敗${job.consecutive_failures}回)`,
    )
  }
  if (job.error_code) detail.push(marketErrorLabel(job.error_code))
  if (job.status === 'succeeded') {
    const result = job.validation_result
    const received = result.source_rows_received ?? 0
    const inserted = result.rows_inserted ?? 0
    detail.push(`取引所から${received.toLocaleString()}本を受信し、${inserted.toLocaleString()}本を追加しました。`)
    if (gapStatus === 'resolved') {
      detail.push('再検証の結果、この欠損は埋まりました。')
    } else if (gapStatus === 'open') {
      detail.push(
        '再検証の結果、まだ欠損が残っています。取引所にもこの範囲の足が無い可能性があります' +
          '(Binance Testnet は定期的にリセットされます)。',
      )
    }
  }
  return { headline, detail }
}

export function hasActiveJob(gap: MarketDataGap): boolean {
  return gap.latest_job !== null && ['queued', 'running'].includes(gap.latest_job.status)
}
