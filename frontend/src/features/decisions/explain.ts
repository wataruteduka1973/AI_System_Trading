import { formatAmount } from '../trading/format'
import { reasonLabel as haltReasonLabel } from '../trading/haltLabels'
import {
  actionLabel,
  checkLabel,
  haltLevelLabel,
  haltScopeLabel,
  haltsAt,
  riskReasonLabel,
} from './labels'
import type { DecisionHalt, DecisionItem } from './types'

export type Explanation = {
  /** One sentence on what the bot did with this bar and why. */
  headline: string
  /** The reasons behind it, one per line. */
  reasons: string[]
}

function parameters(item: DecisionItem): { entry?: number; exit?: number } {
  const raw = item.rationale.parameters
  if (typeof raw !== 'object' || raw === null) return {}
  const record = raw as Record<string, unknown>
  return {
    entry: typeof record.entry_period === 'number' ? record.entry_period : undefined,
    exit: typeof record.exit_period === 'number' ? record.exit_period : undefined,
  }
}

function closeText(item: DecisionItem): string {
  const close = item.rationale.close
  return typeof close === 'string' ? formatAmount(close, 4) : '-'
}

/** What the bot did with one evaluated bar, in words: the signal, and -- when it did not trade on a
 * buy or sell -- exactly what stopped it. `halts` are the bot's halts, to name the ones in force. */
export function explainDecision(item: DecisionItem, halts: DecisionHalt[]): Explanation {
  const { entry, exit } = parameters(item)
  const signal = actionLabel[item.action] ?? item.action
  switch (item.outcome) {
    case 'hold':
      return {
        headline: `シグナルなし(終値 ${closeText(item)})`,
        reasons: [
          `買い: 終値が、直前${entry ?? '?'}本の高値を上抜けると出ます。` +
            `売り: 直前${exit ?? '?'}本の安値を下抜けると出ます。今回はどちらも満たしませんでした。`,
        ],
      }
    case 'ordered':
      return {
        headline:
          item.fill_price !== null && item.fill_quantity !== null
            ? `${signal}を発注し、約定しました(${formatAmount(item.fill_quantity, 6)} @ ${formatAmount(item.fill_price, 4)})`
            : `${signal}を発注しました`,
        reasons: ['リスク判定を通過しました。'],
      }
    case 'closed':
      return {
        headline: `${signal}のシグナルで、反対向きの建玉を決済しました`,
        reasons: ['反対のシグナルは、新規の建玉ではなく決済として扱います(リスク判定の対象外)。'],
      }
    case 'denied': {
      const reasons = item.failed_checks.map((check) => checkLabel[check.name] ?? check.name)
      if (item.risk_reason_code) {
        reasons.push(`判定: ${riskReasonLabel[item.risk_reason_code] ?? item.risk_reason_code}`)
      }
      return {
        headline: `${signal}のシグナルが出ましたが、リスク判定で拒否しました`,
        reasons: reasons.length > 0 ? reasons : ['リスク判定が許可しませんでした。'],
      }
    }
    case 'halted': {
      const blocking = haltsAt(halts, item)
      return {
        headline: `${signal}のシグナルが出ましたが、新規の取引が止まっていたため見送りました`,
        reasons:
          blocking.length > 0
            ? blocking.map(
                (halt) =>
                  `${haltScopeLabel[halt.scope_type] ?? halt.scope_type}が${haltLevelLabel[halt.level] ?? halt.level}` +
                  `(原因: ${haltReasonLabel(halt.reason_code)})`,
              )
            : ['リスク判定は許可しましたが、その時点の取引停止(halt)が新規を止めていました。'],
      }
    }
    case 'paused':
      return {
        headline: `${signal}のシグナルが出ましたが、Botが一時停止中のため新規は見送りました`,
        reasons: ['一時停止中は、建玉の決済はできますが、新規と増し玉はしません。'],
      }
    default:
      return { headline: `${signal}(${item.outcome})`, reasons: [] }
  }
}
