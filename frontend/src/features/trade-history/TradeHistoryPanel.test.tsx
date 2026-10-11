// @vitest-environment jsdom
import '@testing-library/jest-dom/vitest'
import { act, cleanup, fireEvent, render, renderHook, screen, within } from '@testing-library/react'
import { afterEach, expect, it, vi } from 'vitest'
import { formatHeld } from './labels'
import TradeHistoryPanel from './TradeHistoryPanel'
import type { FillPage, FillRow, FillTotals, HistoryFilters, PositionPage, PositionRow } from './types'
import { defaultHistoryFilters } from './types'
import { buildFillQuery, buildPositionQuery, useTradeHistory } from './useTradeHistory'

afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
})

const fill = (overrides: Partial<FillRow> = {}): FillRow => ({
  fill_id: 'f1',
  order_id: 'o1',
  executed_at: '2026-10-07T12:32:50Z',
  symbol: 'BNBUSDT',
  quote_asset: 'USDT',
  side: 'sell',
  order_type: 'market',
  order_status: 'filled',
  client_order_id: 'stop-abc123',
  quantity: '20.953000000000000000',
  price: '780.368601000000000000',
  notional: '16351.27',
  fee_amount: '16.35',
  realized_pnl: '-315.58',
  reason: 'stop_loss',
  bot_id: 'b1',
  bot_name: 'bnbusdt-4h-donchian',
  ...overrides,
})

const totals: FillTotals[] = [
  { quote_asset: 'USDT', fees: '33.02', fills: 2, realized_pnl: '-315.58' },
]

const position = (overrides: Partial<PositionRow> = {}): PositionRow => ({
  id: 'p1',
  symbol: 'BNBUSDT',
  quote_asset: 'USDT',
  bot_id: 'b1',
  bot_name: 'bnbusdt-4h-donchian',
  side: 'long',
  status: 'closed',
  quantity: '0',
  average_entry_price: '795.43',
  realized_pnl: '-315.58',
  stop_price: null,
  opened_at: '2026-10-05T01:02:00Z',
  closed_at: '2026-10-07T12:32:00Z',
  ...overrides,
})

const panel = (props: Partial<Parameters<typeof TradeHistoryPanel>[0]> = {}) =>
  render(
    <TradeHistoryPanel
      tab="fills"
      onTabChange={() => undefined}
      filters={defaultHistoryFilters}
      onFiltersChange={() => undefined}
      bots={[{ id: 'b1', name: 'bnbusdt-4h-donchian' }]}
      fills={[fill()]}
      totals={totals}
      positions={[]}
      positionTotal={0}
      hasMore={false}
      loading={false}
      error=""
      loadedAt={null}
      onLoadMore={() => undefined}
      onReload={() => undefined}
      {...props}
    />,
  )

it('shows each fill with its bot, price, fee, result and why it was placed', () => {
  panel()

  const row = screen.getByText('bnbusdt-4h-donchian', { selector: 'td' }).closest('tr') as HTMLElement
  expect(within(row).getByText('BNBUSDT')).toBeInTheDocument()
  expect(within(row).getByText('売り')).toBeInTheDocument()
  expect(within(row).getByText('20.953')).toBeInTheDocument()
  expect(within(row).getByText('780.3686')).toBeInTheDocument()
  expect(within(row).getByText('-315.58')).toHaveClass('pnl-negative')
  expect(within(row).getByText('損切り')).toHaveClass('bot-state-failed')
})

it('does not show a profit figure for a fill that only opened a position', () => {
  panel({ fills: [fill({ side: 'buy', realized_pnl: '0', reason: 'signal', client_order_id: 'dummy-x' })] })

  const row = screen.getByText('買い', { selector: 'td' }).closest('tr') as HTMLElement
  expect(within(row).getByText('シグナル')).toBeInTheDocument()
  expect(within(row).queryByText(/^[+-]?0$/)).not.toBeInTheDocument()
  expect(within(row).getAllByText('-').length).toBeGreaterThan(0)
})

it('labels a manual order and an order with no bot', () => {
  panel({ fills: [fill({ reason: 'manual', bot_id: null, bot_name: null, client_order_id: 'by-hand' })] })

  const row = screen.getByText('手動', { selector: 'span' }).closest('tr') as HTMLElement
  expect(within(row).getByText('手動', { selector: 'td' })).toBeInTheDocument()
})

