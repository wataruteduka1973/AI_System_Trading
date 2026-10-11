import { useState } from 'react'
import { formatAmount, formatSigned, pnlClass } from '../trading/format'
import {
  formatHeld,
  periodLabel,
  positionSideLabel,
  reasonClass,
  reasonLabel,
  sideLabel,
} from './labels'
import type {
  FillRow,
  FillTotals,
  HistoryFilters,
  HistoryPeriod,
  HistoryTab,
  PositionRow,
} from './types'
import { defaultHistoryFilters } from './types'

function when(value: string | null): string {
  return value ? new Date(value).toLocaleString('ja-JP', { timeZone: 'Asia/Tokyo' }) : '-'
}

/** Totals per quote asset: JPY and USDT are not added together. */
function TotalsStrip({ totals }: { totals: FillTotals[] }) {
  if (totals.length === 0) return null
  return (
    <div className="totals-strip" aria-label="合計">
      {totals.map((total) => {
        const net = String(Number(total.realized_pnl) - Number(total.fees))
        return (
          <span key={total.quote_asset}>
            {total.quote_asset}: 約定 {total.fills.toLocaleString()}件 / 手数料 {formatAmount(total.fees)} / 確定損益{' '}
            <span className={pnlClass(total.realized_pnl)}>{formatSigned(total.realized_pnl)}</span>
            (手数料後 <span className={pnlClass(net)}>{formatSigned(net)}</span>)
          </span>
        )
      })}
    </div>
  )
}

function FillsTable({ fills }: { fills: FillRow[] }) {
  const [open, setOpen] = useState<string | null>(null)
  return (
    <div className="batch-table-wrap">
      <table className="batch-table history-table">
        <thead>
          <tr>
            <th>時刻</th>
            <th>Bot</th>
            <th>銘柄</th>
            <th>売買</th>
            <th>数量</th>
            <th>価格</th>
            <th>約定代金</th>
            <th>手数料</th>
            <th>確定損益</th>
            <th>きっかけ</th>
            <th />
          </tr>
        </thead>
        <tbody>
          {fills.map((fill) => (
            <FillRows
              key={fill.fill_id}
              fill={fill}
              expanded={open === fill.fill_id}
              onToggle={() => setOpen(open === fill.fill_id ? null : fill.fill_id)}
            />
          ))}
        </tbody>
      </table>
    </div>
  )
}

function FillRows({ fill, expanded, onToggle }: { fill: FillRow; expanded: boolean; onToggle: () => void }) {
  const closing = Number(fill.realized_pnl) !== 0
  return (
    <>
      <tr>
        <th scope="row">{when(fill.executed_at)}</th>
        <td>{fill.bot_name ?? '手動'}</td>
        <td>{fill.symbol}</td>
        <td>{sideLabel[fill.side] ?? fill.side}</td>
        <td>{formatAmount(fill.quantity, 6)}</td>
        <td>{formatAmount(fill.price, 4)}</td>
        <td>{formatAmount(fill.notional)}</td>
        <td>{formatAmount(fill.fee_amount)}</td>
        <td className={pnlClass(fill.realized_pnl)}>{closing ? formatSigned(fill.realized_pnl) : '-'}</td>
        <td>
          <span className={reasonClass[fill.reason] ?? 'connection-badge'}>
            {reasonLabel[fill.reason] ?? fill.reason}
          </span>
        </td>
        <td>
          <button type="button" className="secondary-button" onClick={onToggle} aria-expanded={expanded}>
            詳細
          </button>
        </td>
      </tr>
      {expanded && (
        <tr className="history-detail-row">
          <td colSpan={11}>
            <dl className="event-detail-list">
              <div>
                <dt>注文ID</dt>
                <dd>
                  <code>{fill.order_id}</code>
                </dd>
              </div>
              <div>
                <dt>約定ID</dt>
                <dd>
                  <code>{fill.fill_id}</code>
                </dd>
              </div>
              <div>
                <dt>クライアント注文ID</dt>
                <dd>
                  <code>{fill.client_order_id}</code>
                </dd>
              </div>
              <div>
                <dt>注文の種類・状態</dt>
                <dd>
                  {fill.order_type} / {fill.order_status}
                </dd>
              </div>
              <div>
                <dt>通貨</dt>
                <dd>{fill.quote_asset}(金額の単位)</dd>
              </div>
            </dl>
          </td>
        </tr>
      )}
    </>
  )
}

function PositionsTable({ positions }: { positions: PositionRow[] }) {
  return (
    <div className="batch-table-wrap">
      <table className="batch-table history-table">
        <thead>
          <tr>
            <th>状態</th>
            <th>Bot</th>
            <th>銘柄</th>
            <th>向き</th>
            <th>数量</th>
            <th>平均建値</th>
            <th>損切り</th>
            <th>確定損益</th>
            <th>建てた時刻</th>
            <th>決済した時刻</th>
            <th>保有期間</th>
          </tr>
        </thead>
        <tbody>
          {positions.map((position) => {
            const open = position.status === 'open'
            return (
              <tr key={position.id}>
                <th scope="row">
                  <span className={open ? 'connection-badge bot-state-running' : 'connection-badge'}>
                    {open ? '保有中' : '決済済み'}
                  </span>
                </th>
                <td>{position.bot_name ?? '-'}</td>
                <td>{position.symbol}</td>
                <td>{positionSideLabel[position.side] ?? position.side}</td>
                <td>{open ? formatAmount(position.quantity, 6) : '-'}</td>
                <td>{formatAmount(position.average_entry_price, 4)}</td>
                <td>{formatAmount(position.stop_price, 4)}</td>
                <td className={pnlClass(position.realized_pnl)}>
                  {Number(position.realized_pnl) !== 0 || !open ? formatSigned(position.realized_pnl) : '-'}
                </td>
                <td>{when(position.opened_at)}</td>
                <td>{open ? '-' : when(position.closed_at)}</td>
                <td>{open ? '-' : formatHeld(position.opened_at, position.closed_at)}</td>
              </tr>
            )
          })}
        </tbody>
      </table>
    </div>
  )
}

