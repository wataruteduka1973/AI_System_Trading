// @vitest-environment jsdom
import '@testing-library/jest-dom/vitest'
import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, expect, it, vi } from 'vitest'
import { MemoryRouter } from 'react-router'
import App from './App'

vi.mock('./components/CandleChart', () => ({
  default: ({ candles }: { candles: { close: string }[] }) =>
    <div data-testid="market-chart">{candles.map((row) => row.close).join(',')}</div>,
}))

afterEach(() => { cleanup(); vi.unstubAllGlobals() })

const authMe = {
  user: { id: 'user-1', email: 'owner@example.com', display_name: 'Test Owner', status: 'active' },
  memberships: [{ workspace_id: 'ws', workspace_name: 'Test', role: 'owner' }],
}

it('ignores a late previous-timeframe response and scopes job requests', async () => {
  let finishOld: (value: Response) => void = () => {}
  const delayed = new Promise<Response>((resolve) => { finishOld = resolve })
  const json = (value: unknown) => Promise.resolve(new Response(JSON.stringify(value)))
  const changes: { instrument_id: string; enabled: boolean; timeframe?: string }[] = []
  const request = vi.fn((input: string, options?: RequestInit) => {
    const url = new URL(input)
    if (url.pathname.endsWith('/auth/me')) return json(authMe)
    if (url.pathname.endsWith('/market-data-subscriptions') && options?.method === 'PUT') {
      changes.push(JSON.parse(String(options?.body)))
      return json({})
    }
    if (url.pathname.endsWith('/health') || url.pathname.endsWith('/health/db')) return json({ status: 'ok' })
    if (url.pathname.endsWith('/instruments')) return json([{
      id: 'btc', exchange_code: 'binance', symbol: 'BTCJPY', quote_asset: 'JPY', price_scale: 0,
      tick_size: '1',
    }])
    if (url.pathname.endsWith('/candles')) {
      if (url.searchParams.get('timeframe') === '1m') return delayed
      return json([{
        open_time: '2026-08-29T00:00:00Z', close: '555', open: '550', high: '560', low: '540',
      }])
    }
    if (url.pathname.endsWith('/candle-coverage')) return json(null)
    return json([])
  })
  vi.stubGlobal('fetch', request)
  render(<MemoryRouter initialEntries={['/workspaces/ws/markets/binance']}><App /></MemoryRouter>)
  await screen.findByLabelText('時間足')
  await waitFor(() => expect(request.mock.calls.some(([url]) => url.includes('/candles?timeframe=1m'))).toBe(true))
  fireEvent.change(screen.getByLabelText('時間足'), { target: { value: '5m' } })
  await waitFor(() => expect(screen.getByTestId('market-chart')).toHaveTextContent('555'))
  await act(async () => { finishOld(new Response(JSON.stringify([{
    open_time: '2026-08-29T00:00:00Z', close: '111',
  }]))) })
  expect(screen.getByTestId('market-chart')).toHaveTextContent('555')
  expect(screen.getByTestId('market-chart')).not.toHaveTextContent('111')
  expect(request.mock.calls.some(([url]) => url.includes('candle-backfills?instrument_id=btc&timeframe=5m'))).toBe(true)
  expect(screen.getByRole('button', { name: 'この銘柄の全時間足を停止' })).toBeInTheDocument()
  fireEvent.click(screen.getByRole('button', { name: 'この銘柄の全時間足を停止' }))
  await waitFor(() => expect(changes).toHaveLength(1))
  expect(changes[0]).toEqual({ instrument_id: 'btc', enabled: false })
  await screen.findByText('この銘柄の全時間足の自動取得を停止しました。実行中の取得・手動の過去取得は別です。')
  fireEvent.click(screen.getByRole('button', { name: 'この銘柄の全時間足を開始' }))
  await waitFor(() => expect(changes).toHaveLength(2))
  expect(changes[1]).toEqual({ instrument_id: 'btc', enabled: true })
  await screen.findByText('この銘柄の全時間足の自動取得を開始しました。')
  expect(screen.queryByRole('button', { name: 'この時間足の自動取得を開始' })).not.toBeInTheDocument()
})

const jsonResponse = (value: unknown) => Promise.resolve(new Response(JSON.stringify(value)))

const baseInstrument = {
  id: 'btc', exchange_code: 'binance', symbol: 'BTCJPY', quote_asset: 'JPY', price_scale: 0,
  tick_size: '1',
}

it('shows a blocked subscription distinctly from enabled/disabled', async () => {
  const request = vi.fn((input: string) => {
    const url = new URL(input)
    if (url.pathname.endsWith('/auth/me')) return jsonResponse(authMe)
    if (url.pathname.endsWith('/health') || url.pathname.endsWith('/health/db')) return jsonResponse({ status: 'ok' })
    if (url.pathname.endsWith('/instruments')) return jsonResponse([baseInstrument])
    if (url.pathname.endsWith('/candles')) return jsonResponse([])
    if (url.pathname.endsWith('/candle-coverage')) return jsonResponse(null)
    if (url.pathname.endsWith('/candle-backfills')) return jsonResponse([])
    if (url.pathname.endsWith('/market-data-subscriptions')) {
      return jsonResponse([{
        id: 'sub1', instrument_id: 'btc', timeframe: '1m', enabled: true, poll_interval_seconds: 60,
        last_polled_at: null, last_success_at: null, last_error_code: 'communication_failed',
        next_run_at: '2026-09-16T03:00:00Z', consecutive_failures: 3,
        blocked_reason: 'communication_failed',
      }])
    }
    return jsonResponse([])
  })
  vi.stubGlobal('fetch', request)
  const { container } = render(<MemoryRouter initialEntries={['/workspaces/ws/markets/binance']}><App /></MemoryRouter>)
  await screen.findByLabelText('時間足')
  await screen.findByText('自動取得停止中（要確認）')
  expect(container.querySelector('.collection-status.blocked')).not.toBeNull()
  expect(container.textContent).toContain('連続失敗3回')
  expect(container.textContent).toContain('取引所との通信に失敗しました。自動的に再試行します。')
})

it('shows the next retry time for a queued backfill with prior failures', async () => {
  const request = vi.fn((input: string) => {
    const url = new URL(input)
    if (url.pathname.endsWith('/auth/me')) return jsonResponse(authMe)
    if (url.pathname.endsWith('/health') || url.pathname.endsWith('/health/db')) return jsonResponse({ status: 'ok' })
    if (url.pathname.endsWith('/instruments')) return jsonResponse([baseInstrument])
    if (url.pathname.endsWith('/candles')) return jsonResponse([])
    if (url.pathname.endsWith('/candle-coverage')) return jsonResponse(null)
    if (url.pathname.endsWith('/market-data-subscriptions')) return jsonResponse([])
    if (url.pathname.endsWith('/candle-backfills')) {
      return jsonResponse([{
        id: 'job1', instrument_id: 'btc', timeframe: '1m', status: 'queued', rows_written: 0,
        error_code: null, created_at: '2026-09-15T00:00:00Z',
        next_run_at: '2026-09-16T03:02:00Z', consecutive_failures: 2,
      }])
    }
    return jsonResponse([])
  })
  vi.stubGlobal('fetch', request)
  const { container } = render(<MemoryRouter initialEntries={['/workspaces/ws/markets/binance']}><App /></MemoryRouter>)
  await screen.findByLabelText('時間足')
  await screen.findByText('過去取得 (1m): 待機中')
  expect(container.querySelector('.backfill-status.queued')).not.toBeNull()
  expect(container.textContent).toContain('次回再試行予定')
  expect(container.textContent).toContain('連続失敗2回')
})
