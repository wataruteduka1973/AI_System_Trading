// @vitest-environment jsdom
import '@testing-library/jest-dom/vitest'
import { act, cleanup, fireEvent, render, renderHook, screen, within } from '@testing-library/react'
import { afterEach, expect, it, vi } from 'vitest'
import { BacktestForm } from './BacktestPanel'
import BatchResults from './BatchResults'
import type { BacktestBatchItem, BacktestBatchResponse, BacktestRun } from './types'
import { MAX_BATCH_INSTRUMENTS, useBacktests } from './useBacktests'

afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
})

const run = (id: string, netPnl: string, drawdownPct: string, extra: Partial<BacktestRun> = {}): BacktestRun => ({
  id,
  workspace_id: 'ws-1',
  strategy_version_id: 'sv',
  risk_profile_version_id: 'rp',
  dataset_snapshot_id: 'ds',
  parameters: { timeframe: '4h', initial_equity: '10000' },
  code_version: 'v',
  status: 'succeeded',
  summary_metrics: {
    metrics: {
      trade_count: 12,
      win_count: 6,
      loss_count: 6,
      win_rate: '0.5',
      gross_profit: '900',
      gross_loss: '-400',
      net_pnl: netPnl,
      total_fees: '10',
      profit_factor: '2.25',
      max_drawdown: '500',
      max_drawdown_pct: drawdownPct,
    },
  },
  started_at: null,
  finished_at: null,
  created_at: '2026-10-08T00:00:00Z',
  ...extra,
})

const item = (symbol: string, runs: BacktestRun[], extra: Partial<BacktestBatchItem> = {}): BacktestBatchItem => ({
  instrument_id: `i-${symbol}`,
  symbol,
  bars: 4820,
  runs,
  error_code: null,
  error: null,
  ...extra,
})

const result = (): BacktestBatchResponse => ({
  total_bars: 14460,
  items: [
    item('BTCUSDT', [run('r1', '500', '0.08')]),
    item('ETHUSDT', [run('r2', '-300', '0.03')]),
    item('XRPUSDT', [], { bars: 0, error_code: 'no_candles', error: '指定の期間にローソク足がありません' }),
  ],
})

const symbolsInOrder = () =>
  screen.getAllByRole('row').slice(1).map((row) => within(row).getByRole('rowheader').textContent)

it('compares the instruments side by side, with the drawdown before the profit', () => {
  render(<BatchResults result={result()} onViewDetail={() => undefined} />)

  const headers = screen.getAllByRole('columnheader').map((cell) => cell.textContent)
  expect(headers.indexOf('最大DD')).toBeLessThan(headers.indexOf('純損益'))
  const btc = screen.getByText('BTCUSDT').closest('tr') as HTMLElement
  expect(within(btc).getByText('8.0%')).toBeInTheDocument()
  expect(within(btc).getByText('+500')).toHaveClass('pnl-positive')
  expect(within(btc).getByText('+5.0%')).toBeInTheDocument() // 500 on 10,000
  const eth = screen.getByText('ETHUSDT').closest('tr') as HTMLElement
  expect(within(eth).getByText('-300')).toHaveClass('pnl-negative')
  expect(screen.getByText(/3銘柄\(成功 2・失敗 1\)、足の合計 14,460本/)).toBeInTheDocument()
  expect(screen.getByText(/ポートフォリオの成績ではありません/)).toBeInTheDocument()
})

it('says why an instrument failed, in its own row, and does not hide the others', () => {
  render(<BatchResults result={result()} onViewDetail={() => undefined} />)

  const xrp = screen.getByText('XRPUSDT').closest('tr') as HTMLElement
  expect(xrp).toHaveTextContent('指定の期間にローソク足がありません')
  expect(xrp).toHaveTextContent('no_candles')
  expect(within(xrp).queryByRole('button')).not.toBeInTheDocument()
  expect(screen.getByText('BTCUSDT')).toBeInTheDocument()
})

