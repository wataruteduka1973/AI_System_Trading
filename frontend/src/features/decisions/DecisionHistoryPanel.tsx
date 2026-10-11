import { useEffect, useRef, useState } from 'react'
import { reasonLabel as haltReasonLabel } from '../trading/haltLabels'
import { explainDecision } from './explain'
import {
  actionLabel,
  checkLabel,
  haltBlockedAt,
  haltLevelLabel,
  haltScopeLabel,
  notTradedOutcomes,
  outcomeClass,
  outcomeLabel,
  outcomeOrder,
  periodLabel,
} from './labels'
import type { DecisionFilters, DecisionHalt, DecisionItem, DecisionPeriod, Outcome } from './types'
import { defaultDecisionFilters } from './types'

function when(value: string | null): string {
  return value ? new Date(value).toLocaleString('ja-JP', { timeZone: 'Asia/Tokyo' }) : '-'
}

/** How the evaluations split, as one bar and a legend with the counts. */
function SummaryBar({ summary }: { summary: { evaluations: number; counts: Record<string, number> } }) {
  if (summary.evaluations === 0) return null
  return (
    <div className="decision-summary">
      <div className="outcome-bar" role="img" aria-label="判断の内訳">
        {outcomeOrder.map((outcome) => {
          const count = summary.counts[outcome] ?? 0
          return count > 0 ? (
            <span
              key={outcome}
              className={outcomeClass[outcome]}
              style={{ flexGrow: count }}
              title={`${outcomeLabel[outcome]}: ${count}回`}
            />
          ) : null
        })}
      </div>
      <ul className="outcome-legend">
        {outcomeOrder.map((outcome) => (
          <li key={outcome}>
            <span className={`outcome-swatch ${outcomeClass[outcome]}`} />
            {outcomeLabel[outcome]} <strong>{summary.counts[outcome] ?? 0}</strong>
          </li>
        ))}
        <li className="panel-description">評価 {summary.evaluations.toLocaleString()}回</li>
      </ul>
    </div>
  )
}

/** One cell per evaluation, oldest on the left, coloured by what became of it; under it, a row that
 * marks the bars evaluated while a halt was stopping entries. */
function Timeline({
  items,
  halts,
  selected,
  onSelect,
}: {
  items: DecisionItem[]
  halts: DecisionHalt[]
  selected: string | null
  onSelect: (signalId: string) => void
}) {
  if (items.length === 0) return null
  const ordered = [...items].reverse()
  return (
    <div className="decision-timeline" role="group" aria-label="判断の時系列(左が古い)">
      <div className="timeline-row">
        {ordered.map((item) => (
          <button
            key={item.signal_id}
            type="button"
            className={`timeline-cell ${outcomeClass[item.outcome] ?? ''}${selected === item.signal_id ? ' timeline-selected' : ''}`}
            title={`${when(item.evaluated_at)} ${actionLabel[item.action] ?? item.action}: ${outcomeLabel[item.outcome] ?? item.outcome}`}
            aria-label={`${when(item.evaluated_at)} ${outcomeLabel[item.outcome] ?? item.outcome}`}
            onClick={() => onSelect(item.signal_id)}
          />
        ))}
      </div>
      <div className="timeline-row" aria-hidden="true">
        {ordered.map((item) => {
          const blocking = halts.filter((halt) => haltBlockedAt(halt, item.evaluated_at))
          return (
            <span
              key={item.signal_id}
              className={blocking.length > 0 ? 'timeline-halt timeline-halt-on' : 'timeline-halt'}
              title={blocking.length > 0 ? '新規が止まっていた' : undefined}
            />
          )
        })}
      </div>
      <div className="timeline-axis">
        <span>{when(ordered[0].evaluated_at)}</span>
        <span className="panel-description">下の帯は、新規の取引が止まっていた(halt)期間</span>
        <span>{when(ordered[ordered.length - 1].evaluated_at)}</span>
      </div>
    </div>
  )
}

