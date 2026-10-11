// @vitest-environment jsdom
import '@testing-library/jest-dom/vitest'
import { act, cleanup, fireEvent, render, renderHook, screen, within } from '@testing-library/react'
import { afterEach, expect, it, vi } from 'vitest'
import DataGapsPanel from './DataGapsPanel'
import { describeJob, formatSpan } from './gapText'
import type { GapFilters, GapJob, GapPage, GapSummary, MarketDataGap } from './types'
import { emptyGapFilters } from './types'
import { buildGapQuery, useDataGaps } from './useDataGaps'

afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
  vi.useRealTimers()
})

const job = (overrides: Partial<GapJob> = {}): GapJob => ({
  id: 'j1',
  status: 'succeeded',
  attempts: 1,
  rows_written: 0,
  validation_result: { source_rows_received: 0, rows_inserted: 0 },
  error_code: null,
  started_at: null,
  finished_at: null,
  created_at: '2026-10-08T01:00:00Z',
  next_run_at: '2026-10-08T01:00:00Z',
  consecutive_failures: 0,
  ...overrides,
})

const gap = (overrides: Partial<MarketDataGap> = {}): MarketDataGap => ({
  id: 'g1',
  instrument_id: 'i1',
  symbol: 'BTCJPY',
  exchange_code: 'binance',
  timeframe: '1h',
  from_time: '2026-09-07T14:00:00Z',
  to_time: '2026-09-09T06:00:00Z',
  expected_count: 40,
  missing_count: 40,
  reason_code: 'internal_missing_candles',
  status: 'open',
  detected_at: '2026-10-03T09:00:00Z',
  resolved_at: null,
  can_backfill: true,
  latest_job: null,
  affected_bots: [],
  ...overrides,
})

const summary: GapSummary[] = [
  {
    instrument_id: 'i1',
    symbol: 'BTCJPY',
    exchange_code: 'binance',
    timeframe: '1h',
    open_count: 1,
    open_missing: 40,
    ignored_count: 0,
    resolved_count: 2,
  },
  {
    instrument_id: 'i2',
    symbol: 'BTCUSDT',
    exchange_code: 'binance_public',
    timeframe: '5m',
    open_count: 0,
    open_missing: 0,
    ignored_count: 34,
    resolved_count: 0,
  },
]

const panel = (props: Partial<Parameters<typeof DataGapsPanel>[0]> = {}) =>
  render(
    <DataGapsPanel
      summary={summary}
      gaps={[gap()]}
      total={1}
      hasMore={false}
      filters={emptyGapFilters}
      onFiltersChange={() => undefined}
      canFill
      filling={null}
      message=""
      error=""
      loading={false}
      onFill={() => undefined}
      onLoadMore={() => undefined}
      onReload={() => undefined}
      {...props}
    />,
  )

it('leads with whether anything needs attention, and how much is missing', () => {
  panel()

  const verdict = screen.getByRole('status')
  expect(verdict).toHaveTextContent('要対応の欠損があります')
  expect(verdict).toHaveTextContent('1件、欠けている足は合計40本')
})

it('says all is well when nothing is open, and mentions what is ignored', () => {
  panel({ summary: [summary[1]], gaps: [], total: 0 })

  expect(screen.getByRole('status')).toHaveTextContent('要対応の欠損はありません')
  expect(screen.getByRole('status')).toHaveTextContent('無視されている欠損が34件')
  expect(screen.getByText('条件に合う欠損はありません。')).toBeInTheDocument()
})

it('totals each series in a table', () => {
  panel()

  const row = screen.getAllByRole('row').find((r) => r.textContent?.startsWith('BTCUSDT')) as HTMLElement
  expect(within(row).getByText('公開履歴')).toBeInTheDocument()
  expect(within(row).getByText('34')).toBeInTheDocument()
  const open = screen.getAllByRole('row').find((r) => r.textContent?.startsWith('BTCJPY')) as HTMLElement
  expect(within(open).getByText('40')).toHaveClass('pnl-negative')
})

it('shows a gap with its range, size, status and the bots it touches', () => {
  panel({ gaps: [gap({ affected_bots: ['btc-4h', 'btc-1h'] })] })

  const card = screen.getByText('BTCJPY', { selector: 'strong' }).closest('li') as HTMLElement
  expect(within(card).getByText('要対応')).toBeInTheDocument()
  expect(card).toHaveTextContent('約40時間')
  expect(card).toHaveTextContent('欠けている足: 40本')
  expect(card).toHaveTextContent('影響するBot(この銘柄で稼働中): btc-4h, btc-1h')
})