it('totals the fills per asset, with the result before and after fees', () => {
  panel()

  const strip = screen.getByLabelText('合計')
  expect(strip).toHaveTextContent('USDT: 約定 2件 / 手数料 33.02')
  expect(strip).toHaveTextContent('確定損益 -315.58(手数料後 -348.6)')
})

it('opens the order details of a fill and closes them again', () => {
  panel()

  expect(screen.queryByText('クライアント注文ID')).not.toBeInTheDocument()
  fireEvent.click(screen.getByRole('button', { name: '詳細' }))
  expect(screen.getByText('stop-abc123')).toBeInTheDocument()
  expect(screen.getByText('market / filled')).toBeInTheDocument()
  fireEvent.click(screen.getByRole('button', { name: '詳細' }))
  expect(screen.queryByText('stop-abc123')).not.toBeInTheDocument()
})

it('changes the filters and offers to clear them only when they differ from the default', () => {
  const onFiltersChange = vi.fn()
  const { rerender } = panel({ onFiltersChange })
  expect(screen.queryByRole('button', { name: '条件をクリア' })).not.toBeInTheDocument()

  fireEvent.change(screen.getByLabelText('Bot'), { target: { value: 'b1' } })
  expect(onFiltersChange).toHaveBeenLastCalledWith({ ...defaultHistoryFilters, botId: 'b1' })
  fireEvent.change(screen.getByLabelText('売買'), { target: { value: 'sell' } })
  expect(onFiltersChange).toHaveBeenLastCalledWith({ ...defaultHistoryFilters, side: 'sell' })
  fireEvent.change(screen.getByLabelText('期間'), { target: { value: '24h' } })
  expect(onFiltersChange).toHaveBeenLastCalledWith({ ...defaultHistoryFilters, period: '24h' })

  const narrowed: HistoryFilters = { ...defaultHistoryFilters, side: 'sell' }
  rerender(
    <TradeHistoryPanel
      tab="fills"
      onTabChange={() => undefined}
      filters={narrowed}
      onFiltersChange={onFiltersChange}
      bots={[]}
      fills={[]}
      totals={[]}
      positions={[]}
      positionTotal={0}
      hasMore={false}
      loading={false}
      error=""
      loadedAt={null}
      onLoadMore={() => undefined}
      onReload={() => undefined}
    />,
  )
  fireEvent.click(screen.getByRole('button', { name: '条件をクリア' }))
  expect(onFiltersChange).toHaveBeenLastCalledWith(defaultHistoryFilters)
})

it('explains an empty history instead of showing an empty table', () => {
  panel({ fills: [], totals: [] })

  expect(screen.getByText(/条件に合う約定はありません/)).toBeInTheDocument()
  expect(screen.getByText(/リスク判定を通ったときだけ注文します/)).toBeInTheDocument()
  expect(screen.queryByRole('table')).not.toBeInTheDocument()
})

it('shows an error instead of an empty history when the load fails', () => {
  panel({ fills: [], error: '取引履歴APIへ接続できません。' })

  expect(screen.getByRole('alert')).toHaveTextContent('接続できません')
  expect(screen.queryByText(/条件に合う約定はありません/)).not.toBeInTheDocument()
})

it('switches to the positions tab and shows open and closed ones', () => {
  const onTabChange = vi.fn()
  panel({ onTabChange })
  fireEvent.click(screen.getByRole('button', { name: '建玉' }))
  expect(onTabChange).toHaveBeenCalledWith('positions')
  cleanup()

  panel({
    tab: 'positions',
    positions: [
      position({ id: 'open', status: 'open', quantity: '2', realized_pnl: '0', closed_at: null, stop_price: '90' }),
      position(),
    ],
    positionTotal: 2,
  })

  const rows = screen.getAllByRole('row').slice(1)
  expect(within(rows[0]).getByText('保有中')).toBeInTheDocument()
  expect(within(rows[0]).getByText('90')).toBeInTheDocument()
  expect(within(rows[1]).getByText('決済済み')).toBeInTheDocument()
  expect(within(rows[1]).getByText('-315.58')).toHaveClass('pnl-negative')
  expect(within(rows[1]).getByText('2日と12時間')).toBeInTheDocument()
  expect(screen.getByText('2 / 2件を表示')).toBeInTheDocument()
})

