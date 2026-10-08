import { useState } from 'react'
import { formatNumber, formatPercent, formatSigned, returnRate } from './format'
import type { BacktestBatchItem, BacktestBatchResponse, BacktestRun } from './types'

type SortKey = 'request' | 'drawdown' | 'profit'

const sortLabel: Record<SortKey, string> = {
  request: '選んだ順',
  drawdown: '最大DDが小さい順',
  profit: '純損益が大きい順',
}

const roleLabel: Record<string, string> = { train: '訓練', test: '検証' }

type Row = {
  key: string
  item: BacktestBatchItem
  run: BacktestRun | null
}

const metricsOf = (run: BacktestRun | null) => run?.summary_metrics.metrics

/** The rows of one instrument: one per run (two for a walk-forward batch), or one error row. */
function rowsOf(item: BacktestBatchItem): Row[] {
  if (item.error_code !== null || item.runs.length === 0) {
    return [{ key: item.instrument_id, item, run: null }]
  }
  return item.runs.map((run) => ({ key: run.id, item, run }))
}

function sortRows(rows: Row[], key: SortKey): Row[] {
  if (key === 'request') return rows
  const value = (row: Row): number => {
    const metrics = metricsOf(row.run)
    if (!metrics) return Number.POSITIVE_INFINITY // failed rows go last either way
    return key === 'drawdown' ? Number(metrics.max_drawdown_pct) : -Number(metrics.net_pnl)
  }
  return [...rows].sort((a, b) => value(a) - value(b))
}

/** The result of a multi-instrument backtest, side by side. The drawdown comes before the profit:
 * what an instrument can lose matters more than what it made. Each instrument is its own run with
 * its own capital -- this is a comparison, not a portfolio. */
export default function BatchResults({
  result,
  onViewDetail,
}: {
  result: BacktestBatchResponse | null
  onViewDetail: (run: BacktestRun) => void
}) {
  const [sortKey, setSortKey] = useState<SortKey>('request')
  if (!result) return null
  const failed = result.items.filter((item) => item.error_code !== null).length
  const rows = sortRows(result.items.flatMap(rowsOf), sortKey)
  return (
    <section className="workspace-panel" aria-labelledby="batch-results-title">
      <p className="eyebrow">BATCH RESULT</p>
      <h2 id="batch-results-title">銘柄ごとの結果</h2>
      <p className="panel-description">
        {result.items.length}銘柄(成功 {result.items.length - failed}・失敗 {failed})、足の合計{' '}
        {result.total_bars.toLocaleString('ja-JP')}本。銘柄ごとに独立した実行で、資金は共有しません
        (ポートフォリオの成績ではありません)。
      </p>
      <label className="batch-sort">
        並び順
        <select value={sortKey} onChange={(event) => setSortKey(event.target.value as SortKey)}>
          {(Object.keys(sortLabel) as SortKey[]).map((key) => (
            <option key={key} value={key}>
              {sortLabel[key]}
            </option>
          ))}
        </select>
      </label>
      <div className="batch-table-wrap">
        <table className="batch-table">
          <thead>
            <tr>
              <th>銘柄</th>
              <th>最大DD</th>
              <th>純損益</th>
              <th>リターン</th>
              <th>勝率</th>
              <th>PF</th>
              <th>取引数</th>
              <th>足数</th>
              <th />
            </tr>
          </thead>
          <tbody>
            {rows.map(({ key, item, run }) => {
              const metrics = metricsOf(run)
              const role = run?.parameters.walk_forward_role as string | undefined
              if (!metrics) {
                return (
                  <tr key={key} className="batch-row-failed">
                    <th scope="row">{item.symbol}</th>
                    <td colSpan={7}>
                      {item.error ?? '指標がありません'}
                      {item.error_code && <span className="batch-error-code"> ({item.error_code})</span>}
                    </td>
                    <td />
                  </tr>
                )
              }
              const initial = run?.parameters.initial_equity as string | undefined
              const plainRate = returnRate(metrics.net_pnl, initial)
              const rate = plainRate !== null && Number(metrics.net_pnl) > 0 ? `+${plainRate}` : plainRate
              const loss = Number(metrics.net_pnl) < 0
              return (
                <tr key={key}>
                  <th scope="row">
                    {item.symbol}
                    {role && <span className="connection-badge">{roleLabel[role] ?? role}</span>}
                  </th>
                  <td>{formatPercent(metrics.max_drawdown_pct)}</td>
                  <td className={loss ? 'pnl-negative' : 'pnl-positive'}>{formatSigned(metrics.net_pnl)}</td>
                  <td className={loss ? 'pnl-negative' : 'pnl-positive'}>{rate ?? '-'}</td>
                  <td>{formatPercent(metrics.win_rate)}</td>
                  <td>{metrics.profit_factor === null ? '-' : formatNumber(metrics.profit_factor)}</td>
                  <td>{metrics.trade_count}</td>
                  <td>{item.bars.toLocaleString('ja-JP')}</td>
                  <td>
                    {run && (
                      <button type="button" onClick={() => onViewDetail(run)}>
                        詳細を見る
                      </button>
                    )}
                  </td>
                </tr>
              )
            })}
          </tbody>
        </table>
      </div>
    </section>
  )
}
