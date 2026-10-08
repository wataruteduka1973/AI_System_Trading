// @vitest-environment jsdom
import '@testing-library/jest-dom/vitest'
import { act, cleanup, fireEvent, render, renderHook, screen } from '@testing-library/react'
import { afterEach, expect, it, vi } from 'vitest'
import type { WorkspaceInstrument } from '../instruments/types'
import BacktestPanel, { BacktestForm } from './BacktestPanel'
import type { BacktestMetrics, BacktestRun, BacktestRunDetail, BacktestTrade, EquityCurve } from './types'
import { useBacktests } from './useBacktests'

vi.mock('./EquityCurveChart', () => ({
  default: ({ curve }: { curve: EquityCurve }) => (
    <div data-testid="equity-curve">{curve.points.map((point) => point.equity).join(',')}</div>
  ),
}))

afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
})

const noop = () => undefined

const run = (overrides: Partial<BacktestRun> = {}): BacktestRun => ({
  id: 'run-1',
  workspace_id: 'ws-1',
  strategy_version_id: 'sv-1',
  risk_profile_version_id: 'rp-1',
  dataset_snapshot_id: 'ds-1',
  parameters: { timeframe: '4h' },
  code_version: 'approved-strategy-0.1',
  status: 'succeeded',
  summary_metrics: {
    metrics: {
      trade_count: 10,
      win_count: 5,
      loss_count: 5,
      win_rate: '0.5',
      gross_profit: '120',
      gross_loss: '-60',
      net_pnl: '60',
      total_fees: '4',
      profit_factor: '2',
      max_drawdown: '58000',
      max_drawdown_pct: '0.058',
    },
  },
  started_at: null,
  finished_at: null,
  created_at: '2026-10-03T00:00:00Z',
  ...overrides,
})

const trade = (overrides: Partial<BacktestTrade> = {}): BacktestTrade => ({
  id: 't1',
  backtest_run_id: 'run-1',
  sequence_no: 1,
  instrument_id: 'i1',
  side: 'buy',
  entry_time: '2026-01-01T00:00:00Z',
  exit_time: '2026-01-02T00:00:00Z',
  entry_price: '100',
  exit_price: '110',
  quantity: '1',
  fees: '0.1',
  realized_pnl: '9.9',
  ...overrides,
})

const curve = (): EquityCurve => ({
  initial_equity: '1000',
  points: [
    { time: '2026-01-01T00:00:00Z', equity: '1000' },
    { time: '2026-03-01T00:00:00Z', equity: '1060' },
  ],
})

const detail = (trades: BacktestTrade[], equityCurve: EquityCurve = curve()): BacktestRunDetail => ({
  runId: 'run-1',
  trades,
  equityCurve,
})

const instrument = (id: string, symbol: string): WorkspaceInstrument => ({
  id,
  exchange_code: 'binance',
  market_code: 'crypto_spot',
  symbol,
  quote_asset: 'USDT',
  price_scale: 2,
  tick_size: '0.01',
  step_size: '0.0001',
  min_quantity: null,
  min_notional: null,
  status: 'active',
  rules_synced_at: null,
})

it('shows rates as percentages and labels walk-forward windows', () => {
  render(
    <BacktestPanel
      visible
      backtests={[run({ parameters: { timeframe: '4h', walk_forward_role: 'test' } })]}
      selectedRunDetail={null}
      onViewDetail={noop}
    />,
  )

  expect(screen.getByText('勝率: 50.0%')).toBeInTheDocument()
  expect(screen.getByText('最大DD: 5.8%')).toBeInTheDocument()
  expect(screen.getByText('純損益: 60')).toBeInTheDocument()
  expect(screen.getByText('検証期間')).toBeInTheDocument()
})

it('says so when a run has no metrics, and opens its detail on request', () => {
  const onViewDetail = vi.fn()
  render(
    <BacktestPanel
      visible
      backtests={[run({ status: 'failed', summary_metrics: {} })]}
      selectedRunDetail={null}
      onViewDetail={onViewDetail}
    />,
  )

  expect(screen.getByText('指標なし')).toBeInTheDocument()
  expect(screen.getByText('失敗')).toBeInTheDocument()
  fireEvent.click(screen.getByRole('button', { name: '詳細を見る' }))
  expect(onViewDetail).toHaveBeenCalledWith(expect.objectContaining({ id: 'run-1' }))
})

