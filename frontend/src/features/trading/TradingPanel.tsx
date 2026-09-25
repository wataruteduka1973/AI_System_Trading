import type { ConnectionSummary } from '../connections/types'
import type { WorkspaceInstrument } from '../instruments/types'
import type { Timeframe } from '../market-data/types'
import type { BotRunSummary, TradingAccount, TradingBot } from './types'

const stateLabel: Record<TradingBot['desired_state'], string> = {
  stopped: '停止中',
  running: '稼働中',
  paused: '一時停止',
  failed: '失敗',
}

const stateBadgeClass: Record<TradingBot['desired_state'], string> = {
  stopped: 'connection-badge',
  running: 'connection-badge bot-state-running',
  paused: 'connection-badge bot-state-paused',
  failed: 'connection-badge bot-state-failed',
}

const timeframeOptions: Timeframe[] = ['1m', '5m', '15m', '30m', '1h', '4h', '1d']

/** Rendered inside the shared workspace-panel section, mirroring ConnectionsPanel's
 * placement convention -- see that component's own doc comment. */
export default function TradingPanel({
  visible,
  tradingAccounts,
  bots,
  latestRuns,
  onCommand,
}: {
  visible: boolean
  tradingAccounts: TradingAccount[]
  bots: TradingBot[]
  latestRuns: Record<string, BotRunSummary>
  onCommand: (bot: TradingBot, command: 'start' | 'pause' | 'resume' | 'stop') => void
}) {
  if (!visible) return null
  return (
    <>
      {tradingAccounts.length > 0 && (
        <ul className="connection-list">
          {tradingAccounts.map((account) => (
            <li key={account.id}>
              <strong>{account.base_currency}口座</strong>
              <span>{account.mode}</span>
              <span className="connection-badge">{account.status}</span>
            </li>
          ))}
        </ul>
      )}
      {bots.length > 0 && (
        <ul className="connection-list">
          {bots.map((bot) => {
            const latestSignal = latestRuns[bot.id]?.latest_signal
            return (
              <li key={bot.id}>
                <strong>{bot.name}</strong>
                <span>{bot.timeframe}</span>
                <span className={stateBadgeClass[bot.desired_state]}>{stateLabel[bot.desired_state]}</span>
                <span>
                  {latestSignal
                    ? `直近シグナル: ${latestSignal.action} (${new Date(latestSignal.created_at).toLocaleTimeString()})`
                    : '評価履歴なし'}
                </span>
                {bot.desired_state === 'stopped' && (
                  <button type="button" onClick={() => onCommand(bot, 'start')}>
                    開始
                  </button>
                )}
                {bot.desired_state === 'running' && (
                  <>
                    <button type="button" onClick={() => onCommand(bot, 'pause')}>
                      一時停止
                    </button>
                    <button type="button" className="danger-button" onClick={() => onCommand(bot, 'stop')}>
                      停止
                    </button>
                  </>
                )}
                {bot.desired_state === 'paused' && (
                  <>
                    <button type="button" onClick={() => onCommand(bot, 'resume')}>
                      再開
                    </button>
                    <button type="button" className="danger-button" onClick={() => onCommand(bot, 'stop')}>
                      停止
                    </button>
                  </>
                )}
              </li>
            )
          })}
        </ul>
      )}
    </>
  )
}