it('formats how long a position was held', () => {
  expect(formatHeld(null, null)).toBe('-')
  expect(formatHeld('2026-01-01T00:00:00Z', '2026-01-01T00:20:00Z')).toBe('20分')
  expect(formatHeld('2026-01-01T00:00:00Z', '2026-01-01T07:00:00Z')).toBe('7時間')
  expect(formatHeld('2026-01-01T00:00:00Z', '2026-01-04T03:00:00Z')).toBe('3日と3時間')
})

it('builds the queries from the filters', () => {
  const now = new Date('2026-10-08T12:00:00Z')

  const fills = new URLSearchParams(
    buildFillQuery({ botId: 'b1', side: 'sell', period: '24h', showClosed: true }, now, {
      before: '2026-10-07T00:00:00Z',
      beforeId: 'f9',
    }),
  )
  expect(fills.get('bot_id')).toBe('b1')
  expect(fills.get('side')).toBe('sell')
  expect(fills.get('from_time')).toBe('2026-10-07T12:00:00.000Z')
  expect(fills.get('before_id')).toBe('f9')
  expect(new URLSearchParams(buildFillQuery({ ...defaultHistoryFilters, period: 'all' }, now, null)).has('from_time')).toBe(
    false,
  )

  const open = new URLSearchParams(buildPositionQuery({ ...defaultHistoryFilters, showClosed: false }, 0))
  expect(open.getAll('status')).toEqual(['open'])
  const all = new URLSearchParams(buildPositionQuery({ ...defaultHistoryFilters, botId: 'b1' }, 50))
  expect(all.getAll('status')).toEqual(['open', 'closed'])
  expect(all.get('bot_id')).toBe('b1')
  expect(all.get('offset')).toBe('50')
})

it('loads fills, then older ones after them, and the positions when asked', async () => {
  const pages: FillPage[] = [
    { items: [fill({ fill_id: 'f1' })], totals, next_before: '2026-10-07T12:00:00Z', next_before_id: 'f1' },
    { items: [fill({ fill_id: 'f2' })], totals, next_before: null, next_before_id: null },
  ]
  const positionPage: PositionPage = { total: 1, items: [position()] }
  const urls: string[] = []
  vi.stubGlobal(
    'fetch',
    vi.fn<typeof fetch>(async (input) => {
      const url = String(input)
      urls.push(url)
      if (url.includes('/position-history')) return new Response(JSON.stringify(positionPage), { status: 200 })
      return new Response(JSON.stringify(pages.shift()), { status: 200 })
    }),
  )
  const { result } = renderHook(() => useTradeHistory('ws-1'))

  await act(async () => {
    await new Promise((resolve) => setTimeout(resolve, 20))
  })
  expect(result.current.fills.map((f) => f.fill_id)).toEqual(['f1'])
  expect(result.current.hasMore).toBe(true)
  expect(result.current.totals[0].fills).toBe(2)

  await act(async () => {
    result.current.loadMore()
    await new Promise((resolve) => setTimeout(resolve, 20))
  })
  expect(result.current.fills.map((f) => f.fill_id)).toEqual(['f1', 'f2'])
  expect(result.current.hasMore).toBe(false)
  expect(urls[1]).toContain('before_id=f1')

  act(() => result.current.setTab('positions'))
  await act(async () => {
    await new Promise((resolve) => setTimeout(resolve, 20))
  })
  expect(result.current.positions.map((p) => p.id)).toEqual(['p1'])
  expect(urls.at(-1)).toContain('/position-history')
})

it('reports a failing load as an error and keeps nothing stale', async () => {
  vi.stubGlobal('fetch', vi.fn<typeof fetch>(async () => new Response('{}', { status: 500 })))
  const { result } = renderHook(() => useTradeHistory('ws-1'))

  await act(async () => {
    await new Promise((resolve) => setTimeout(resolve, 20))
  })

  expect(result.current.error).toContain('約定の履歴を取得できませんでした')
  expect(result.current.fills).toEqual([])
})