it('lists trades, marking a position still open at the end of the range', () => {
  render(
    <BacktestPanel
      visible
      backtests={[]}
      selectedRunDetail={detail([
        trade(),
        trade({ id: 't2', sequence_no: 2, exit_price: null, realized_pnl: null }),
      ])}
      onViewDetail={noop}
    />,
  )

  expect(screen.getByText('取引一覧(2件)')).toBeInTheDocument()
  expect(screen.getByText('100 → 110')).toBeInTheDocument()
  expect(screen.getByText('100 → (保有中)')).toBeInTheDocument()
})

it('says so when a run had no trades', () => {
  render(
    <BacktestPanel
      visible
      backtests={[]}
      selectedRunDetail={detail([])}
      onViewDetail={noop}
    />,
  )

  expect(screen.getByText('この実行では取引が発生しませんでした。')).toBeInTheDocument()
})

const renderForm = (overrides: Partial<Parameters<typeof BacktestForm>[0]> = {}) =>
  render(
    <BacktestForm
      visible
      workspaceInstruments={[instrument('w1', 'BTCUSDT')]}
      researchInstruments={[instrument('r1', 'ETHUSDT')]}
      backtestMessage=""
      instrumentId="r1"
      onInstrumentIdChange={noop}
      timeframe="4h"
      onTimeframeChange={noop}
      fromTime="2024-01-01T00:00"
      onFromTimeChange={noop}
      toTime="2026-10-01T00:00"
      onToTimeChange={noop}
      initialEquity="1000000"
      onInitialEquityChange={noop}
      spread="0"
      onSpreadChange={noop}
      walkForward={false}
      onWalkForwardChange={noop}
      trainRatio="0.7"
      onTrainRatioChange={noop}
      onCreateBacktest={noop}
      {...overrides}
    />,
  )

it('separates research instruments from the workspace ones', () => {
  renderForm()

  expect(screen.getByRole('group', { name: '取引用(自分の接続)' })).toBeInTheDocument()
  expect(screen.getByRole('option', { name: 'ETHUSDT (検証用)' })).toBeInTheDocument()
})

it.each([
  ['instrumentId', ''],
  ['fromTime', ''],
  ['toTime', ''],
  ['initialEquity', ''],
])('cannot run without %s', (field, value) => {
  renderForm({ [field]: value })

  expect(screen.getByRole('button', { name: '実行' })).toBeDisabled()
})

it('asks for the training share only for a walk-forward run', () => {
  const onCreateBacktest = vi.fn()
  renderForm({ onCreateBacktest })
  expect(screen.queryByLabelText('訓練期間の割合')).not.toBeInTheDocument()
  fireEvent.click(screen.getByRole('button', { name: '実行' }))
  expect(onCreateBacktest).toHaveBeenCalledOnce()

  cleanup()
  renderForm({ walkForward: true })
  expect(screen.getByLabelText('訓練期間の割合')).toHaveValue('0.7')
})


it('shows the picked run with every metric, the return on the starting equity and the curve', () => {
  render(
    <BacktestPanel
      visible
      backtests={[run({ parameters: { timeframe: '4h', initial_equity: '1000', spread: '0' } })]}
      selectedRunDetail={detail([trade()])}
      onViewDetail={noop}
    />,
  )

  expect(screen.getByText('実行の詳細')).toBeInTheDocument()
  expect(screen.getByText('+60')).toBeInTheDocument() // 純損益
  expect(screen.getByText('6.0%')).toBeInTheDocument() // 60 on 1000
  expect(screen.getByText('5.8%(58,000)')).toBeInTheDocument()
  expect(screen.getByText('プロフィットファクター').nextSibling).toHaveTextContent('2')
  expect(screen.getByText('手数料合計').nextSibling).toHaveTextContent('4')
  expect(screen.getByText('5勝5敗', { exact: false })).toBeInTheDocument()
  expect(screen.getByTestId('equity-curve')).toHaveTextContent('1000,1060')
  expect(screen.getByText('期間', { exact: false })).toHaveTextContent('2026/1/1')
})

it('does not invent a profit factor when no trade lost', () => {
  const base = run({ parameters: { initial_equity: '1000' } })
  const metrics = { ...base.summary_metrics.metrics!, profit_factor: null }
  render(
    <BacktestPanel
      visible
      backtests={[{ ...base, summary_metrics: { metrics } }]}
      selectedRunDetail={detail([])}
      onViewDetail={noop}
    />,
  )

  expect(screen.getByText('プロフィットファクター').nextSibling).toHaveTextContent('算出不可(負け無し)')
})

