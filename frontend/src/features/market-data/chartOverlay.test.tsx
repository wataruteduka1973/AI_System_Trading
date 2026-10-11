// @vitest-environment jsdom
import '@testing-library/jest-dom/vitest'
import { act, cleanup, fireEvent, render, renderHook, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import CandleChart from '../../components/CandleChart'
import type { ChartCandle } from '../../components/marketData'
import type { FillRow, PositionRow } from '../trade-history/types'
import { buildChartOverlay, candleAt } from './chartOverlay'
import { useChartTrades } from './useChartTrades'

const chartMock = vi.hoisted(() => {
  const state = {
    markers: [] as unknown[],
    lineData: [] as unknown[][],
    priceLines: [] as Record<string, unknown>[],
    removedLines: 0,
    removedPriceLines: 0,
    markerPlugins: 0,
  }
  return state
})

vi.mock('lightweight-charts', () => ({
  CandlestickSeries: 'candles',
  LineSeries: 'line',
  LineStyle: { Dotted: 1, Dashed: 2 },
  ColorType: { Solid: 'solid' },
  CrosshairMode: { Normal: 0 },
  createSeriesMarkers: () => {
    chartMock.markerPlugins += 1
    return { setMarkers: (markers: unknown[]) => (chartMock.markers = markers) }
  },
  createChart: () => ({
    addSeries: (kind: string) =>
      kind === 'line'
        ? { setData: (data: unknown[]) => chartMock.lineData.push(data) }
        : {
            setData: vi.fn(),
            update: vi.fn(),
            createPriceLine: (options: Record<string, unknown>) => {
              chartMock.priceLines.push(options)
              return options
            },
            removePriceLine: () => (chartMock.removedPriceLines += 1),
          },
    removeSeries: () => (chartMock.removedLines += 1),
    subscribeCrosshairMove: vi.fn(),
    remove: vi.fn(),
    timeScale: () => ({
      subscribeVisibleLogicalRangeChange: vi.fn(),
      getVisibleLogicalRange: () => null,
      fitContent: vi.fn(),
      setVisibleLogicalRange: vi.fn(),
    }),
  }),
}))

afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
  chartMock.markers = []
  chartMock.lineData = []
  chartMock.priceLines = []
  chartMock.removedLines = 0
  chartMock.removedPriceLines = 0
  chartMock.markerPlugins = 0
})

const FOUR_HOURS = 4 * 3600_000
const START = new Date('2026-10-05T00:00:00Z').getTime()

const candle = (index: number): ChartCandle => ({
  open_time: new Date(START + index * FOUR_HOURS).toISOString(),
  close_time: new Date(START + (index + 1) * FOUR_HOURS).toISOString(),
  open: '100',
  high: '110',
  low: '90',
  close: '105',
  volume: '1',
  source: 'test',
  quality_status: 'complete',
})

const candles = Array.from({ length: 10 }, (_, index) => candle(index))

const fill = (overrides: Partial<FillRow> = {}): FillRow => ({
  fill_id: 'f1',
  order_id: 'o1',
  executed_at: new Date(START + FOUR_HOURS + 8_000).toISOString(), // just after candle 0 closed
  symbol: 'BNBUSDT',
  quote_asset: 'USDT',
  side: 'buy',
  order_type: 'market',
  order_status: 'filled',
  client_order_id: 'dummy-1',
  quantity: '20.953000000000000000',
  price: '795.43',
  notional: '16666',
  fee_amount: '16.6',
  realized_pnl: '0',
  reason: 'signal',
  bot_id: 'b1',
  bot_name: 'bnb-bot',
  ...overrides,
})

const position = (overrides: Partial<PositionRow> = {}): PositionRow => ({
  id: 'p1',
  symbol: 'BNBUSDT',
  quote_asset: 'USDT',
  bot_id: 'b1',
  bot_name: 'bnb-bot',
  side: 'long',
  status: 'closed',
  quantity: '0',
  average_entry_price: '795.43',
  realized_pnl: '-315.58',
  stop_price: '780.37',
  opened_at: new Date(START + FOUR_HOURS + 8_000).toISOString(),
  closed_at: new Date(START + 5 * FOUR_HOURS + 3_000).toISOString(),
  ...overrides,
})

const stop = fill({
  fill_id: 'f2',
  side: 'sell',
  price: '780.37',
  realized_pnl: '-315.58',
  reason: 'stop_loss',
  executed_at: new Date(START + 5 * FOUR_HOURS + 3_000).toISOString(),
})

