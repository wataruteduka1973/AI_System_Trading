// @vitest-environment jsdom
import '@testing-library/jest-dom/vitest'
import { act, cleanup, fireEvent, render, renderHook, screen, within } from '@testing-library/react'
import { afterEach, expect, it, vi } from 'vitest'
import EventLogPanel from './EventLogPanel'
import { eventSubject } from './eventSubject'
import type { EventFacets, EventFilters, EventPage, SystemEvent } from './types'
import { emptyFilters } from './types'
import { buildEventQuery, useEventLog } from './useEventLog'

afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
  vi.useRealTimers()
})

const event = (overrides: Partial<SystemEvent> = {}): SystemEvent => ({
  id: 'e1',
  occurred_at: '2026-10-08T03:00:00Z',
  severity: 'critical',
  category: 'risk',
  event_type: 'trading_halt.ledger_mismatch',
  reason_code: 'ledger_mismatch',
  source_type: 'reconciliation',
  source_id: null,
  target_type: 'account',
  target_id: 'ac11ea0b-0000-0000-0000-000000000000',
  correlation_id: 'c0ffee00-0000-0000-0000-000000000001',
  message: '注文と台帳の不整合を検知したため、口座の取引を止めました',
  payload: { finding_count: 1 },
  ...overrides,
})

const facets: EventFacets = {
  severities: ['critical', 'error'],
  categories: ['risk', 'market_data'],
  event_types: ['trading_halt.ledger_mismatch'],
  reason_codes: ['ledger_mismatch', 'worker_stalled'],
}

const panel = (props: Partial<Parameters<typeof EventLogPanel>[0]> = {}) =>
  render(
    <EventLogPanel
      filters={emptyFilters}
      onFiltersChange={() => undefined}
      events={[event()]}
      facets={facets}
      bots={[{ id: 'b1', name: 'btcusdt-4h-donchian' }]}
      hasMore={false}
      loading={false}
      error=""
      searchedAt={null}
      onLoadMore={() => undefined}
      onReload={() => undefined}
      {...props}
    />,
  )

it('shows each event with its severity, category, time, cause and message', () => {
  panel()

  const item = screen.getByText(/注文と台帳の不整合を検知した/).closest('li') as HTMLElement
  expect(within(item).getByText('重大')).toBeInTheDocument()
  expect(within(item).getByText('リスク・取引停止')).toBeInTheDocument()
  expect(item).toHaveTextContent('原因: 注文・台帳の不整合')
  expect(item).toHaveTextContent('account: ac11ea0b')
  expect(screen.getByText('1件')).toBeInTheDocument()
})

it('keeps the technical details and the payload one click away', () => {
  panel()

  const details = screen.getByText('詳細').closest('details') as HTMLElement
  expect(within(details).getByText('trading_halt.ledger_mismatch')).toBeInTheDocument()
  expect(within(details).getByText('reconciliation')).toBeInTheDocument()
  expect(within(details).getByText('c0ffee00-0000-0000-0000-000000000001')).toBeInTheDocument()
  expect(details).toHaveTextContent('"finding_count": 1')
})

it('names the bot an event is about, from the target or the payload', () => {
  const names = { b1: 'btcusdt-4h-donchian' }

  expect(eventSubject(event({ target_type: 'bot', target_id: 'b1' }), names)).toBe('Bot: btcusdt-4h-donchian')
  expect(eventSubject(event({ target_type: 'bot', target_id: 'b9999999-0' }), names)).toBe('Bot: b9999999')
  expect(eventSubject(event({ payload: { bot_name: 'eth-4h' } }), names)).toBe('Bot: eth-4h')
  expect(eventSubject(event({ payload: { bot_names: ['a', 'b', 'c', 'd', 'e'] } }), names)).toBe(
    'Bot: a, b, c ほか2件',
  )
  expect(eventSubject(event({ target_type: null, target_id: null, payload: {} }), names)).toBeNull()
})

