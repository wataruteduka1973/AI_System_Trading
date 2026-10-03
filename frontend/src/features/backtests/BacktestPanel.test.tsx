// @vitest-environment jsdom
import '@testing-library/jest-dom/vitest'
import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, expect, it, vi } from 'vitest'
import type { WorkspaceInstrument } from '../instruments/types'
import BacktestPanel, { BacktestForm } from './BacktestPanel'
import type { BacktestRun, BacktestTrade } from './types'

afterEach(cleanup)

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
      selectedRunTrades={null}
      onViewTrades={noop}
    />,
  )

  expect(screen.getByText('勝率: 50.0%')).toBeInTheDocument()
  expect(screen.getByText('最大DD: 5.8%')).toBeInTheDocument()
  expect(screen.getByText('純損益: 60')).toBeInTheDocument()
  expect(screen.getByText('検証期間')).toBeInTheDocument()
})

it('says so when a run has no metrics, and opens its trades on request', () => {
  const onViewTrades = vi.fn()
  render(
    <BacktestPanel
      visible
      backtests={[run({ status: 'failed', summary_metrics: {} })]}
      selectedRunTrades={null}
      onViewTrades={onViewTrades}
    />,
  )

  expect(screen.getByText('指標なし')).toBeInTheDocument()
  expect(screen.getByText('失敗')).toBeInTheDocument()
  fireEvent.click(screen.getByRole('button', { name: '取引を見る' }))
  expect(onViewTrades).toHaveBeenCalledWith(expect.objectContaining({ id: 'run-1' }))
})

it('lists trades, marking a position still open at the end of the range', () => {
  render(
    <BacktestPanel
      visible
      backtests={[]}
      selectedRunTrades={{
        runId: 'run-1',
        trades: [trade(), trade({ id: 't2', sequence_no: 2, exit_price: null, realized_pnl: null })],
      }}
      onViewTrades={noop}
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
      selectedRunTrades={{ runId: 'run-1', trades: [] }}
      onViewTrades={noop}
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