it('rounds the exact Decimal strings the API sends', () => {
  const base = run({ parameters: { initial_equity: '1000000' } })
  const metrics = {
    ...base.summary_metrics.metrics!,
    net_pnl: '112452.5412724390215028812109',
    profit_factor: '0E+26',
    gross_profit: '0',
    gross_loss: '14.40904431000000000000000000',
    total_fees: '3.954768310000000000000000000',
  }
  render(
    <BacktestPanel
      visible
      backtests={[{ ...base, summary_metrics: { metrics } }]}
      selectedRunDetail={detail([])}
      onViewDetail={noop}
    />,
  )

  expect(screen.getByText('純損益').nextSibling).toHaveTextContent('+112,452.54')
  expect(screen.getByText('プロフィットファクター').nextSibling).toHaveTextContent(/^0$/)
  expect(screen.getByText('総利益 / 総損失').nextSibling).toHaveTextContent('0 / 14.41')
  expect(screen.getByText('手数料合計').nextSibling).toHaveTextContent(/^3\.95$/)
})

it('compares with the baseline when the run has one', () => {
  const base = run({ parameters: { initial_equity: '1000' } })
  const withBaseline: BacktestRun = {
    ...base,
    summary_metrics: {
      ...base.summary_metrics,
      baseline_comparison: {
        baseline: base.summary_metrics.metrics!,
        net_pnl_diff: '25',
        win_rate_diff: '0.1',
        max_drawdown_pct_diff: '-0.02',
        profit_factor_diff: null,
      },
    },
  }
  render(
    <BacktestPanel
      visible
      backtests={[withBaseline]}
      selectedRunDetail={detail([])}
      onViewDetail={noop}
    />,
  )

  expect(screen.getByText('基準(baseline)との差', { exact: false })).toHaveTextContent(
    '純損益 +25 / 勝率 +10pt / 最大DD -2pt',
  )
})

it('shows a run without a stored curve, and leaves the return blank when the start is unknown', () => {
  render(
    <BacktestPanel
      visible
      backtests={[run()]}
      selectedRunDetail={detail([], { initial_equity: null, points: [] })}
      onViewDetail={noop}
    />,
  )

  expect(screen.getByText('リターン').nextSibling).toHaveTextContent('-')
  expect(screen.getByTestId('equity-curve')).toBeEmptyDOMElement()
})

it('loads a run’s trades and equity curve together', async () => {
  const fetchMock = vi.fn<typeof fetch>(async (input) => {
    const url = String(input)
    if (url.endsWith('/trades')) return new Response(JSON.stringify([trade()]), { status: 200 })
    if (url.endsWith('/equity-curve')) return new Response(JSON.stringify(curve()), { status: 200 })
    return new Response('[]', { status: 200 })
  })
  vi.stubGlobal('fetch', fetchMock)
  const { result } = renderHook(() => useBacktests('ws-1'))

  await act(async () => {
    await result.current.loadRunDetail(run())
  })

  expect(result.current.selectedRunDetail?.runId).toBe('run-1')
  expect(result.current.selectedRunDetail?.trades).toHaveLength(1)
  expect(result.current.selectedRunDetail?.equityCurve.points).toHaveLength(2)
})

it('still shows the trades when the curve cannot be fetched', async () => {
  const fetchMock = vi.fn<typeof fetch>(async (input) =>
    String(input).endsWith('/equity-curve')
      ? new Response('{}', { status: 500 })
      : new Response(JSON.stringify([trade()]), { status: 200 }),
  )
  vi.stubGlobal('fetch', fetchMock)
  const { result } = renderHook(() => useBacktests('ws-1'))

  await act(async () => {
    await result.current.loadRunDetail(run())
  })

  expect(result.current.selectedRunDetail?.trades).toHaveLength(1)
  expect(result.current.selectedRunDetail?.equityCurve).toEqual({ initial_equity: null, points: [] })
})

it('shows the net P&L of a run in the list rounded, not as the exact decimal string', () => {
  const long = run({
    summary_metrics: {
      metrics: { ...(run().summary_metrics.metrics as BacktestMetrics), net_pnl: '112452.5412724390215028812109' },
    },
  })
  render(<BacktestPanel visible backtests={[long]} selectedRunDetail={null} onViewDetail={noop} />)

  expect(screen.getByText('純損益: 112,452.54')).toBeInTheDocument()
})