function DecisionCard({
  item,
  halts,
  selected,
}: {
  item: DecisionItem
  halts: DecisionHalt[]
  selected: boolean
}) {
  const ref = useRef<HTMLLIElement>(null)
  useEffect(() => {
    if (selected) ref.current?.scrollIntoView?.({ block: 'center', behavior: 'smooth' })
  }, [selected])
  const explanation = explainDecision(item, halts)
  return (
    <li
      ref={ref}
      id={`decision-${item.signal_id}`}
      className={`decision-card ${outcomeClass[item.outcome] ?? ''}-border${selected ? ' decision-selected' : ''}`}
    >
      <div className="halt-heading">
        <span className="event-badges">
          <span className={`connection-badge ${outcomeClass[item.outcome] ?? ''}-badge`}>
            {outcomeLabel[item.outcome] ?? item.outcome}
          </span>
          <span className="connection-badge">シグナル: {actionLabel[item.action] ?? item.action}</span>
        </span>
        <time dateTime={item.evaluated_at}>
          {when(item.bar_close_time ?? item.evaluated_at)} の足
        </time>
      </div>
      <p className="event-message">{explanation.headline}</p>
      {explanation.reasons.length > 0 && (
        <ul className="plain-list">
          {explanation.reasons.map((reason) => (
            <li key={reason}>{reason}</li>
          ))}
        </ul>
      )}
      <details className="event-details" open={selected || undefined}>
        <summary>詳細</summary>
        <dl className="event-detail-list">
          <div>
            <dt>評価した時刻</dt>
            <dd>{when(item.evaluated_at)}</dd>
          </div>
          <div>
            <dt>足(開始)</dt>
            <dd>{when(item.bar_open_time)}</dd>
          </div>
          <div>
            <dt>戦略</dt>
            <dd>{String(item.rationale.kind ?? '-')}</dd>
          </div>
          <div>
            <dt>リスク判定</dt>
            <dd>{item.risk_outcome ?? '(判定なし)'}</dd>
          </div>
          {item.order_id && (
            <div>
              <dt>注文ID</dt>
              <dd>
                <code>{item.order_id}</code>
              </dd>
            </div>
          )}
        </dl>
        {item.failed_checks.length > 0 && (
          <ul className="plain-list">
            {item.failed_checks.map((check) => (
              <li key={check.name}>
                {checkLabel[check.name] ?? check.name}
                {Object.keys(check.detail).length > 0 && (
                  <code className="check-detail"> {JSON.stringify(check.detail)}</code>
                )}
              </li>
            ))}
          </ul>
        )}
        <pre className="event-payload">{JSON.stringify(item.rationale, null, 2)}</pre>
      </details>
    </li>
  )
}

function HaltList({ halts }: { halts: DecisionHalt[] }) {
  if (halts.length === 0) {
    return <p className="panel-description">この期間に、このBotの新規を止めた取引停止(halt)はありません。</p>
  }
  return (
    <ul className="halt-list">
      {halts.map((halt) => (
        <li key={halt.id} className="halt-item">
          <div className="halt-heading">
            <strong>{haltReasonLabel(halt.reason_code)}</strong>
            <span className="connection-badge bot-state-paused">{haltLevelLabel[halt.level] ?? halt.level}</span>
          </div>
          <p className="halt-meta">
            {haltScopeLabel[halt.scope_type] ?? halt.scope_type} / {when(halt.halted_at)} から{' '}
            {halt.released_at ? `${when(halt.released_at)} まで` : '解除されていません(継続中)'}
          </p>
        </li>
      ))}
    </ul>
  )
}