describe('placing a moment on the candles', () => {
  it('finds the last candle that had closed by then: the one whose close the fill was made at', () => {
    expect(candleAt(candles, new Date(START + FOUR_HOURS + 8_000).toISOString())?.open_time).toBe(
      candles[0].open_time,
    )
    expect(candleAt(candles, candles[3].open_time)?.open_time).toBe(candles[2].open_time) // at the close
    expect(candleAt(candles, new Date(START + FOUR_HOURS - 1000).toISOString())).toBeNull() // not closed yet
  })

  it('uses the last candle for a moment past the newest, and none for one before all closed', () => {
    expect(candleAt(candles, new Date(START + 99 * FOUR_HOURS).toISOString())?.open_time).toBe(
      candles[9].open_time,
    )
    expect(candleAt(candles, new Date(START - 1000).toISOString())).toBeNull()
    expect(candleAt([], '2026-10-05T00:00:00Z')).toBeNull()
  })
})

describe('building the overlay', () => {
  it('marks a signal buy below its bar and a stop-loss exit as an amber square', () => {
    const overlay = buildChartOverlay([fill(), stop], [], candles)

    const [buy, sell] = overlay.markers
    expect(buy).toMatchObject({ position: 'belowBar', shape: 'arrowUp', color: '#4fd1a5', text: '買い 20.953' })
    expect(buy.time).toBe(Math.floor(new Date(candles[0].open_time).getTime() / 1000))
    expect(sell).toMatchObject({ shape: 'square', color: '#f5b84b', position: 'aboveBar' })
    expect(sell.text).toBe('損切り 20.953')
  })

  it('marks a signal sell above its bar, and tells the other exits apart', () => {
    const overlay = buildChartOverlay(
      [
        fill({ fill_id: 'a', side: 'sell', reason: 'signal' }),
        fill({ fill_id: 'b', side: 'sell', reason: 'emergency_close' }),
        fill({ fill_id: 'c', side: 'sell', reason: 'reversal_close' }),
        fill({ fill_id: 'd', side: 'buy', reason: 'manual' }),
      ],
      [],
      candles,
    )

    const byId = Object.fromEntries(overlay.markers.map((marker) => [marker.id, marker]))
    expect(byId.a).toMatchObject({ shape: 'arrowDown', color: '#ff7b78', text: '売り 20.953' })
    expect(byId.b.text).toBe('緊急決済 20.953')
    expect(byId.c.text).toBe('ドテン決済 20.953')
    expect(byId.d).toMatchObject({ text: '手動 20.953', position: 'belowBar' })
  })

  it('keeps markers in time order and counts the fills older than the loaded candles', () => {
    const old = fill({ fill_id: 'old', executed_at: new Date(START - FOUR_HOURS).toISOString() })
    const overlay = buildChartOverlay([stop, fill(), old], [], candles)

    expect(overlay.markers.map((marker) => marker.id)).toEqual(['f1', 'f2'])
    expect(overlay.offscreen).toBe(1)
  })

  it('collects the fills on each candle for the tooltip', () => {
    const overlay = buildChartOverlay([fill(), fill({ fill_id: 'same-bar' })], [], candles)

    const time = Math.floor(new Date(candles[0].open_time).getTime() / 1000)
    expect(overlay.detailsByTime.get(time)?.map((detail) => detail.fillId)).toEqual(['f1', 'same-bar'])
  })

  it('draws a closed position from its entry to its exit, red for a loss and green for a gain', () => {
    const loss = buildChartOverlay([fill(), stop], [position()], candles)
    expect(loss.segments).toHaveLength(1)
    expect(loss.segments[0]).toMatchObject({
      color: '#ff7b78',
      profit: false,
      from: { price: 795.43 },
      to: { price: 780.37 },
    })
    expect(loss.segments[0].from.time).toBeLessThan(loss.segments[0].to.time)

    const gain = buildChartOverlay(
      [fill(), { ...stop, price: '810', realized_pnl: '300' }],
      [position({ realized_pnl: '300' })],
      candles,
    )
    expect(gain.segments[0]).toMatchObject({ color: '#4fd1a5', profit: true })
  })

  it('draws nothing for a position opened and closed on one candle, or with no exit fill', () => {
    const sameBar = position({ closed_at: new Date(START + FOUR_HOURS + 60_000).toISOString() })
    const quick = { ...stop, executed_at: new Date(START + FOUR_HOURS + 60_000).toISOString() }

    expect(buildChartOverlay([fill(), quick], [sameBar], candles).segments).toEqual([])
    expect(buildChartOverlay([fill()], [position()], candles).segments).toEqual([])
  })

  it('draws an open position as price lines for its entry and its stop', () => {
    const open = position({ status: 'open', quantity: '2', closed_at: null, realized_pnl: '0' })

    const overlay = buildChartOverlay([fill()], [open], candles)

    expect(overlay.segments).toEqual([])
    expect(overlay.lines).toEqual([
      expect.objectContaining({ price: 795.43, title: '建値 買い 2', style: 'dashed' }),
      expect.objectContaining({ price: 780.37, title: '損切り', style: 'dotted' }),
    ])
    const noStop = buildChartOverlay([], [{ ...open, stop_price: null }], candles)
    expect(noStop.lines).toHaveLength(1)
  })
})

