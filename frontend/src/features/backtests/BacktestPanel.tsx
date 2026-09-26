import type { WorkspaceInstrument } from '../instruments/types'
import type { Timeframe } from '../market-data/types'
import type { BacktestRun, BacktestTrade } from './types'

const statusLabel: Record<string, string> = {
  queued: 'キュー待ち',
  running: '実行中',
  succeeded: '成功',
  failed: '失敗',
}

const statusBadgeClass: Record<string, string> = {
  queued: 'connection-badge',
  running: 'connection-badge',
  succeeded: 'connection-badge bot-state-running',
  failed: 'connection-badge bot-state-failed',
}

const timeframeOptions: Timeframe[] = ['1m', '5m', '15m', '30m', '1h', '4h', '1d']

const walkForwardRoleLabel: Record<string, string> = { train: '訓練期間', test: '検証期間' }

function formatPercent(value: string): string {
  const parsed = Number(value)
  return Number.isFinite(parsed) ? `${(parsed * 100).toFixed(1)}%` : value
}

/** Rendered inside the shared workspace-panel section, mirroring
 * ConnectionsPanel/TradingPanel's placement convention. */
export default function BacktestPanel({
  visible,
  backtests,
  selectedRunTrades,
  onViewTrades,
}: {
  visible: boolean
  backtests: BacktestRun[]
  selectedRunTrades: { runId: string; trades: BacktestTrade[] } | null
  onViewTrades: (run: BacktestRun) => void
}) {
  if (!visible) return null
  return (
    <>
      {backtests.length > 0 && (
        <ul className="connection-list">
          {backtests.map((run) => {
            const metrics = run.summary_metrics.metrics
            const role = run.parameters.walk_forward_role as string | undefined
            return (
              <li key={run.id}>
                <strong>{String(run.parameters.timeframe ?? run.id.slice(0, 8))}</strong>
                <span className={statusBadgeClass[run.status] ?? 'connection-badge'}>
                  {statusLabel[run.status] ?? run.status}
                </span>
                {role && <span className="connection-badge">{walkForwardRoleLabel[role] ?? role}</span>}
                {metrics ? (
                  <>
                    <span>取引数: {metrics.trade_count}</span>
                    <span>勝率: {formatPercent(metrics.win_rate)}</span>
                    <span>純損益: {metrics.net_pnl}</span>
                    <span>最大DD: {formatPercent(metrics.max_drawdown_pct)}</span>
                  </>
                ) : (
                  <span>指標なし</span>
                )}
                <button type="button" onClick={() => onViewTrades(run)}>
                  取引を見る
                </button>
              </li>
            )
          })}
        </ul>
      )}
      {selectedRunTrades && (
        <div className="account-selection">
          <h3>取引一覧({selectedRunTrades.trades.length}件)</h3>
          {selectedRunTrades.trades.length === 0 ? (
            <p className="panel-description">この実行では取引が発生しませんでした。</p>
          ) : (
            <ul className="account-list">
              {selectedRunTrades.trades.map((trade) => (
                <li key={trade.id}>
                  <strong>#{trade.sequence_no}</strong>
                  <span>{trade.side}</span>
                  <span>{trade.entry_price} → {trade.exit_price ?? '(保有中)'}</span>
                  <span>数量: {trade.quantity}</span>
                  <span>損益: {trade.realized_pnl ?? '-'}</span>
                </li>
              ))}
            </ul>
          )}
        </div>
      )}
    </>
  )
}

export function BacktestForm({
  visible,
  workspaceInstruments,
  backtestMessage,
  instrumentId,
  onInstrumentIdChange,
  timeframe,
  onTimeframeChange,
  fromTime,
  onFromTimeChange,
  toTime,
  onToTimeChange,
  initialEquity,
  onInitialEquityChange,
  spread,
  onSpreadChange,
  walkForward,
  onWalkForwardChange,
  trainRatio,
  onTrainRatioChange,
  onCreateBacktest,
}: {
  visible: boolean
  workspaceInstruments: WorkspaceInstrument[]
  backtestMessage: string
  instrumentId: string
  onInstrumentIdChange: (value: string) => void
  timeframe: Timeframe
  onTimeframeChange: (value: Timeframe) => void
  fromTime: string
  onFromTimeChange: (value: string) => void
  toTime: string
  onToTimeChange: (value: string) => void
  initialEquity: string
  onInitialEquityChange: (value: string) => void
  spread: string
  onSpreadChange: (value: string) => void
  walkForward: boolean
  onWalkForwardChange: (value: boolean) => void
  trainRatio: string
  onTrainRatioChange: (value: string) => void
  onCreateBacktest: () => void
}) {
  if (!visible) return null
  return (
    <section className="workspace-panel connection-registration">
      <div>
        <p className="eyebrow">BACKTESTS</p>
        <h2>バックテストを実行</h2>
        <p className="panel-description">
          過去の確定済みローソク足に対して、Botと同じシグナル・リスク判定ロジックを再生します。
          このリクエストの応答が返るまで同期的に実行されます。
        </p>
      </div>
      <div className="registration-grid">
        <label>
          銘柄
          <select value={instrumentId} onChange={(event) => onInstrumentIdChange(event.target.value)}>
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
          <select value={timeframe} onChange={(event) => onTimeframeChange(event.target.value as Timeframe)}>
            {timeframeOptions.map((tf) => (
              <option key={tf} value={tf}>
                {tf}
              </option>
            ))}
          </select>
        </label>
        <label>
          開始日時
          <input type="datetime-local" value={fromTime} onChange={(event) => onFromTimeChange(event.target.value)} />
        </label>
        <label>
          終了日時
          <input type="datetime-local" value={toTime} onChange={(event) => onToTimeChange(event.target.value)} />
        </label>
        <label>
          初期資金
          <input value={initialEquity} onChange={(event) => onInitialEquityChange(event.target.value)} />
        </label>
        <label>
          spread
          <input value={spread} onChange={(event) => onSpreadChange(event.target.value)} />
        </label>
        <label>
          <input
            type="checkbox"
            checked={walkForward}
            onChange={(event) => onWalkForwardChange(event.target.checked)}
          />
          walk-forward評価(訓練/検証に分割)
        </label>
        {walkForward && (
          <label>
            訓練期間の割合
            <input value={trainRatio} onChange={(event) => onTrainRatioChange(event.target.value)} />
          </label>
        )}
        <button
          type="button"
          onClick={onCreateBacktest}
          disabled={!instrumentId || !fromTime || !toTime || !initialEquity}
        >
          実行
        </button>
      </div>
      <p className="workspace-message">{backtestMessage}</p>
    </section>
  )
}