/** 取引履歴 (FR-UI-12): every fill with its bot, reason and result, and the positions. */
export default function TradeHistoryPanel({
  tab,
  onTabChange,
  filters,
  onFiltersChange,
  bots,
  fills,
  totals,
  positions,
  positionTotal,
  hasMore,
  loading,
  error,
  loadedAt,
  onLoadMore,
  onReload,
}: {
  tab: HistoryTab
  onTabChange: (tab: HistoryTab) => void
  filters: HistoryFilters
  onFiltersChange: (filters: HistoryFilters) => void
  bots: { id: string; name: string }[]
  fills: FillRow[]
  totals: FillTotals[]
  positions: PositionRow[]
  positionTotal: number
  hasMore: boolean
  loading: boolean
  error: string
  loadedAt: Date | null
  onLoadMore: () => void
  onReload: () => void
}) {
  const set = (patch: Partial<HistoryFilters>) => onFiltersChange({ ...filters, ...patch })
  const empty = tab === 'fills' ? fills.length === 0 : positions.length === 0
  const changed =
    filters.botId !== defaultHistoryFilters.botId ||
    filters.side !== defaultHistoryFilters.side ||
    filters.period !== defaultHistoryFilters.period ||
    filters.showClosed !== defaultHistoryFilters.showClosed
  return (
    <section className="workspace-panel" aria-labelledby="trade-history-title">
      <p className="eyebrow">TRADE HISTORY</p>
      <h2 id="trade-history-title">取引履歴</h2>
      <div className="batch-mode-toggle" role="group" aria-label="表示する履歴">
        <button
          type="button"
          className={tab === 'fills' ? '' : 'secondary-button'}
          aria-pressed={tab === 'fills'}
          onClick={() => onTabChange('fills')}
        >
          約定
        </button>
        <button
          type="button"
          className={tab === 'positions' ? '' : 'secondary-button'}
          aria-pressed={tab === 'positions'}
          onClick={() => onTabChange('positions')}
        >
          建玉
        </button>
      </div>
      <div className="event-filters">
        <label>
          Bot
          <select value={filters.botId} onChange={(event) => set({ botId: event.target.value })}>
            <option value="">すべて</option>
            {bots.map((bot) => (
              <option key={bot.id} value={bot.id}>
                {bot.name}
              </option>
            ))}
          </select>
        </label>
        {tab === 'fills' && (
          <>
            <label>
              売買
              <select
                value={filters.side}
                onChange={(event) => set({ side: event.target.value as HistoryFilters['side'] })}
              >
                <option value="">すべて</option>
                <option value="buy">買い</option>
                <option value="sell">売り</option>
              </select>
            </label>
            <label>
              期間
              <select
                value={filters.period}
                onChange={(event) => set({ period: event.target.value as HistoryPeriod })}
              >
                {(Object.keys(periodLabel) as HistoryPeriod[]).map((period) => (
                  <option key={period} value={period}>
                    {periodLabel[period]}
                  </option>
                ))}
              </select>
            </label>
          </>
        )}
        {tab === 'positions' && (
          <label className="batch-instrument">
            <input
              type="checkbox"
              checked={filters.showClosed}
              onChange={(event) => set({ showClosed: event.target.checked })}
            />
            決済済みも表示
          </label>
        )}
      </div>
      {tab === 'fills' && <TotalsStrip totals={totals} />}
      <div className="notification-toolbar">
        <span>
          {loading && empty
            ? '読み込んでいます…'
            : tab === 'fills'
              ? `${fills.length}件を表示${hasMore ? '(さらにあります)' : ''}`
              : `${positions.length} / ${positionTotal}件を表示`}
          {loadedAt && ` (${loadedAt.toLocaleTimeString()}時点)`}
        </span>
        <span className="bot-actions">
          {changed && (
            <button type="button" className="secondary-button" onClick={() => onFiltersChange(defaultHistoryFilters)}>
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
      {!error && !loading && empty ? (
        <p className="panel-description">
          {tab === 'fills'
            ? '条件に合う約定はありません。Botは、シグナルが出て、リスク判定を通ったときだけ注文します。'
            : '条件に合う建玉はありません。'}
        </p>
      ) : tab === 'fills' ? (
        <FillsTable fills={fills} />
      ) : (
        <PositionsTable positions={positions} />
      )}
      {hasMore && (
        <button type="button" onClick={onLoadMore} disabled={loading}>
          さらに読み込む
        </button>
      )}
    </section>
  )
}
