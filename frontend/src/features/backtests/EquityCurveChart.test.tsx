// @vitest-environment jsdom
import '@testing-library/jest-dom/vitest'
import { cleanup, render, screen } from '@testing-library/react'
import { afterEach, expect, it, vi } from 'vitest'
import EquityCurveChart from './EquityCurveChart'

afterEach(() => {
  cleanup()
  vi.clearAllMocks()
})

const chartMock = vi.hoisted(() => ({
  setData: vi.fn(),
  createPriceLine: vi.fn(),
  remove: vi.fn(),
  fitContent: vi.fn(),
  createChart: vi.fn(),
}))
vi.mock('lightweight-charts', () => ({
  AreaSeries: {},
  ColorType: { Solid: 'solid' },
  LineStyle: { Dashed: 2 },
  createChart: chartMock.createChart.mockImplementation(() => ({
    addSeries: () => ({ setData: chartMock.setData, createPriceLine: chartMock.createPriceLine }),
    timeScale: () => ({ fitContent: chartMock.fitContent }),
    remove: chartMock.remove,
  })),
}))

it('draws each stored point once, with the starting equity as a reference line', () => {
  const { unmount } = render(
    <EquityCurveChart
      curve={{
        initial_equity: '1000',
        points: [
          { time: '2026-01-01T00:00:00Z', equity: '1000' },
          { time: '2026-01-01T00:00:00Z', equity: '999' }, // same second: dropped, not an error
          { time: '2026-01-02T00:00:00Z', equity: '1050.5' },
        ],
      }}
    />,
  )

  expect(chartMock.setData).toHaveBeenCalledWith([
    { time: Date.parse('2026-01-01T00:00:00Z') / 1000, value: 1000 },
    { time: Date.parse('2026-01-02T00:00:00Z') / 1000, value: 1050.5 },
  ])
  expect(chartMock.createPriceLine).toHaveBeenCalledWith(expect.objectContaining({ price: 1000 }))
  unmount()
  expect(chartMock.remove).toHaveBeenCalledOnce()
})

it('explains an empty curve instead of drawing an empty chart', () => {
  render(<EquityCurveChart curve={{ initial_equity: null, points: [] }} />)

  expect(screen.getByText(/資産曲線が保存されていません/)).toBeInTheDocument()
  expect(chartMock.createChart).not.toHaveBeenCalled()
})

it('omits the reference line when the starting equity is unknown', () => {
  render(
    <EquityCurveChart
      curve={{ initial_equity: null, points: [{ time: '2026-01-01T00:00:00Z', equity: '5' }] }}
    />,
  )

  expect(chartMock.setData).toHaveBeenCalledOnce()
  expect(chartMock.createPriceLine).not.toHaveBeenCalled()
})
