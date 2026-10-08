import type { TradingHalt } from './types'
import { levelEffect, levelLabel, reasonLabel, releaseGuide, scopeLabel } from './haltLabels'

const levelClass: Record<TradingHalt['level'], string> = {
  warning: 'connection-badge bot-state-paused',
  entry_halted: 'connection-badge bot-state-paused',
  all_trading_halted: 'connection-badge bot-state-failed',
  emergency_stopped: 'connection-badge bot-state-failed',
}

/** 取引停止センター (FR-UI-11): every active halt, why it is there, what it still allows and
 * how it ends. Releasing is Owner-only on the server; the button is offered to Owners only. */
export default function HaltCenter({
  halts,
  isOwner,
  botNames,
  accountNames,
  message,
  onRelease,
}: {
  halts: TradingHalt[]
  isOwner: boolean
  botNames: Record<string, string>
  accountNames: Record<string, string>
  message: string
  onRelease: (halt: TradingHalt) => void
}) {
  return (
    <section className="workspace-panel" aria-labelledby="halt-center-title">
      <p className="eyebrow">TRADING HALTS</p>
      <h2 id="halt-center-title">取引停止センター</h2>
      {halts.length === 0 ? (
        <p className="panel-description">現在、取引を止めている停止(halt)はありません。</p>
      ) : (
        <ul className="halt-list">
          {halts.map((halt) => (
            <li key={halt.id} className="halt-item">
              <div className="halt-heading">
                <strong>{reasonLabel(halt.reason_code)}</strong>
                <span className={levelClass[halt.level]}>{levelLabel[halt.level]}</span>
              </div>
              <p className="halt-meta">
                {scopeLabel(halt, { bots: botNames, accounts: accountNames })} /{' '}
                {new Date(halt.halted_at).toLocaleString()} から
              </p>
              <p>{levelEffect[halt.level]}</p>
              <p className="panel-description">{releaseGuide(halt.reason_code)}</p>
              {isOwner ? (
                <button type="button" onClick={() => onRelease(halt)}>
                  {halt.level === 'emergency_stopped' ? '緊急停止を解除' : '停止を解除'}
                </button>
              ) : (
                <p className="panel-description">解除はOwnerのみ可能です。</p>
              )}
            </li>
          ))}
        </ul>
      )}
      {message && <p className="workspace-message">{message}</p>}
    </section>
  )
}