describe('drawing it on the chart', () => {
  const instrument = { symbol: 'BNBUSDT', quote_asset: 'USDT', price_scale: 2, tick_size: '0.01' }
  const props = {
    candles,
    instrument,
    loadingInitial: false,
    loadingOlder: false,
    error: null,
    hasOlder: false,
    onLoadOlder: () => undefined,
    onDisplayedRangeChange: () => undefined,
  }

  it('hands the markers, the entry-to-exit lines and the price lines to the chart', () => {
    const open = position({ id: 'open', status: 'open', quantity: '2', closed_at: null })
    const overlay = buildChartOverlay([fill(), stop], [position(), open], candles)

    render(<CandleChart {...props} overlay={overlay} />)

    expect(chartMock.markers).toHaveLength(2)
    expect(chartMock.markers[0]).toMatchObject({ id: 'f1', shape: 'arrowUp', text: '買い 20.953' })
    expect(chartMock.lineData).toHaveLength(1)
    expect(chartMock.lineData[0]).toHaveLength(2)
    expect(chartMock.priceLines.map((line) => line.title)).toEqual(['建値 買い 2', '損切り'])
  })

  it('draws nothing without an overlay, and replaces the old drawing when it changes', () => {
    const { rerender } = render(<CandleChart {...props} overlay={null} />)
    expect(chartMock.markerPlugins).toBe(0)

    const first = buildChartOverlay([fill(), stop], [position()], candles)
    rerender(<CandleChart {...props} overlay={first} />)
    expect(chartMock.lineData).toHaveLength(1)

    rerender(<CandleChart {...props} overlay={buildChartOverlay([fill()], [], candles)} />)
    expect(chartMock.removedLines).toBe(1) // the old segment is gone
    expect(chartMock.markers).toHaveLength(1)
    expect(chartMock.markerPlugins).toBe(1) // one plugin, reused
  })

  it('names the fills of the candle under the cursor in the tooltip', () => {
    const overlay = buildChartOverlay([fill(), stop], [], candles)
    const { container } = render(<CandleChart {...props} overlay={overlay} candles={[candles[0]]} />)

    // The tooltip shows the newest candle by default: candle 0 is the one the buy was made at.
    expect(container.querySelector('.chart-tooltip')).toHaveTextContent('約定 買い 20.953 @ 795.43')
  })
})

describe('loading the trades for the chart', () => {
  it('asks for the instrument only on a market page, then fills and positions', async () => {
    const urls: string[] = []
    vi.stubGlobal(
      'fetch',
      vi.fn<typeof fetch>(async (input) => {
        const url = String(input)
        urls.push(url)
        if (url.includes('/position-history')) {
          return new Response(JSON.stringify({ total: 1, items: [position()] }), { status: 200 })
        }
        return new Response(
          JSON.stringify({ items: [fill()], totals: [], next_before: null, next_before_id: null }),
          { status: 200 },
        )
      }),
    )
    const { result, rerender } = renderHook(
      ({ enabled }) => useChartTrades('ws-1', 'i-1', enabled),
      { initialProps: { enabled: false } },
    )
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 20))
    })
    expect(urls).toEqual([])

    rerender({ enabled: true })
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 30))
    })

    expect(urls[0]).toContain('/workspaces/ws-1/fills?instrument_id=i-1')
    expect(urls[1]).toContain('/position-history?instrument_id=i-1&status=open&status=closed')
    expect(result.current.fills.map((f) => f.fill_id)).toEqual(['f1'])
    expect(result.current.positions.map((p) => p.id)).toEqual(['p1'])
  })

  it('follows the cursor for a long history and keeps what it has when a refresh fails', async () => {
    const pages = [
      { items: [fill({ fill_id: 'new' })], totals: [], next_before: '2026-10-07T00:00:00Z', next_before_id: 'new' },
      { items: [fill({ fill_id: 'old' })], totals: [], next_before: null, next_before_id: null },
    ]
    let failing = false
    const urls: string[] = []
    vi.stubGlobal(
      'fetch',
      vi.fn<typeof fetch>(async (input) => {
        const url = String(input)
        urls.push(url)
        if (failing) return new Response('{}', { status: 500 })
        if (url.includes('/position-history')) {
          return new Response(JSON.stringify({ total: 0, items: [] }), { status: 200 })
        }
        return new Response(JSON.stringify(pages.shift()), { status: 200 })
      }),
    )
    const { result } = renderHook(() => useChartTrades('ws-1', 'i-1', true))

    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 40))
    })
    expect(result.current.fills.map((f) => f.fill_id)).toEqual(['new', 'old'])
    expect(urls[1]).toContain('before_id=new')
    failing = true
    expect(result.current.fills).toHaveLength(2)
  })
})

