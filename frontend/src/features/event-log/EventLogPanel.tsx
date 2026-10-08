import type { ChangeEvent } from 'react'
import {
  categoryLabel,
  eventReasonLabel,
  periodLabel,
  severityClass,
  severityLabel,
} from './eventLabels'
import { eventSubject } from './eventSubject'
import type { EventFacets, EventFilters, Period, SystemEvent } from './types'
import { emptyFilters } from './types'

const defaultSeverities = ['critical', 'error', 'warning', 'info', 'debug']

function EventRow({
  event,
  botNames,
  onTrace,
}: {
  event: SystemEvent
  botNames: Record<string, string>
  onTrace: (correlationId: string) => void
}) {
  const subject = eventSubject(event, botNames)
  return (
    <li className="event-item">
      <div className="halt-heading">
        <span className="event-badges">
          <span className={severityClass[event.severity] ?? 'connection-badge'}>
            {severityLabel[event.severity] ?? event.severity}
          </span>
          <span className="connection-badge">{categoryLabel[event.category] ?? event.category}</span>
        </span>
        <time dateTime={event.occurred_at}>{new Date(event.occurred_at).toLocaleString()}</time>
      </div>
      <p className="event-message">{event.message}</p>
      <p className="halt-meta">
        {event.reason_code && <>原因: {eventReasonLabel(event.reason_code)}</>}
        {event.reason_code && subject && ' / '}
        {subject}
      </p>
      <details className="event-details">
        <summary>詳細</summary>
        <dl className="event-detail-list">
          <div>
            <dt>イベント種別</dt>
            <dd>{event.event_type}</dd>
          </div>
          <div>
            <dt>原因コード</dt>
            <dd>{event.reason_code ?? '-'}</dd>
          </div>
          <div>
            <dt>発生元</dt>
            <dd>{event.source_type}</dd>
          </div>
          <div>
            <dt>相関ID</dt>
            <dd>
              <code>{event.correlation_id}</code>
            </dd>
          </div>
        </dl>
        <button type="button" onClick={() => onTrace(event.correlation_id)}>
          同じ一連のイベントだけ表示
        </button>
        {Object.keys(event.payload).length > 0 && (
          <pre className="event-payload">{JSON.stringify(event.payload, null, 2)}</pre>
        )}
      </details>
    </li>
  )
}

/** 検索・絞り込みができるイベントログ (FR-UI-09). Newest first; older events on request. */
export default function EventLogPanel({
  filters,
  onFiltersChange,
  events,
  facets,
  bots,
  hasMore,
  loading,
  error,
  searchedAt,
  onLoadMore,
  onReload,
}: {
  filters: EventFilters
  onFiltersChange: (filters: EventFilters) => void
  events: SystemEvent[]
  facets: EventFacets | null
  bots: { id: string; name: string }[]
  hasMore: boolean
  loading: boolean
  error: string
  searchedAt: Date | null
  onLoadMore: () => void
  onReload: () => void
}) {
  const botNames = Object.fromEntries(bots.map((bot) => [bot.id, bot.name]))
  const set = (patch: Partial<EventFilters>) => onFiltersChange({ ...filters, ...patch })
  const severities = facets && facets.severities.length > 0 ? facets.severities : defaultSeverities
  const toggleSeverity = (severity: string) =>
    set({
      severities: filters.severities.includes(severity)
        ? filters.severities.filter((entry) => entry !== severity)
        : [...filters.severities, severity],
    })
  const select =
    (key: 'category' | 'reasonCode' | 'eventType' | 'botId') =>
    (event: ChangeEvent<HTMLSelectElement>) =>
      set({ [key]: event.target.value })
  const narrowed =
    JSON.stringify({ ...filters, period: '' }) !== JSON.stringify({ ...emptyFilters, period: '' })
  return (
    <section className="workspace-panel" aria-labelledby="event-log-title">
      <p className="eyebrow">EVENT LOG</p>
      <h2 id="event-log-title">イベントログ</h2>
      <p className="panel-description">
        取引停止・Workerの停止・Botの失敗・通知の失敗など、システムが記録した出来事を新しい順に出します。
      </p>
      <div className="event-filters">
        <label>
          期間
          <select value={filters.period} onChange={(event) => set({ period: event.target.value as Period })}>
            {(Object.keys(periodLabel) as Period[]).map((period) => (
              <option key={period} value={period}>
                {periodLabel[period]}
              </option>
            ))}
          </select>
        </label>
        <label>
          カテゴリ
          <select value={filters.category} onChange={select('category')}>
            <option value="">すべて</option>
            {(facets?.categories ?? []).map((category) => (
              <option key={category} value={category}>
                {categoryLabel[category] ?? category}
              </option>
            ))}
          </select>
        </label>
        <label>
          原因
          <select value={filters.reasonCode} onChange={select('reasonCode')}>
            <option value="">すべて</option>
            {(facets?.reason_codes ?? []).map((code) => (
              <option key={code} value={code}>
                {eventReasonLabel(code)}
              </option>
            ))}
          </select>
        </label>
        <label>
          種別
          <select value={filters.eventType} onChange={select('eventType')}>
            <option value="">すべて</option>
            {(facets?.event_types ?? []).map((type) => (
              <option key={type} value={type}>
                {type}
              </option>
            ))}
          </select>
        </label>
        <label>
          Bot
          <select value={filters.botId} onChange={select('botId')}>
            <option value="">すべて</option>
            {bots.map((bot) => (
              <option key={bot.id} value={bot.id}>
                {bot.name}
              </option>
            ))}
          </select>
        </label>
        <label>
          メッセージ内の語
          <input
            type="search"
            value={filters.text}
            maxLength={100}
            onChange={(event) => set({ text: event.target.value })}
            placeholder="例: 台帳"
          />
        </label>
      </div>
      <fieldset className="event-severities">
        <legend>重要度</legend>
        {severities.map((severity) => (
          <label key={severity} className="batch-instrument">
            <input
              type="checkbox"
              checked={filters.severities.includes(severity)}
              onChange={() => toggleSeverity(severity)}
            />
            {severityLabel[severity] ?? severity}
          </label>
        ))}
        <span className="panel-description">(選ばなければすべて)</span>
      </fieldset>
      {filters.correlationId && (
        <p className="event-trace" role="status">
          相関ID <code>{filters.correlationId}</code> の一連のイベントだけを表示しています。
          <button type="button" className="secondary-button" onClick={() => set({ correlationId: '' })}>
            解除
          </button>
        </p>
      )}
      <div className="notification-toolbar">
        <span>
          {loading && events.length === 0 ? '読み込んでいます…' : `${events.length}件${hasMore ? '以上' : ''}`}
          {searchedAt && ` (${searchedAt.toLocaleTimeString()}時点)`}
        </span>
        <span className="bot-actions">
          {narrowed && (
            <button
              type="button"
              className="secondary-button"
              onClick={() => onFiltersChange({ ...emptyFilters, period: filters.period })}
            >
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
      {!error && !loading && events.length === 0 ? (
        <p className="panel-description">条件に合うイベントはありません。</p>
      ) : (
        <ol className="event-list">
          {events.map((event) => (
            <EventRow
              key={event.id}
              event={event}
              botNames={botNames}
              onTrace={(correlationId) => set({ correlationId })}
            />
          ))}
        </ol>
      )}
      {hasMore && (
        <button type="button" onClick={onLoadMore} disabled={loading}>
          {loading ? '読み込んでいます…' : 'さらに古いイベントを読み込む'}
        </button>
      )}
    </section>
  )
}