it('says so when no bot is affected, and for a public history that none can be', () => {
  const { rerender } = panel()
  expect(screen.getByText('この銘柄で稼働中のBotはありません。')).toBeInTheDocument()

  rerender(
    <DataGapsPanel
      summary={summary}
      gaps={[gap({ exchange_code: 'binance_public', can_backfill: false })]}
      total={1}
      hasMore={false}
      filters={emptyGapFilters}
      onFiltersChange={() => undefined}
      canFill
      filling={null}
      message=""
      error=""
      loading={false}
      onFill={() => undefined}
      onLoadMore={() => undefined}
      onReload={() => undefined}
    />,
  )
  expect(screen.getByText(/稼働中のBotには影響しません/)).toBeInTheDocument()
  expect(screen.getByText(/この画面からは補完できません/)).toBeInTheDocument()
  expect(screen.queryByRole('button', { name: /補完/ })).not.toBeInTheDocument()
})

it('offers to fill a gap that can be filled, and a retry when a job already failed', () => {
  const onFill = vi.fn()
  const first = gap()
  const { rerender } = panel({ onFill, gaps: [first] })

  fireEvent.click(screen.getByRole('button', { name: 'この欠損を補完' }))
  expect(onFill).toHaveBeenCalledWith(first)

  rerender(
    <DataGapsPanel
      summary={summary}
      gaps={[gap({ latest_job: job({ status: 'failed', error_code: 'configuration_error' }) })]}
      total={1}
      hasMore={false}
      filters={emptyGapFilters}
      onFiltersChange={() => undefined}
      canFill
      filling={null}
      message=""
      error=""
      loading={false}
      onFill={onFill}
      onLoadMore={() => undefined}
      onReload={() => undefined}
    />,
  )
  expect(screen.getByText(/接続設定を確認してください/)).toBeInTheDocument()
  expect(screen.getByRole('button', { name: '補完を再試行' })).toBeInTheDocument()
})

it('does not offer the fill to someone who cannot run it, nor while one is in flight', () => {
  const { rerender } = panel({ canFill: false })
  expect(screen.queryByRole('button', { name: 'この欠損を補完' })).not.toBeInTheDocument()
  expect(screen.getByText('補完はOperator以上の権限で実行できます。')).toBeInTheDocument()

  rerender(
    <DataGapsPanel
      summary={summary}
      gaps={[gap({ can_backfill: false, latest_job: job({ status: 'running' }) })]}
      total={1}
      hasMore={false}
      filters={emptyGapFilters}
      onFiltersChange={() => undefined}
      canFill
      filling={null}
      message=""
      error=""
      loading={false}
      onFill={() => undefined}
      onLoadMore={() => undefined}
      onReload={() => undefined}
    />,
  )
  expect(screen.getByText('補完: 取得中')).toBeInTheDocument()
  expect(screen.queryByRole('button', { name: /補完を|この欠損/ })).not.toBeInTheDocument()
})

it('reports the re-validation: filled, or still open because the exchange has no such candles', () => {
  const done = job({ validation_result: { source_rows_received: 120, rows_inserted: 40 } })

  expect(describeJob(done, 'resolved').detail.join(' ')).toContain('取引所から120本を受信し、40本を追加')
  expect(describeJob(done, 'resolved').detail.join(' ')).toContain('この欠損は埋まりました')
  const still = describeJob(job(), 'open').detail.join(' ')
  expect(still).toContain('まだ欠損が残っています')
  expect(still).toContain('Binance Testnet は定期的にリセット')
  const waiting = describeJob(job({ status: 'queued', consecutive_failures: 2 }), 'open')
  expect(waiting.detail.join(' ')).toContain('連続失敗2回')
})

it('shows ignored gaps as deliberately ignored', () => {
  panel({ gaps: [gap({ status: 'ignored', can_backfill: false })] })

  expect(screen.getByText('無視(元データに無い)')).toBeInTheDocument()
  expect(screen.getByText(/意図的に無視されています/)).toBeInTheDocument()
})

it('changes the filters, clears them, and pages on request', () => {
  const onFiltersChange = vi.fn()
  const onLoadMore = vi.fn()
  const { rerender } = panel({ onFiltersChange, onLoadMore, hasMore: true, total: 120 })

  fireEvent.change(screen.getByLabelText('時間足'), { target: { value: '4h' } })
  expect(onFiltersChange).toHaveBeenLastCalledWith({ ...emptyGapFilters, timeframe: '4h' })
  fireEvent.change(screen.getByLabelText('銘柄'), { target: { value: 'i1' } })
  expect(onFiltersChange).toHaveBeenLastCalledWith({ ...emptyGapFilters, instrumentId: 'i1' })
  fireEvent.click(screen.getByLabelText('無視した欠損も表示'))
  expect(onFiltersChange).toHaveBeenLastCalledWith({ ...emptyGapFilters, showIgnored: true })
  fireEvent.click(screen.getByLabelText('解消済みも表示'))
  expect(onFiltersChange).toHaveBeenLastCalledWith({ ...emptyGapFilters, showResolved: true })
  expect(screen.getByText('1 / 120件を表示')).toBeInTheDocument()
  fireEvent.click(screen.getByRole('button', { name: 'さらに読み込む' }))
  expect(onLoadMore).toHaveBeenCalled()

  const narrowed: GapFilters = { ...emptyGapFilters, timeframe: '4h' }
  rerender(
    <DataGapsPanel
      summary={summary}
      gaps={[gap()]}
      total={1}
      hasMore={false}
      filters={narrowed}
      onFiltersChange={onFiltersChange}
      canFill
      filling={null}
      message=""
      error=""
      loading={false}
      onFill={() => undefined}
      onLoadMore={() => undefined}
      onReload={() => undefined}
    />,
  )
  fireEvent.click(screen.getByRole('button', { name: '条件をクリア' }))
  expect(onFiltersChange).toHaveBeenLastCalledWith(emptyGapFilters)
})