it('sorts by the smallest drawdown or the biggest profit, failures last', () => {
  render(<BatchResults result={result()} onViewDetail={() => undefined} />)
  expect(symbolsInOrder()).toEqual(['BTCUSDT', 'ETHUSDT', 'XRPUSDT'])

  fireEvent.change(screen.getByLabelText('並び順'), { target: { value: 'drawdown' } })
  expect(symbolsInOrder()).toEqual(['ETHUSDT', 'BTCUSDT', 'XRPUSDT'])

  fireEvent.change(screen.getByLabelText('並び順'), { target: { value: 'profit' } })
  expect(symbolsInOrder()).toEqual(['BTCUSDT', 'ETHUSDT', 'XRPUSDT'])
})

it('shows the training and test runs of a walk-forward batch as two rows', () => {
  const walk = item('BTCUSDT', [
    run('t', '100', '0.02', { parameters: { initial_equity: '10000', walk_forward_role: 'train' } }),
    run('v', '-20', '0.04', { parameters: { initial_equity: '10000', walk_forward_role: 'test' } }),
  ])
  render(<BatchResults result={{ total_bars: 100, items: [walk] }} onViewDetail={() => undefined} />)

  expect(screen.getByText('訓練')).toBeInTheDocument()
  expect(screen.getByText('検証')).toBeInTheDocument()
})

it('opens the run detail from its row', () => {
  const onViewDetail = vi.fn()
  render(<BatchResults result={result()} onViewDetail={onViewDetail} />)

  const btc = screen.getByText('BTCUSDT').closest('tr') as HTMLElement
  fireEvent.click(within(btc).getByRole('button', { name: '詳細を見る' }))

  expect(onViewDetail).toHaveBeenCalledWith(result().items[0].runs[0])
})

it('renders nothing before a batch has run', () => {
  const { container } = render(<BatchResults result={null} onViewDetail={() => undefined} />)

  expect(container).toBeEmptyDOMElement()
})

const form = (overrides: Record<string, unknown> = {}) =>
  render(
    <BacktestForm
      visible
      workspaceInstruments={[
        { id: 'a', symbol: 'BTCUSDT' },
        { id: 'b', symbol: 'ETHUSDT' },
      ] as never}
      researchInstruments={[{ id: 'c', symbol: 'SOLUSDT' }] as never}
      backtestMessage=""
      instrumentId=""
      onInstrumentIdChange={() => undefined}
      timeframe="4h"
      onTimeframeChange={() => undefined}
      fromTime="2026-01-01T00:00"
      onFromTimeChange={() => undefined}
      toTime="2026-02-01T00:00"
      onToTimeChange={() => undefined}
      initialEquity="10000"
      onInitialEquityChange={() => undefined}
      spread="0"
      onSpreadChange={() => undefined}
      walkForward={false}
      onWalkForwardChange={() => undefined}
      trainRatio="0.7"
      onTrainRatioChange={() => undefined}
      onCreateBacktest={() => undefined}
      {...overrides}
    />,
  )

it('keeps the single-instrument form by default and offers the multi mode', () => {
  const onBatchModeChange = vi.fn()
  form({ onBatchModeChange })

  expect(screen.getByLabelText('銘柄')).toBeInTheDocument()
  expect(screen.queryByRole('group', { name: /銘柄を選ぶ/ })).not.toBeInTheDocument()
  fireEvent.click(screen.getByRole('button', { name: '複数銘柄をまとめて比較' }))
  expect(onBatchModeChange).toHaveBeenCalledWith(true)
})