it('changes the filters from the controls and offers the choices that exist', () => {
  const onFiltersChange = vi.fn()
  panel({ onFiltersChange })

  fireEvent.change(screen.getByLabelText('カテゴリ'), { target: { value: 'risk' } })
  expect(onFiltersChange).toHaveBeenLastCalledWith({ ...emptyFilters, category: 'risk' })
  fireEvent.change(screen.getByLabelText('原因'), { target: { value: 'worker_stalled' } })
  expect(onFiltersChange).toHaveBeenLastCalledWith({ ...emptyFilters, reasonCode: 'worker_stalled' })
  fireEvent.change(screen.getByLabelText('Bot'), { target: { value: 'b1' } })
  expect(onFiltersChange).toHaveBeenLastCalledWith({ ...emptyFilters, botId: 'b1' })
  fireEvent.change(screen.getByLabelText('期間'), { target: { value: '1h' } })
  expect(onFiltersChange).toHaveBeenLastCalledWith({ ...emptyFilters, period: '1h' })
  fireEvent.change(screen.getByLabelText('メッセージ内の語'), { target: { value: '台帳' } })
  expect(onFiltersChange).toHaveBeenLastCalledWith({ ...emptyFilters, text: '台帳' })
  fireEvent.click(screen.getByLabelText('重大'))
  expect(onFiltersChange).toHaveBeenLastCalledWith({ ...emptyFilters, severities: ['critical'] })
  expect(within(screen.getByLabelText('原因')).getByText('Workerの停止')).toBeInTheDocument()
  expect(within(screen.getByLabelText('カテゴリ')).getByText('市場データ')).toBeInTheDocument()
})

it('narrows to one correlation id from an event and lets it be released', () => {
  const onFiltersChange = vi.fn()
  const { rerender } = panel({ onFiltersChange })

  fireEvent.click(screen.getByRole('button', { name: '同じ一連のイベントだけ表示' }))
  expect(onFiltersChange).toHaveBeenLastCalledWith({
    ...emptyFilters,
    correlationId: 'c0ffee00-0000-0000-0000-000000000001',
  })

  const traced: EventFilters = { ...emptyFilters, correlationId: 'c0ffee00-0000-0000-0000-000000000001' }
  rerender(
    <EventLogPanel
      filters={traced}
      onFiltersChange={onFiltersChange}
      events={[event()]}
      facets={facets}
      bots={[]}
      hasMore={false}
      loading={false}
      error=""
      searchedAt={null}
      onLoadMore={() => undefined}
      onReload={() => undefined}
    />,
  )
  expect(screen.getByRole('status')).toHaveTextContent('の一連のイベントだけを表示しています')
  fireEvent.click(screen.getByRole('button', { name: '解除' }))
  expect(onFiltersChange).toHaveBeenLastCalledWith(emptyFilters)
})

it('offers to clear the conditions only when some are set', () => {
  const onFiltersChange = vi.fn()
  const { rerender } = panel({ onFiltersChange })
  expect(screen.queryByRole('button', { name: '条件をクリア' })).not.toBeInTheDocument()

  rerender(
    <EventLogPanel
      filters={{ ...emptyFilters, category: 'risk', period: '1h' }}
      onFiltersChange={onFiltersChange}
      events={[]}
      facets={facets}
      bots={[]}
      hasMore={false}
      loading={false}
      error=""
      searchedAt={null}
      onLoadMore={() => undefined}
      onReload={() => undefined}
    />,
  )
  fireEvent.click(screen.getByRole('button', { name: '条件をクリア' }))
  expect(onFiltersChange).toHaveBeenLastCalledWith({ ...emptyFilters, period: '1h' }) // the period stays
})

it('says when nothing matches, shows an error instead of an empty list, and pages on request', () => {
  const onLoadMore = vi.fn()
  const { rerender } = panel({ events: [] })
  expect(screen.getByText('条件に合うイベントはありません。')).toBeInTheDocument()

  rerender(
    <EventLogPanel
      filters={emptyFilters}
      onFiltersChange={() => undefined}
      events={[]}
      facets={facets}
      bots={[]}
      hasMore={false}
      loading={false}
      error="イベントログAPIへ接続できません。"
      searchedAt={null}
      onLoadMore={() => undefined}
      onReload={() => undefined}
    />,
  )
  expect(screen.getByRole('alert')).toHaveTextContent('接続できません')
  expect(screen.queryByText('条件に合うイベントはありません。')).not.toBeInTheDocument()

  rerender(
    <EventLogPanel
      filters={emptyFilters}
      onFiltersChange={() => undefined}
      events={[event()]}
      facets={facets}
      bots={[]}
      hasMore
      loading={false}
      error=""
      searchedAt={null}
      onLoadMore={onLoadMore}
      onReload={() => undefined}
    />,
  )
  expect(screen.getByText('1件以上')).toBeInTheDocument()
  fireEvent.click(screen.getByRole('button', { name: 'さらに古いイベントを読み込む' }))
  expect(onLoadMore).toHaveBeenCalled()
})

