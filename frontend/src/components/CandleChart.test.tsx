// @vitest-environment jsdom
import '@testing-library/jest-dom/vitest'
import { cleanup, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import CandleChart from './CandleChart'
import {
  formatPrice,
  mergeCandlePages,
  movedTowardOlder,
  shouldRequestOlder,
  type ChartCandle,
} from './marketData'

afterEach(cleanup)

const chartMock = vi.hoisted(() => ({
  setData: vi.fn(), remove: vi.fn(), fitContent: vi.fn(),
  createChart: vi.fn(),
}))
vi.mock('lightweight-charts', () => ({
  CandlestickSeries: {}, ColorType: { Solid: 'solid' }, CrosshairMode: { Normal: 0 },
  createChart: chartMock.createChart.mockImplementation(() => ({
    addSeries: () => ({ setData: chartMock.setData }),
    subscribeCrosshairMove: vi.fn(), remove: chartMock.remove,
    timeScale: () => ({
      subscribeVisibleLogicalRangeChange: vi.fn(), getVisibleLogicalRange: () => null,
      fitContent: chartMock.fitContent, setVisibleLogicalRange: vi.fn(),
    }),
  })),
}))

const candle = (openTime: string, close = '100'): ChartCandle => ({
  open_time: openTime,
  close_time: openTime,
  open: '99',
  high: '101',
  low: '98',
  close,
  volume: '10',
  source: 'binance',
  quality_status: 'backfilled',
})

const baseProps = {
  candles: [] as ChartCandle[],
  instrument: { symbol: 'BTCJPY', quote_asset: 'JPY', price_scale: 0, tick_size: '1' },
  loadingInitial: false,
  loadingOlder: false,
  error: null,
  hasOlder: false,
  onLoadOlder: vi.fn(),
  onDisplayedRangeChange: vi.fn(),
}

describe('CandleChart', () => {
  it('initializes when loading ends after candles have already arrived', () => {
    chartMock.createChart.mockClear()
    chartMock.setData.mockClear()
    const rows = [candle('2026-08-27T00:00:00Z')]
    const { rerender } = render(<CandleChart {...baseProps} candles={rows} loadingInitial />)
    expect(chartMock.createChart).not.toHaveBeenCalled()
    rerender(<CandleChart {...baseProps} candles={rows} loadingInitial={false} />)
    expect(chartMock.createChart).toHaveBeenCalledTimes(1)
    expect(chartMock.setData).toHaveBeenCalledTimes(1)
  })
  it('formats tooltip prices with the instrument price scale', () => {
    expect(formatPrice('12567000.000000000000000000', 0)).toBe('12567000')
    expect(formatPrice('147.1', 3)).toBe('147.100')
  })

  it('does not treat the initial range as a user move toward older candles', () => {
    expect(movedTowardOlder(null, 0)).toBe(false)
    expect(movedTowardOlder(0, 0)).toBe(false)
    expect(movedTowardOlder(0, -1)).toBe(true)
    expect(
      shouldRequestOlder({
        hasUserInteracted: false,
        previousVisibleFrom: 0,
        currentVisibleFrom: -1,
        hasOlder: true,
        loadingOlder: false,
      }),
    ).toBe(false)
    expect(
      shouldRequestOlder({
        hasUserInteracted: true,
        previousVisibleFrom: 0,
        currentVisibleFrom: -1,
        hasOlder: true,
        loadingOlder: false,
      }),
    ).toBe(true)
  })

  it('merges older pages chronologically without duplicate timestamps', () => {
    const current = [
      candle('2026-08-27T00:01:00Z'),
      candle('2026-08-27T00:02:00Z', 'old'),
    ]
    const incoming = [
      candle('2026-08-27T00:00:00Z'),
      candle('2026-08-27T00:02:00Z', 'corrected'),
    ]

    const merged = mergeCandlePages(current, incoming)

    expect(merged.map((row) => row.open_time)).toEqual([
      '2026-08-27T00:00:00Z',
      '2026-08-27T00:01:00Z',
      '2026-08-27T00:02:00Z',
    ])
    expect(merged[2].close).toBe('corrected')
  })

  it('shows initial loading, empty, and API failure states separately', () => {
    const { rerender } = render(<CandleChart {...baseProps} loadingInitial />)
    expect(screen.getByText('ローソク足を読み込んでいます。')).toBeInTheDocument()

    rerender(<CandleChart {...baseProps} />)
    expect(screen.getByText('表示できる確定足がまだありません。')).toBeInTheDocument()

    rerender(<CandleChart {...baseProps} error="ローソク足APIへ接続できません。" />)
    expect(screen.getByText('ローソク足APIへ接続できません。')).toBeInTheDocument()
  })
})