it('lets several instruments be ticked, runs them as one batch and shows the limit', () => {
  const onToggle = vi.fn()
  const onCreateBatch = vi.fn()
  form({
    batchMode: true,
    batchInstrumentIds: ['a', 'c'],
    onToggleBatchInstrument: onToggle,
    onCreateBatch,
  })

  expect(screen.getByText('銘柄を選ぶ(2 / 12)')).toBeInTheDocument()
  expect(screen.getByLabelText('BTCUSDT')).toBeChecked()
  expect(screen.getByLabelText('ETHUSDT')).not.toBeChecked()
  expect(screen.getByLabelText('SOLUSDT (検証用)')).toBeChecked()
  expect(screen.queryByLabelText('銘柄')).not.toBeInTheDocument()
  expect(screen.getByText(/30万本を超えるとまとめて断られる/)).toBeInTheDocument()
  fireEvent.click(screen.getByLabelText('ETHUSDT'))
  expect(onToggle).toHaveBeenCalledWith('b')
  fireEvent.click(screen.getByRole('button', { name: '2銘柄を実行' }))
  expect(onCreateBatch).toHaveBeenCalled()
})

it('cannot run a batch with nothing ticked, and stops offering more at the limit', () => {
  form({ batchMode: true, batchInstrumentIds: [] })
  expect(screen.getByRole('button', { name: '0銘柄を実行' })).toBeDisabled()
  cleanup()

  form({ batchMode: true, batchInstrumentIds: ['a'], maxBatchInstruments: 1 })
  expect(screen.getByLabelText('ETHUSDT')).toBeDisabled()
  expect(screen.getByLabelText('BTCUSDT')).toBeEnabled() // it can still be un-ticked
})

it('posts one batch request with the shared settings and keeps the result', async () => {
  const response: BacktestBatchResponse = result()
  const fetchMock = vi.fn<typeof fetch>(async (_input, init) =>
    init?.method === 'POST'
      ? new Response(JSON.stringify(response), { status: 201 })
      : new Response('[]', { status: 200 }),
  )
  vi.stubGlobal('fetch', fetchMock)
  const { result: hook } = renderHook(() => useBacktests('ws-1'))

  act(() => {
    hook.current.toggleBatchInstrument('a')
    hook.current.toggleBatchInstrument('b')
    hook.current.setFromTime('2026-01-01T00:00')
    hook.current.setToTime('2026-02-01T00:00')
    hook.current.setTimeframe('4h')
  })
  await act(async () => {
    await hook.current.createBatch()
  })

  const call = fetchMock.mock.calls.find(([, init]) => init?.method === 'POST')
  expect(String(call?.[0])).toMatch(/\/workspaces\/ws-1\/backtests\/batch$/)
  const body = JSON.parse(String(call?.[1]?.body))
  expect(body).toMatchObject({
    instrument_ids: ['a', 'b'],
    timeframe: '4h',
    initial_equity: '10000',
    mode: 'single',
  })
  expect(hook.current.batchResult?.items).toHaveLength(3)
  expect(hook.current.backtestMessage).toContain('3銘柄のうち1銘柄は実行できませんでした')
})

it('shows the server reason when the batch is refused for its size', async () => {
  const detail = '対象の足が合計957,565本で、上限の300,000本を超えています(BTCUSDT 957,565本)'
  vi.stubGlobal(
    'fetch',
    vi.fn<typeof fetch>(async (_input, init) =>
      init?.method === 'POST'
        ? new Response(JSON.stringify({ detail }), { status: 422 })
        : new Response('[]', { status: 200 }),
    ),
  )
  const { result: hook } = renderHook(() => useBacktests('ws-1'))

  act(() => {
    hook.current.toggleBatchInstrument('a')
    hook.current.setFromTime('2024-01-01T00:00')
    hook.current.setToTime('2026-01-01T00:00')
  })
  await act(async () => {
    await hook.current.createBatch()
  })

  expect(hook.current.backtestMessage).toContain(detail)
  expect(hook.current.batchResult).toBeNull()
})

it('never selects more instruments than the server accepts', () => {
  vi.stubGlobal('fetch', vi.fn<typeof fetch>(async () => new Response('[]', { status: 200 })))
  const { result: hook } = renderHook(() => useBacktests('ws-1'))

  act(() => {
    for (let index = 0; index < MAX_BATCH_INSTRUMENTS + 3; index += 1) {
      hook.current.toggleBatchInstrument(`i${index}`)
    }
  })

  expect(hook.current.batchInstrumentIds).toHaveLength(MAX_BATCH_INSTRUMENTS)
})