it('shows an error instead of an all-clear when the report cannot be read', () => {
  panel({ summary: [], gaps: [], total: 0, error: '欠損データAPIへ接続できません。' })

  expect(screen.getByRole('alert')).toHaveTextContent('接続できません')
  expect(screen.queryByText('要対応の欠損はありません')).not.toBeInTheDocument()
  expect(screen.queryByText('条件に合う欠損はありません。')).not.toBeInTheDocument()
})

it('formats how long a missing stretch is', () => {
  expect(formatSpan('2026-01-01T00:00:00Z', '2026-01-01T00:25:00Z')).toBe('25分')
  expect(formatSpan('2026-01-01T00:00:00Z', '2026-01-01T15:00:00Z')).toBe('15時間')
  expect(formatSpan('2026-01-01T00:00:00Z', '2026-01-02T15:00:00Z')).toBe('39時間')
  expect(formatSpan('2026-01-01T00:00:00Z', '2026-01-04T03:00:00Z')).toBe('3日と3時間')
})

it('asks for the open gaps, and the ignored and resolved ones only on request', () => {
  const plain = new URLSearchParams(buildGapQuery(emptyGapFilters, 0))
  expect(plain.getAll('status')).toEqual(['open'])
  const all = new URLSearchParams(
    buildGapQuery({ showIgnored: true, showResolved: true, instrumentId: 'i1', timeframe: '4h' }, 100),
  )
  expect(all.getAll('status')).toEqual(['open', 'ignored', 'resolved'])
  expect(all.get('instrument_id')).toBe('i1')
  expect(all.get('timeframe')).toBe('4h')
  expect(all.get('offset')).toBe('100')
})

it('loads the gaps and the summary, and queues a fill then reloads', async () => {
  const page: GapPage = { total: 1, items: [gap()] }
  const calls: { url: string; method: string }[] = []
  vi.stubGlobal(
    'fetch',
    vi.fn<typeof fetch>(async (input, init) => {
      const url = String(input)
      calls.push({ url, method: init?.method ?? 'GET' })
      if (url.endsWith('/summary')) return new Response(JSON.stringify(summary), { status: 200 })
      if (url.includes('/backfill')) return new Response(JSON.stringify(job({ status: 'queued' })), { status: 202 })
      return new Response(JSON.stringify(page), { status: 200 })
    }),
  )
  const { result } = renderHook(() => useDataGaps('ws-1'))

  await act(async () => {
    await new Promise((resolve) => setTimeout(resolve, 20))
  })
  expect(result.current.gaps).toHaveLength(1)
  expect(result.current.summary).toHaveLength(2)

  await act(async () => {
    await result.current.fill(result.current.gaps[0])
  })

  expect(calls.some((c) => c.method === 'POST' && c.url.endsWith('/market-data-gaps/g1/backfill'))).toBe(true)
  expect(result.current.message).toContain('BTCJPY 1h の欠損の補完を登録しました')
  expect(calls.filter((c) => c.method === 'GET' && !c.url.endsWith('/summary')).length).toBeGreaterThanOrEqual(2)
})

it('shows why a fill was refused', async () => {
  vi.stubGlobal(
    'fetch',
    vi.fn<typeof fetch>(async (input, init) => {
      if (init?.method === 'POST') {
        return new Response(JSON.stringify({ detail: 'An overlapping backfill is already queued or running' }), {
          status: 409,
        })
      }
      return new Response(String(input).endsWith('/summary') ? '[]' : JSON.stringify({ total: 0, items: [] }), {
        status: 200,
      })
    }),
  )
  const { result } = renderHook(() => useDataGaps('ws-1'))

  await act(async () => {
    await result.current.fill(gap())
  })

  expect(result.current.message).toContain('補完を登録できませんでした')
  expect(result.current.message).toContain('overlapping backfill')
})

it('reports a failing load as an error', async () => {
  vi.stubGlobal('fetch', vi.fn<typeof fetch>(async () => new Response('{}', { status: 500 })))
  const { result } = renderHook(() => useDataGaps('ws-1'))

  await act(async () => {
    await new Promise((resolve) => setTimeout(resolve, 20))
  })

  expect(result.current.error).toContain('欠損データを取得できませんでした')
  expect(result.current.gaps).toEqual([])
})