/** 判断の履歴: why a bot did or did not trade, bar by bar. */
export default function DecisionHistoryPanel({
  bots,
  botId,
  filters,
  onFiltersChange,
  items,
  summary,
  halts,
  hasMore,
  loading,
  error,
  loadedAt,
  onLoadMore,
  onReload,
}: {
  bots: { id: string; name: string }[]
  botId: string
  filters: DecisionFilters
  onFiltersChange: (filters: DecisionFilters) => void
  items: DecisionItem[]
  summary: { evaluations: number; counts: Record<string, number> }
  halts: DecisionHalt[]
  hasMore: boolean
  loading: boolean
  error: string
  loadedAt: Date | null
  onLoadMore: () => void
  onReload: () => void
}) {
  const [selected, setSelected] = useState<string | null>(null)
  const set = (patch: Partial<DecisionFilters>) => onFiltersChange({ ...filters, ...patch })
  const toggle = (outcome: Outcome) =>
    set({
      outcomes: filters.outcomes.includes(outcome)
        ? filters.outcomes.filter((entry) => entry !== outcome)
        : [...filters.outcomes, outcome],
    })
  const onlyNotTraded =
    filters.outcomes.length === notTradedOutcomes.length &&
    notTradedOutcomes.every((outcome) => filters.outcomes.includes(outcome))
  const narrowed = filters.outcomes.length > 0 || filters.period !== defaultDecisionFilters.period
  if (bots.length === 0) {
    return (
      <section className="workspace-panel">
        <h2>判断の履歴</h2>
        <p className="panel-description">Botがありません。Bot管理でBotを作成すると、ここに判断の履歴が出ます。</p>
      </section>
    )
  }
  return (
    <section className="workspace-panel" aria-labelledby="decision-title">
      <p className="eyebrow">DECISIONS</p>
      <h2 id="decision-title">判断の履歴</h2>
      <p className="panel-description">
        Botが足を評価するたびに、シグナルと、リスク判定・取引停止の結果を記録しています。取引が無いときに、その理由を追えます。
      </p>
      <div className="event-filters">
        <label>
          Bot
          <select value={botId} onChange={(event) => set({ botId: event.target.value })}>
            {bots.map((bot) => (
              <option key={bot.id} value={bot.id}>
                {bot.name}
              </option>
            ))}
          </select>
        </label>
        <label>
          期間
          <select value={filters.period} onChange={(event) => set({ period: event.target.value as DecisionPeriod })}>
            {(Object.keys(periodLabel) as DecisionPeriod[]).map((period) => (
              <option key={period} value={period}>
                {periodLabel[period]}
              </option>
            ))}
          </select>
        </label>
      </div>
      <SummaryBar summary={summary} />
      <Timeline items={items} halts={halts} selected={selected} onSelect={setSelected} />
      <fieldset className="event-severities">
        <legend>表示する判断</legend>
        {outcomeOrder.map((outcome) => (
          <label key={outcome} className="batch-instrument">
            <input type="checkbox" checked={filters.outcomes.includes(outcome)} onChange={() => toggle(outcome)} />
            {outcomeLabel[outcome]}
          </label>
        ))}
        <button
          type="button"
          className="secondary-button"
          aria-pressed={onlyNotTraded}
          onClick={() => set({ outcomes: onlyNotTraded ? [] : [...notTradedOutcomes] })}
        >
          取引しなかった理由だけ見る
        </button>
      </fieldset>
      <div className="notification-toolbar">
        <span>
          {loading && items.length === 0 ? '読み込んでいます…' : `${items.length}件を表示${hasMore ? '(さらにあります)' : ''}`}
          {loadedAt && ` (${loadedAt.toLocaleTimeString()}時点)`}
        </span>
        <span className="bot-actions">
          {narrowed && (
            <button
              type="button"
              className="secondary-button"
              onClick={() => onFiltersChange({ ...defaultDecisionFilters, botId })}
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
      {!error && !loading && items.length === 0 ? (
        <p className="panel-description">
          条件に合う判断はありません。Botは、足が確定するたびに(4時間足なら4時間ごとに)評価します。
        </p>
      ) : (
        <ol className="decision-list">
          {items.map((item) => (
            <DecisionCard key={item.signal_id} item={item} halts={halts} selected={selected === item.signal_id} />
          ))}
        </ol>
      )}
      {hasMore && (
        <button type="button" onClick={onLoadMore} disabled={loading}>
          さらに古い判断を読み込む
        </button>
      )}
      <h3>新規を止めた取引停止(halt)</h3>
      <HaltList halts={halts} />
    </section>
  )
}