it('builds the query from the filters, with the period resolved against one instant', () => {
  const now = new Date('2026-10-08T12:00:00Z')

  const query = new URLSearchParams(
    buildEventQuery(
      {
        ...emptyFilters,
        severities: ['error', 'critical'],
        category: 'risk',
        reasonCode: 'ledger_mismatch',
        botId: 'b1',
        correlationId: 'c1',
        text: ' 台帳 ',
        period: '24h',
      },
      now,
      { before: '2026-10-07T00:00:00Z', beforeId: 'e9' },
    ),
  )

  expect(query.getAll('severity')).toEqual(['error', 'critical'])
  expect(query.get('category')).toBe('risk')
  expect(query.get('reason_code')).toBe('ledger_mismatch')
  expect(query.get('bot_id')).toBe('b1')
  expect(query.get('correlation_id')).toBe('c1')
  expect(query.get('q')).toBe('台帳')
  expect(query.get('from_time')).toBe('2026-10-07T12:00:00.000Z')
  expect(query.get('before')).toBe('2026-10-07T00:00:00Z')
  expect(query.get('before_id')).toBe('e9')
  const all = new URLSearchParams(buildEventQuery({ ...emptyFilters, period: 'all' }, now, null))
  expect(all.has('from_time')).toBe(false)
  expect(all.has('before')).toBe(false)
})

it('loads the first page, then older pages after it, and searches again when a filter changes', async () => {
  const page = (id: string, next: boolean): EventPage => ({
    items: [event({ id })],
    next_before: next ? '2026-10-08T02:00:00Z' : null,
    next_before_id: next ? id : null,
  })
  const pages = [page('p1', true), page('p2', false), page('p3', false)]
  const urls: string[] = []
  vi.stubGlobal(
    'fetch',
    vi.fn<typeof fetch>(async (input) => {
      const url = String(input)
      if (url.includes('/events/facets')) return new Response(JSON.stringify(facets), { status: 200 })
      urls.push(url)
      return new Response(JSON.stringify(pages.shift()), { status: 200 })
    }),
  )
  const { result } = renderHook(() => useEventLog('ws-1'))

  await act(async () => {
    await new Promise((resolve) => setTimeout(resolve, 20))
  })
  expect(result.current.events.map((e) => e.id)).toEqual(['p1'])
  expect(result.current.hasMore).toBe(true)
  expect(result.current.facets?.categories).toContain('risk')

  await act(async () => {
    result.current.loadMore()
    await new Promise((resolve) => setTimeout(resolve, 20))
  })
  expect(result.current.events.map((e) => e.id)).toEqual(['p1', 'p2'])
  expect(result.current.hasMore).toBe(false)
  expect(urls[1]).toContain('before_id=p1')

  act(() => result.current.setFilters({ ...emptyFilters, category: 'risk' }))
  await act(async () => {
    await new Promise((resolve) => setTimeout(resolve, 20))
  })
  expect(result.current.events.map((e) => e.id)).toEqual(['p3']) // replaced, not appended
  expect(urls[2]).toContain('category=risk')
  expect(urls[2]).not.toContain('before')
})

it('reports a failing search as an error and keeps nothing stale', async () => {
  vi.stubGlobal(
    'fetch',
    vi.fn<typeof fetch>(async (input) =>
      String(input).includes('/facets')
        ? new Response('{}', { status: 200 })
        : new Response(JSON.stringify({ detail: 'boom' }), { status: 500 }),
    ),
  )
  const { result } = renderHook(() => useEventLog('ws-1'))

  await act(async () => {
    await new Promise((resolve) => setTimeout(resolve, 20))
  })

  expect(result.current.error).toContain('イベントを取得できませんでした')
  expect(result.current.events).toEqual([])
})
