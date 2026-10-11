import { describeJob, exchangeLabel, formatSpan, gapStatusClass, gapStatusLabel } from './gapText'
import type { GapFilters, GapSummary, MarketDataGap } from './types'
import { emptyGapFilters } from './types'

const timeframes = ['1m', '5m', '15m', '30m', '1h', '4h', '1d']

function when(value: string): string {
  return new Date(value).toLocaleString('ja-JP', { timeZone: 'Asia/Tokyo' })
}

function SummaryTable({ summary }: { summary: GapSummary[] }) {
  if (summary.length === 0) return <p className="panel-description">記録された欠損はありません。</p>
  return (
    <div className="batch-table-wrap">
      <table className="batch-table">
        <thead>
          <tr>
            <th>銘柄</th>
            <th>時間足</th>
            <th>要対応</th>
            <th>欠けている足</th>
            <th>無視</th>
            <th>解消済み</th>
          </tr>
        </thead>
        <tbody>
          {summary.map((row) => (
            <tr key={`${row.instrument_id}-${row.timeframe}`}>
              <th scope="row">
                {row.symbol}
                <span className="connection-badge">{exchangeLabel[row.exchange_code] ?? row.exchange_code}</span>
              </th>
              <td>{row.timeframe}</td>
              <td className={row.open_count > 0 ? 'pnl-negative' : ''}>{row.open_count}</td>
              <td className={row.open_missing > 0 ? 'pnl-negative' : ''}>
                {row.open_missing.toLocaleString()}
              </td>
              <td>{row.ignored_count}</td>
              <td>{row.resolved_count}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

function GapCard({
  gap,
  canFill,
  filling,
  onFill,
}: {
  gap: MarketDataGap
  canFill: boolean
  filling: boolean
  onFill: (gap: MarketDataGap) => void
}) {
  const job = gap.latest_job ? describeJob(gap.latest_job, gap.status) : null
  const isPublic = gap.exchange_code === 'binance_public'
  return (
    <li className="gap-card">
      <div className="bot-card-head">
        <strong>{gap.symbol}</strong>
        <span className="connection-badge">{exchangeLabel[gap.exchange_code] ?? gap.exchange_code}</span>
        <span>{gap.timeframe}</span>
        <span className={gapStatusClass[gap.status] ?? 'connection-badge'}>
          {gapStatusLabel[gap.status] ?? gap.status}
        </span>
      </div>
      <p className="gap-range">
        {when(gap.from_time)} 〜 {when(gap.to_time)}(約{formatSpan(gap.from_time, gap.to_time)})
      </p>
      <p>
        欠けている足: <strong>{(gap.missing_count ?? 0).toLocaleString()}本</strong>
        {gap.expected_count !== null && gap.expected_count !== gap.missing_count && (
          <> / 期待 {gap.expected_count.toLocaleString()}本</>
        )}
        <span className="panel-description"> (検知 {when(gap.detected_at)})</span>
      </p>
      <p className="halt-meta">
        {gap.affected_bots.length > 0
          ? `影響するBot(この銘柄で稼働中): ${gap.affected_bots.join(', ')}`
          : isPublic
            ? '検証用の公開履歴です。稼働中のBotには影響しません。'
            : 'この銘柄で稼働中のBotはありません。'}
      </p>
      {job && (
        <div className="gap-job">
          <strong>{job.headline}</strong>
          {job.detail.map((line) => (
            <p key={line} className="panel-description">
              {line}
            </p>
          ))}
        </div>
      )}
      {gap.status === 'ignored' && (
        <p className="panel-description">取引所の元データにも無い足として、意図的に無視されています。</p>
      )}
      {gap.status === 'open' && isPublic && (
        <p className="panel-description">
          公開履歴の欠損は、この画面からは補完できません(公開履歴の取り込みで再取得します)。
        </p>
      )}
      {gap.can_backfill && canFill && (
        <button type="button" onClick={() => onFill(gap)} disabled={filling}>
          {filling ? '登録しています…' : gap.latest_job ? '補完を再試行' : 'この欠損を補完'}
        </button>
      )}
      {gap.can_backfill && !canFill && (
        <p className="panel-description">補完はOperator以上の権限で実行できます。</p>
      )}
    </li>
  )
}

/** 欠損データ (FR-UI-10): where candles are missing, what it touches, and what is being done. */
export default function DataGapsPanel({
  summary,
  gaps,
  total,
  hasMore,
  filters,
  onFiltersChange,
  canFill,
  filling,
  message,
  error,
  loading,
  onFill,
  onLoadMore,
  onReload,
}: {
  summary: GapSummary[]
  gaps: MarketDataGap[]
  total: number
  hasMore: boolean
  filters: GapFilters
  onFiltersChange: (filters: GapFilters) => void
  canFill: boolean
  filling: string | null
  message: string
  error: string
  loading: boolean
  onFill: (gap: MarketDataGap) => void
  onLoadMore: () => void
  onReload: () => void
}) {
  const set = (patch: Partial<GapFilters>) => onFiltersChange({ ...filters, ...patch })
  const instruments = Array.from(new Map(summary.map((row) => [row.instrument_id, row.symbol])))
  const open = summary.reduce((sum, row) => sum + row.open_count, 0)
  const missing = summary.reduce((sum, row) => sum + row.open_missing, 0)
  const ignored = summary.reduce((sum, row) => sum + row.ignored_count, 0)
  return (
    <section className="workspace-panel" aria-labelledby="data-gaps-title">
      <p className="eyebrow">DATA GAPS</p>
      <h2 id="data-gaps-title">欠損データ</h2>
      {!error && (
        <div
          className={open > 0 ? 'status-verdict status-verdict-attention' : 'status-verdict'}
          role="status"
        >
          <strong>{open > 0 ? '要対応の欠損があります' : '要対応の欠損はありません'}</strong>
          <span>
            {open > 0
              ? `${open}件、欠けている足は合計${missing.toLocaleString()}本です。`
              : `無視されている欠損が${ignored}件あります(取引所の元データに無い足)。`}
          </span>
        </div>
      )}
      <h3>銘柄・時間足ごとの集計</h3>
      <SummaryTable summary={summary} />
      <h3>欠損の一覧</h3>
      <div className="event-filters">
        <label>
          銘柄
          <select value={filters.instrumentId} onChange={(event) => set({ instrumentId: event.target.value })}>
            <option value="">すべて</option>
            {instruments.map(([id, symbol]) => (
              <option key={id} value={id}>
                {symbol}
              </option>
            ))}
          </select>
        </label>
        <label>
          時間足
          <select value={filters.timeframe} onChange={(event) => set({ timeframe: event.target.value })}>
            <option value="">すべて</option>
            {timeframes.map((timeframe) => (
              <option key={timeframe} value={timeframe}>
                {timeframe}
              </option>
            ))}
          </select>
        </label>
      </div>
      <div className="event-severities">
        <label className="batch-instrument">
          <input
            type="checkbox"
            checked={filters.showIgnored}
            onChange={(event) => set({ showIgnored: event.target.checked })}
          />
          無視した欠損も表示
        </label>
        <label className="batch-instrument">
          <input
            type="checkbox"
            checked={filters.showResolved}
            onChange={(event) => set({ showResolved: event.target.checked })}
          />
          解消済みも表示
        </label>
      </div>
      <div className="notification-toolbar">
        <span>
          {loading && gaps.length === 0 ? '読み込んでいます…' : `${gaps.length} / ${total}件を表示`}
        </span>
        <span className="bot-actions">
          {(filters.instrumentId || filters.timeframe || filters.showIgnored || filters.showResolved) && (
            <button type="button" className="secondary-button" onClick={() => onFiltersChange(emptyGapFilters)}>
              条件をクリア
            </button>
          )}
          <button type="button" onClick={onReload} disabled={loading}>
            更新
          </button>
        </span>
      </div>
      {error && (
        <p role="alert" className="login-error">
          {error}
        </p>
      )}
      {message && <p className="workspace-message">{message}</p>}
      {!error && !loading && gaps.length === 0 ? (
        <p className="panel-description">条件に合う欠損はありません。</p>
      ) : (
        <ul className="gap-list">
          {gaps.map((gap) => (
            <GapCard key={gap.id} gap={gap} canFill={canFill} filling={filling === gap.id} onFill={onFill} />
          ))}
        </ul>
      )}
      {hasMore && (
        <button type="button" onClick={onLoadMore} disabled={loading}>
          さらに読み込む
        </button>
      )}
    </section>
  )
}
