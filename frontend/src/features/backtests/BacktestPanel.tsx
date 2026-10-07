import type { WorkspaceInstrument } from '../instruments/types'
import type { Timeframe } from '../market-data/types'
import EquityCurveChart from './EquityCurveChart'
import type { BacktestMetrics, BacktestRun, BacktestRunDetail } from './types'

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

/** The API sends Decimals as exact strings ("0E+26", 18 decimals); show at most 2. */
function formatNumber(value: string): string {
  const parsed = Number(value)
  return Number.isFinite(parsed) ? parsed.toLocaleString('ja-JP', { maximumFractionDigits: 2 }) : value
}

function formatSigned(value: string): string {
  const parsed = Number(value)
  if (!Number.isFinite(parsed)) return value
  return `${parsed > 0 ? '+' : ''}${formatNumber(value)}`
}

function formatDate(value: string): string {
  return new Date(value).toLocaleDateString('ja-JP', { timeZone: 'Asia/Tokyo' })
}

/** Net P&L as a share of the starting equity, or null when either is unknown or zero. */
function returnRate(netPnl: string, initialEquity: string | null | undefined): string | null {
  const pnl = Number(netPnl)
  const start = Number(initialEquity)
  if (!initialEquity || !Number.isFinite(pnl) || !Number.isFinite(start) || start === 0) return null
  return formatPercent(String(pnl / start))
}

function MetricsGrid({
  metrics,
  initialEquity,
}: {
  metrics: BacktestMetrics
  initialEquity: string | null | undefined
}) {
  const rate = returnRate(metrics.net_pnl, initialEquity)
  const rows: [string, string][] = [
    ['純損益', formatSigned(metrics.net_pnl)],
    ['リターン', rate ?? '-'],
    ['最大DD', `${formatPercent(metrics.max_drawdown_pct)}(${formatNumber(metrics.max_drawdown)})`],
    ['プロフィットファクター', metrics.profit_factor === null ? '算出不可(負け無し)' : formatNumber(metrics.profit_factor)],
    ['勝率', `${formatPercent(metrics.win_rate)}(${metrics.win_count}勝${metrics.loss_count}敗)`],
    ['総利益 / 総損失', `${formatNumber(metrics.gross_profit)} / ${formatNumber(metrics.gross_loss)}`],
    ['手数料合計', formatNumber(metrics.total_fees)],
    ['取引数', String(metrics.trade_count)],
  ]
  return (
    <dl className="metric-grid">
      {rows.map(([label, value]) => (
        <div key={label}>
          <dt>{label}</dt>
          <dd>{value}</dd>
        </div>
      ))}
    </dl>
  )
}

/** Rendered inside the shared workspace-panel section, mirroring
 * ConnectionsPanel/TradingPanel's placement convention. */
export default function BacktestPanel({
  visible,
  backtests,
  selectedRunDetail,
  onViewDetail,
}: {
  visible: boolean
  backtests: BacktestRun[]
  selectedRunDetail: BacktestRunDetail | null
  onViewDetail: (run: BacktestRun) => void
}) {
  if (!visible) return null
  const selectedRun = backtests.find((run) => run.id === selectedRunDetail?.runId)
  const selectedMetrics = selectedRun?.summary_metrics.metrics
  const baseline = selectedRun?.summary_metrics.baseline_comparison
  const curve = selectedRunDetail?.equityCurve
  const initialEquity =
    curve?.initial_equity ?? (selectedRun?.parameters.initial_equity as string | undefined)
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
                <button type="button" onClick={() => onViewDetail(run)}>
                  詳細を見る
                </button>
              </li>
            )
          })}
        </ul>
      )}
      {selectedRunDetail && (
        <div className="account-selection">
          <h3>実行の詳細</h3>
          {selectedRun && (
            <p className="panel-description">
              時間足 {String(selectedRun.parameters.timeframe ?? '-')} / 開始時の資産{' '}
              {initialEquity ? formatNumber(initialEquity) : '-'}
              {' / '}売買差 {String(selectedRun.parameters.spread ?? '-')}
              {curve && curve.points.length > 0 && (
                <>
                  {' / '}期間 {formatDate(curve.points[0].time)} 〜{' '}
                  {formatDate(curve.points[curve.points.length - 1].time)}
                </>
              )}
            </p>
          )}
          {selectedMetrics && <MetricsGrid metrics={selectedMetrics} initialEquity={initialEquity} />}
          {baseline && (
            <p className="panel-description">
              基準(baseline)との差: 純損益 {formatSigned(baseline.net_pnl_diff)} / 勝率{' '}
              {formatSigned(String(Number(baseline.win_rate_diff) * 100))}pt / 最大DD{' '}
              {formatSigned(String(Number(baseline.max_drawdown_pct_diff) * 100))}pt
            </p>
          )}
          {curve && <EquityCurveChart curve={curve} />}
          <h3>取引一覧({selectedRunDetail.trades.length}件)</h3>
          {selectedRunDetail.trades.length === 0 ? (
            <p className="panel-description">この実行では取引が発生しませんでした。</p>
          ) : (
            <ul className="account-list">
              {selectedRunDetail.trades.map((trade) => (
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
  researchInstruments,
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
  researchInstruments: WorkspaceInstrument[]
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
          過去の確定済みローソク足に対して、Botと同じ承認済みの戦略(Donchian 55/20＋損切り)と
          リスク判定ロジックを再生します。このリクエストの応答が返るまで同期的に実行されます。
        </p>
      </div>
      <div className="registration-grid">
        <label>
          銘柄
          <select value={instrumentId} onChange={(event) => onInstrumentIdChange(event.target.value)}>
            <option value="">選択してください</option>
            {workspaceInstruments.length > 0 && (
              <optgroup label="取引用(自分の接続)">
                {workspaceInstruments.map((instrument) => (
                  <option key={instrument.id} value={instrument.id}>
                    {instrument.symbol}
                  </option>
                ))}
              </optgroup>
            )}
            {researchInstruments.length > 0 && (
              <optgroup label="検証用(公開履歴データ・接続不要)">
                {researchInstruments.map((instrument) => (
                  <option key={instrument.id} value={instrument.id}>
                    {instrument.symbol} (検証用)
                  </option>
                ))}
              </optgroup>
            )}
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
