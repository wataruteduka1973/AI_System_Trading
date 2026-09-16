import type { WorkspaceInstrument } from './types'

export default function InstrumentsPanel({
  hasSelectedAccount,
  onSync,
  instrumentMessage,
  workspaceInstruments,
}: {
  hasSelectedAccount: boolean
  onSync: () => void
  instrumentMessage: string
  workspaceInstruments: WorkspaceInstrument[]
}) {
  return (
    <section className="workspace-panel instrument-panel">
      <div>
        <p className="eyebrow">MARKET RULES</p>
        <h2>銘柄同期</h2>
        <p className="panel-description">
          OANDA USD/JPY・Binance BTC/JPYの価格刻みと最小数量を参照専用APIから取得します。
          注文は送信しません。
        </p>
      </div>
      <button type="button" onClick={onSync} disabled={!hasSelectedAccount}>
        選択済み口座から同期
      </button>
      <p className="workspace-message">{instrumentMessage}</p>
      {workspaceInstruments.length > 0 && (
        <ul className="instrument-list">
          {workspaceInstruments.map((instrument) => (
            <li key={instrument.id}>
              <strong>
                {instrument.exchange_code.toUpperCase()} {instrument.symbol}
              </strong>
              <span>価格刻み: {instrument.tick_size}</span>
              <span>数量刻み: {instrument.step_size}</span>
              <span>最小数量: {instrument.min_quantity ?? '未提供'}</span>
              <span>最小金額: {instrument.min_notional ?? '未提供'}</span>
              <span>
                同期: {instrument.rules_synced_at
                  ? new Date(instrument.rules_synced_at).toLocaleString('ja-JP')
                  : '未同期'}
              </span>
            </li>
          ))}
        </ul>
      )}
    </section>
  )
}
