import { useState } from 'react'
import type { ConnectionSummary } from '../connections/types'
import type { WorkspaceInstrument } from '../instruments/types'
import { findStaleBots } from './botStaleness'
import type { BotRunSummary, TradingAccount, TradingBot, TradingHalt } from './types'

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

/** The server always creates this one (paper_provisioning.APPROVED_*); shown, not chosen. */
const approvedStrategyLabel = '4h・Donchian 55/20'

/** Rendered inside the shared workspace-panel section, mirroring ConnectionsPanel's
 * placement convention -- see that component's own doc comment. */
export default function TradingPanel({
  visible,
  tradingAccounts,
  bots,
  latestRuns,
  halts,
  onCommand,
  onEmergencyStop,
  onReleaseHalt,
}: {
  visible: boolean
  tradingAccounts: TradingAccount[]
  bots: TradingBot[]
  latestRuns: Record<string, BotRunSummary>
  halts: TradingHalt[]
  onCommand: (bot: TradingBot, command: 'start' | 'pause' | 'resume' | 'stop') => void
  /** `bot === null` stops the whole workspace. */
  onEmergencyStop: (bot: TradingBot | null, closePositions: boolean) => void
  onReleaseHalt: (halt: TradingHalt) => void
}) {
  const [closePositions, setClosePositions] = useState(false)
  if (!visible) return null
  const staleBots = findStaleBots(bots, latestRuns, new Date())
  const emergencyHalts = halts.filter((halt) => halt.level === 'emergency_stopped')
  const haltScopeLabel = (halt: TradingHalt) =>
    halt.scope_type === 'workspace'
      ? 'ワークスペース全体'
      : (bots.find((bot) => bot.id === halt.scope_id)?.name ?? `${halt.scope_type}`)
  const confirmStop = (bot: TradingBot | null) => {
    const target = bot ? `「${bot.name}」` : 'このワークスペースの全Bot'
    const policy = closePositions ? '建玉は成行で決済します。' : '建玉はそのまま残します。'
    if (window.confirm(`${target}を緊急停止します。${policy}解除はOwnerのみ可能です。よろしいですか?`)) {
      onEmergencyStop(bot, closePositions)
    }
  }
  return (
    <>
      {emergencyHalts.length > 0 && (
        <div role="alert" className="worker-alert">
          <strong>緊急停止中です。</strong>
          新規の注文は出ません。解除はOwnerのみ可能で、解除してもBotは停止したままです。
          <ul>
            {emergencyHalts.map((halt) => (
              <li key={halt.id}>
                {haltScopeLabel(halt)}({new Date(halt.halted_at).toLocaleString()}から){' '}
                <button type="button" onClick={() => onReleaseHalt(halt)}>
                  緊急停止を解除
                </button>
              </li>
            ))}
          </ul>
        </div>
      )}
      {bots.length > 0 && (
        <div className="emergency-stop-controls">
          <button type="button" className="danger-button" onClick={() => confirmStop(null)}>
            全Botを緊急停止
          </button>
          <label>
            <input
              type="checkbox"
              checked={closePositions}
              onChange={(event) => setClosePositions(event.target.checked)}
            />
            建玉を成行で決済する
          </label>
        </div>
      )}
      {staleBots.length > 0 && (
        <div role="alert" className="worker-alert">
          <strong>トレーディングWorkerが止まっている可能性があります。</strong>
          稼働中のボットが、確定した最新の足をまだ評価していません。起動ウィンドウを確認し、
          止まっていれば [A] キーで再起動してください。
          <ul>
            {staleBots.map(({ bot, expectedBarClose, lastEvaluatedAt }) => (
              <li key={bot.id}>
                {bot.name}: {expectedBarClose.toLocaleString()} に確定した足が未評価(最終評価:{' '}
                {lastEvaluatedAt ? lastEvaluatedAt.toLocaleString() : 'なし'})
              </li>
            ))}
          </ul>
        </div>
      )}
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
                <button type="button" className="danger-button" onClick={() => confirmStop(bot)}>
                  緊急停止
                </button>
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
          <p className="panel-description">
            作成のみ行います。開始は一覧の「開始」ボタンから別途行ってください。
            戦略は検証済みのDonchian 55/20＋損切り(4h足)だけで、暗号資産の銘柄にのみ作成できます。
          </p>
        </div>
        <div className="registration-grid">
          <label>
            Bot名
            <input value={botName} onChange={(event) => onBotNameChange(event.target.value)} placeholder="btc-4h-bot" />
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
            戦略・時間足
            <input value={approvedStrategyLabel} readOnly />
          </label>
          <button type="button" onClick={onCreateBot} disabled={!botName.trim() || !botAccountId || !botInstrumentId}>
            Bot作成
          </button>
        </div>
      </section>
    </>
  )
}