export function TradingForms({
  visible,
  connections,
  workspaceInstruments,
  tradingAccounts,
  tradingMessage,
  accountConnectionId,
  onAccountConnectionIdChange,
  accountBaseCurrency,
  onAccountBaseCurrencyChange,
  onCreateTradingAccount,
  depositAccountId,
  onDepositAccountIdChange,
  depositAmount,
  onDepositAmountChange,
  depositAsset,
  onDepositAssetChange,
  onCreateDeposit,
  botName,
  onBotNameChange,
  botAccountId,
  onBotAccountIdChange,
  botInstrumentId,
  onBotInstrumentIdChange,
  botTimeframe,
  onBotTimeframeChange,
  onCreateBot,
}: {
  visible: boolean
  connections: ConnectionSummary[]
  workspaceInstruments: WorkspaceInstrument[]
  tradingAccounts: TradingAccount[]
  tradingMessage: string
  accountConnectionId: string
  onAccountConnectionIdChange: (value: string) => void
  accountBaseCurrency: string
  onAccountBaseCurrencyChange: (value: string) => void
  onCreateTradingAccount: () => void
  depositAccountId: string
  onDepositAccountIdChange: (value: string) => void
  depositAmount: string
  onDepositAmountChange: (value: string) => void
  depositAsset: string
  onDepositAssetChange: (value: string) => void
  onCreateDeposit: () => void
  botName: string
  onBotNameChange: (value: string) => void
  botAccountId: string
  onBotAccountIdChange: (value: string) => void
  botInstrumentId: string
  onBotInstrumentIdChange: (value: string) => void
  botTimeframe: Timeframe
  onBotTimeframeChange: (value: Timeframe) => void
  onCreateBot: () => void
}) {
  if (!visible) return null
  return (
    <>
      <section className="workspace-panel connection-registration">
        <div>
          <p className="eyebrow">TRADING ACCOUNTS</p>
          <h2>取引口座を作成</h2>
          <p className="panel-description">Paper口座のみ作成します。実口座・実発注は行いません。</p>
        </div>
        <div className="registration-grid">
          <label>
            取引所接続
            <select value={accountConnectionId} onChange={(event) => onAccountConnectionIdChange(event.target.value)}>
              <option value="">選択してください</option>
              {connections.map((connection) => (
                <option key={connection.id} value={connection.id}>
                  {connection.label} ({connection.status})
                </option>
              ))}
            </select>
          </label>
          <label>
            通貨
            <input value={accountBaseCurrency} onChange={(event) => onAccountBaseCurrencyChange(event.target.value)} />
          </label>
          <button type="button" onClick={onCreateTradingAccount} disabled={!accountConnectionId || !accountBaseCurrency.trim()}>
            口座を作成
          </button>
        </div>
        {tradingAccounts.length > 0 && (
          <div className="registration-grid">
            <label>
              入金先口座
              <select value={depositAccountId} onChange={(event) => onDepositAccountIdChange(event.target.value)}>
                <option value="">選択してください</option>
                {tradingAccounts.map((account) => (
                  <option key={account.id} value={account.id}>
                    {account.base_currency}口座 ({account.id.slice(0, 8)})
                  </option>
                ))}
              </select>
            </label>
            <label>
              金額
              <input value={depositAmount} onChange={(event) => onDepositAmountChange(event.target.value)} placeholder="100000" />
            </label>
            <label>
              資産
              <input value={depositAsset} onChange={(event) => onDepositAssetChange(event.target.value)} />
            </label>
            <button type="button" onClick={onCreateDeposit} disabled={!depositAccountId || !depositAmount}>
              入金
            </button>
          </div>
        )}
        <p className="workspace-message">{tradingMessage}</p>
      </section>

      <section className="workspace-panel connection-registration">
        <div>
          <p className="eyebrow">BOTS</p>
          <h2>Botを作成</h2>
          <p className="panel-description">作成のみ行います。開始は一覧の「開始」ボタンから別途行ってください。</p>
        </div>
        <div className="registration-grid">
          <label>
            Bot名
            <input value={botName} onChange={(event) => onBotNameChange(event.target.value)} placeholder="btc-1m-bot" />
          </label>
          <label>
            取引口座
            <select value={botAccountId} onChange={(event) => onBotAccountIdChange(event.target.value)}>
              <option value="">選択してください</option>
              {tradingAccounts.map((account) => (
                <option key={account.id} value={account.id}>
                  {account.base_currency}口座 ({account.id.slice(0, 8)})
                </option>
              ))}
            </select>
          </label>
          <label>
            銘柄
            <select value={botInstrumentId} onChange={(event) => onBotInstrumentIdChange(event.target.value)}>
              <option value="">選択してください</option>
              {workspaceInstruments.map((instrument) => (
                <option key={instrument.id} value={instrument.id}>
                  {instrument.symbol}
                </option>
              ))}
            </select>
          </label>
          <label>
            時間足
            <select value={botTimeframe} onChange={(event) => onBotTimeframeChange(event.target.value as Timeframe)}>
              {timeframeOptions.map((timeframe) => (
                <option key={timeframe} value={timeframe}>
                  {timeframe}
                </option>
              ))}
            </select>
          </label>
          <button type="button" onClick={onCreateBot} disabled={!botName.trim() || !botAccountId || !botInstrumentId}>
            Bot作成
          </button>
        </div>
      </section>
    </>
  )
}