describe('the market panel legend', () => {
  const panelProps = (overrides: Record<string, unknown> = {}) => ({
    visible: true,
    visibleInstruments: [
      {
        id: 'i-1',
        exchange_code: 'binance_public',
        market_code: 'crypto_spot',
        symbol: 'BNBUSDT',
        quote_asset: 'USDT',
        price_scale: 2,
        tick_size: '0.01',
        step_size: '0.0001',
        min_quantity: null,
        min_notional: null,
        status: 'active',
        rules_synced_at: null,
      },
    ],
    isResearch: true,
    activeInstrumentId: 'i-1',
    onSelectInstrument: () => undefined,
    selectedWorkspaceId: 'ws-1',
    timeframe: '4h' as const,
    onTimeframeChange: () => undefined,
    submittingMarketAction: false,
    onStartAutomaticCollection: () => undefined,
    onStopAutomaticCollection: () => undefined,
    marketDataMessage: '',
    subscriptions: [],
    backfillJobs: [],
    coverage: null,
    candles,
    marketDataLoading: false,
    olderCandlesLoading: false,
    candleError: null,
    hasOlderCandles: false,
    onLoadOlder: () => undefined,
    displayedRange: null,
    onDisplayedRangeChange: () => undefined,
    connectionStatus: 'idle' as const,
    lastDataAt: null,
    gapCount: 0,
    lastGapReason: null,
    liveCandle: null,
    ...overrides,
  })

  it('offers the toggle and a legend only when the workspace has trades here', async () => {
    const { default: MarketDataPanel } = await import('./MarketDataPanel')
    const { rerender } = render(<MarketDataPanel {...panelProps()} />)
    expect(screen.queryByLabelText('このワークスペースの取引を重ねて表示')).not.toBeInTheDocument()

    const overlay = buildChartOverlay([fill(), stop], [position()], candles)
    rerender(<MarketDataPanel {...panelProps({ tradeOverlay: overlay, tradeCount: 2 })} />)
    expect(screen.getByLabelText('このワークスペースの取引を重ねて表示')).toBeChecked()
    expect(screen.getByText('約定 2件')).toBeInTheDocument()
    expect(screen.getByText('損切り・緊急決済')).toBeInTheDocument()
    expect(chartMock.markers).toHaveLength(2)
  })

  it('draws the overlay only while the toggle is on, and says how many fills are off screen', async () => {
    const { default: MarketDataPanel } = await import('./MarketDataPanel')
    const onShowTradesChange = vi.fn()
    const overlay = { ...buildChartOverlay([fill()], [], candles), offscreen: 3 }
    const { rerender } = render(
      <MarketDataPanel
        {...panelProps({ tradeOverlay: overlay, tradeCount: 4, showTrades: false, onShowTradesChange })}
      />,
    )
    expect(chartMock.markerPlugins).toBe(0) // off: nothing drawn

    fireEvent.click(screen.getByLabelText('このワークスペースの取引を重ねて表示'))
    expect(onShowTradesChange).toHaveBeenCalledWith(true)

    rerender(<MarketDataPanel {...panelProps({ tradeOverlay: overlay, tradeCount: 4, showTrades: true })} />)
    expect(chartMock.markers).toHaveLength(1)
    expect(screen.getByText(/読み込み済みの足より前の約定が3件あります/)).toBeInTheDocument()
  })
})
