import type { ChartCandle } from '../../components/marketData'
import { formatAmount } from '../trading/format'
import type { FillRow, PositionRow } from '../trade-history/types'

/** What the chart draws over the candles for the workspace's own paper trades on this instrument
 * (FR-UI-02, docs/plans/chart-overlay.md): a marker per fill, a segment from a closed position's
 * entry to its exit, and price lines for an open position's average entry and stop. */

export type OverlayMarker = {
  id: string
  /** UTC seconds of the candle the fill falls on. */
  time: number
  position: 'aboveBar' | 'belowBar'
  shape: 'arrowUp' | 'arrowDown' | 'square'
  color: string
  text: string
}

export type OverlaySegment = {
  id: string
  from: { time: number; price: number }
  to: { time: number; price: number }
  color: string
  profit: boolean
}

export type OverlayLine = {
  id: string
  price: number
  title: string
  color: string
  style: 'dashed' | 'dotted'
}

export type OverlayFillDetail = {
  fillId: string
  side: string
  quantity: string
  price: string
  reason: string
  realizedPnl: string
  botName: string | null
}

export type ChartOverlay = {
  markers: OverlayMarker[]
  segments: OverlaySegment[]
  lines: OverlayLine[]
  /** The fills on each candle (by its open time, UTC seconds), for the tooltip. */
  detailsByTime: Map<number, OverlayFillDetail[]>
  /** Fills older than the loaded candles: not drawn until the chart is scrolled back to them. */
  offscreen: number
}

export const BUY_COLOR = '#4fd1a5'
export const SELL_COLOR = '#ff7b78'
export const STOP_COLOR = '#f5b84b'
export const ENTRY_COLOR = '#63b3ff'

const reasonText: Record<string, string> = {
  stop_loss: '損切り',
  reversal_close: 'ドテン決済',
  emergency_close: '緊急決済',
  manual: '手動',
}

const seconds = (value: string) => Math.floor(new Date(value).getTime() / 1000)

/** The candle a trade belongs to: the last one that had closed by then. A paper fill is made at the
 * close of the latest final candle (`order_flow._simulate_fill`), so that candle's close is the
 * fill's price -- the bar that gave the signal, not the one that has just opened. `null` when the
 * moment is before every loaded candle closed (the candle it belongs to is not loaded). */
export function candleAt(candles: ChartCandle[], at: string): ChartCandle | null {
  const time = new Date(at).getTime()
  let found: ChartCandle | null = null
  let low = 0
  let high = candles.length - 1
  while (low <= high) {
    const middle = Math.floor((low + high) / 2)
    if (new Date(candles[middle].close_time).getTime() <= time) {
      found = candles[middle]
      low = middle + 1
    } else {
      high = middle - 1
    }
  }
  return found
}

function markerFor(fill: FillRow): Pick<OverlayMarker, 'position' | 'shape' | 'color' | 'text'> {
  const quantity = formatAmount(fill.quantity, 4)
  if (fill.reason !== 'signal') {
    return {
      position: fill.side === 'buy' ? 'belowBar' : 'aboveBar',
      shape: 'square',
      color: fill.reason === 'stop_loss' || fill.reason === 'emergency_close' ? STOP_COLOR : '#b794f4',
      text: `${reasonText[fill.reason] ?? fill.reason} ${quantity}`,
    }
  }
  return fill.side === 'buy'
    ? { position: 'belowBar', shape: 'arrowUp', color: BUY_COLOR, text: `買い ${quantity}` }
    : { position: 'aboveBar', shape: 'arrowDown', color: SELL_COLOR, text: `売り ${quantity}` }
}

export function buildChartOverlay(
  fills: FillRow[],
  positions: PositionRow[],
  candles: ChartCandle[],
): ChartOverlay {
  const markers: OverlayMarker[] = []
  const detailsByTime = new Map<number, OverlayFillDetail[]>()
  let offscreen = 0
  for (const fill of fills) {
    const candle = candleAt(candles, fill.executed_at)
    if (candle === null) {
      offscreen += 1
      continue
    }
    const time = seconds(candle.open_time)
    markers.push({ id: fill.fill_id, time, ...markerFor(fill) })
    const details = detailsByTime.get(time) ?? []
    details.push({
      fillId: fill.fill_id,
      side: fill.side,
      quantity: fill.quantity,
      price: fill.price,
      reason: fill.reason,
      realizedPnl: fill.realized_pnl,
      botName: fill.bot_name,
    })
    detailsByTime.set(time, details)
  }
  // The chart wants markers in time order.
  markers.sort((a, b) => a.time - b.time)

  const segments: OverlaySegment[] = []
  const lines: OverlayLine[] = []
  for (const position of positions) {
    if (position.average_entry_price === null) continue
    const entry = Number(position.average_entry_price)
    if (position.status === 'open') {
      lines.push({
        id: `${position.id}-entry`,
        price: entry,
        title: `建値 ${position.side === 'short' ? '売り' : '買い'} ${formatAmount(position.quantity, 4)}`,
        color: ENTRY_COLOR,
        style: 'dashed',
      })
      if (position.stop_price !== null) {
        lines.push({
          id: `${position.id}-stop`,
          price: Number(position.stop_price),
          title: '損切り',
          color: STOP_COLOR,
          style: 'dotted',
        })
      }
      continue
    }
    if (!position.opened_at || !position.closed_at) continue
    const closing = fills
      .filter((fill) => Number(fill.realized_pnl) !== 0)
      .sort(
        (a, b) =>
          Math.abs(new Date(a.executed_at).getTime() - new Date(position.closed_at as string).getTime()) -
          Math.abs(new Date(b.executed_at).getTime() - new Date(position.closed_at as string).getTime()),
      )[0]
    const opened = candleAt(candles, position.opened_at)
    const closed = closing ? candleAt(candles, closing.executed_at) : null
    if (!opened || !closed || !closing) continue
    if (seconds(opened.open_time) >= seconds(closed.open_time)) continue // opened and closed on one candle
    const profit = Number(position.realized_pnl) >= 0
    segments.push({
      id: position.id,
      from: { time: seconds(opened.open_time), price: entry },
      to: { time: seconds(closed.open_time), price: Number(closing.price) },
      color: profit ? BUY_COLOR : SELL_COLOR,
      profit,
    })
  }
  return { markers, segments, lines, detailsByTime, offscreen }
}
