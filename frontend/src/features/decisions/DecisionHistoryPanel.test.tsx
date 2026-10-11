// @vitest-environment jsdom
import '@testing-library/jest-dom/vitest'
import { act, cleanup, fireEvent, render, renderHook, screen, within } from '@testing-library/react'
import { afterEach, expect, it, vi } from 'vitest'
import DecisionHistoryPanel from './DecisionHistoryPanel'
import { explainDecision } from './explain'
import { haltBlockedAt, haltsAt } from './labels'
import type { DecisionFilters, DecisionHalt, DecisionHistory, DecisionItem } from './types'
import { defaultDecisionFilters } from './types'
import { buildDecisionQuery, useDecisionHistory } from './useDecisionHistory'

afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
})

const item = (overrides: Partial<DecisionItem> = {}): DecisionItem => ({
  signal_id: 's1',
  evaluated_at: '2026-10-08T04:00:08Z',
  bar_open_time: '2026-10-08T00:00:00Z',
  bar_close_time: '2026-10-08T04:00:00Z',
  action: 'hold',
  outcome: 'hold',
  rationale: {
    kind: 'donchian_breakout',
    close: '769.670000000000000000',
    parameters: { entry_period: 110, exit_period: 40 },
  },
  risk_outcome: null,
  risk_reason_code: null,
  failed_checks: [],
  approved_quantity: null,
  order_id: null,
  order_side: null,
  fill_price: null,
  fill_quantity: null,
  ...overrides,
})

const halt = (overrides: Partial<DecisionHalt> = {}): DecisionHalt => ({
  id: 'h1',
  scope_type: 'account',
  level: 'entry_halted',
  reason_code: 'consecutive_loss_limit',
  status: 'active',
  halted_at: '2026-10-08T00:00:00Z',
  released_at: null,
  ...overrides,
})

const denied = item({
  signal_id: 's2',
  action: 'sell',
  outcome: 'denied',
  risk_outcome: 'deny',
  risk_reason_code: 'hard_limit_breach',
  failed_checks: [{ name: 'binance_no_short', detail: {} }],
})

const summary = { evaluations: 10, counts: { hold: 7, ordered: 1, denied: 2 } }

const panel = (props: Partial<Parameters<typeof DecisionHistoryPanel>[0]> = {}) =>
  render(
    <DecisionHistoryPanel
      bots={[{ id: 'b1', name: 'btcusdt-4h-donchian' }]}
      botId="b1"
      filters={defaultDecisionFilters}
      onFiltersChange={() => undefined}
      items={[denied, item()]}
      summary={summary}
      halts={[]}
      hasMore={false}
      loading={false}
      error=""
      loadedAt={null}
      onLoadMore={() => undefined}
      onReload={() => undefined}
      {...props}
    />,
  )

it('explains a hold with the close and the two breakout rules', () => {
  const explanation = explainDecision(item(), [])

  expect(explanation.headline).toBe('シグナルなし(終値 769.67)')
  expect(explanation.reasons[0]).toContain('直前110本の高値を上抜けると')
  expect(explanation.reasons[0]).toContain('直前40本の安値を下抜けると')
})

it('explains a denied entry by the checks that failed, in words', () => {
  const explanation = explainDecision(denied, [])

  expect(explanation.headline).toContain('売りのシグナルが出ましたが、リスク判定で拒否')
  expect(explanation.reasons.join(' ')).toContain('空売りは不可')
  expect(explanation.reasons.join(' ')).toContain('絶対上限(hard limit)を超えた')
})

it('explains a skipped entry by the halt that was in force at the time', () => {
  const halted = item({ outcome: 'halted', action: 'buy', risk_outcome: 'allow' })

  const named = explainDecision(halted, [halt()])
  expect(named.headline).toContain('新規の取引が止まっていたため見送りました')
  expect(named.reasons[0]).toBe('この口座が新規建玉の停止(原因: 連敗の上限)')

  const unknown = explainDecision(halted, [halt({ released_at: '2026-10-08T01:00:00Z' })])
  expect(unknown.reasons[0]).toContain('その時点の取引停止(halt)が新規を止めていました')
})

it('explains an order, a close and a paused skip', () => {
  const ordered = explainDecision(
    item({ outcome: 'ordered', action: 'buy', fill_price: '795.43', fill_quantity: '20.953' }),
    [],
  )
  expect(ordered.headline).toBe('買いを発注し、約定しました(20.953 @ 795.43)')
  expect(explainDecision(item({ outcome: 'closed', action: 'sell' }), []).headline).toContain(
    '反対向きの建玉を決済しました',
  )
  expect(explainDecision(item({ outcome: 'paused', action: 'buy' }), []).headline).toContain(
    'Botが一時停止中のため新規は見送りました',
  )
})

it('knows when a halt was in force, and that a warning stops nothing', () => {
  const at = '2026-10-08T04:00:08Z'

  expect(haltBlockedAt(halt(), at)).toBe(true)
  expect(haltBlockedAt(halt({ released_at: '2026-10-08T04:00:08Z' }), at)).toBe(false) // released at that moment
  expect(haltBlockedAt(halt({ halted_at: '2026-10-08T05:00:00Z' }), at)).toBe(false) // not yet
  expect(haltBlockedAt(halt({ level: 'warning' }), at)).toBe(false)
  expect(haltsAt([halt(), halt({ id: 'h2', level: 'warning' })], item()).map((h) => h.id)).toEqual(['h1'])
})

it('shows the split of the evaluations as a bar and a legend with the counts', () => {
  panel()

  expect(screen.getByRole('img', { name: '判断の内訳' })).toBeInTheDocument()
  const hold = screen.getByText('様子見(hold)', { selector: 'li' }) as HTMLElement
  expect(hold).toHaveTextContent('様子見(hold) 7')
  expect(screen.getByText('評価 10回')).toBeInTheDocument()
})

it('draws one cell per evaluation, oldest first, and a band where entries were stopped', () => {
  const evaluated = [
    item({ signal_id: 'new', evaluated_at: '2026-10-08T08:00:00Z', outcome: 'denied' }),
    item({ signal_id: 'old', evaluated_at: '2026-10-07T20:00:00Z' }),
  ]
  panel({ items: evaluated, halts: [halt({ halted_at: '2026-10-08T00:00:00Z' })] })

  const cells = within(screen.getByRole('group', { name: /判断の時系列/ })).getAllByRole('button')
  expect(cells).toHaveLength(2)
  expect(cells[0].getAttribute('aria-label')).toContain('様子見')
  expect(cells[1].getAttribute('aria-label')).toContain('リスク判定で拒否')
  expect(cells[1]).toHaveClass('outcome-denied')
  const band = document.querySelectorAll('.timeline-halt')
  expect(band[0]).not.toHaveClass('timeline-halt-on') // the old bar: before the halt
  expect(band[1]).toHaveClass('timeline-halt-on') // the new bar: while it was in force
})

it('opens the entry of the cell that was clicked', () => {
  panel({ items: [item({ signal_id: 'a' }), item({ signal_id: 'b' })] })

  fireEvent.click(within(screen.getByRole('group', { name: /判断の時系列/ })).getAllByRole('button')[0])

  expect(document.getElementById('decision-b')).toHaveClass('decision-selected')
  expect(document.getElementById('decision-b')?.querySelector('details')).toHaveAttribute('open')
  expect(document.getElementById('decision-a')).not.toHaveClass('decision-selected')
})

it('shows each decision with its signal, outcome, reason and technical detail', () => {
  panel()

  const card = document.getElementById('decision-s2') as HTMLElement
  expect(within(card).getByText('リスク判定で拒否')).toBeInTheDocument()
  expect(within(card).getByText('シグナル: 売り')).toBeInTheDocument()
  expect(card).toHaveTextContent('売りのシグナルが出ましたが、リスク判定で拒否しました')
  expect(card).toHaveTextContent('空売りは不可')
  expect(card).toHaveTextContent('"kind": "donchian_breakout"')
})

it('filters by outcome, and has a button for only the reasons nothing was traded', () => {
  const onFiltersChange = vi.fn()
  panel({ onFiltersChange })

  fireEvent.click(screen.getByLabelText('リスク判定で拒否'))
  expect(onFiltersChange).toHaveBeenLastCalledWith({ ...defaultDecisionFilters, outcomes: ['denied'] })
  fireEvent.click(screen.getByRole('button', { name: '取引しなかった理由だけ見る' }))
  expect(onFiltersChange).toHaveBeenLastCalledWith({
    ...defaultDecisionFilters,
    outcomes: ['denied', 'halted', 'paused'],
  })
  fireEvent.change(screen.getByLabelText('期間'), { target: { value: '7d' } })
  expect(onFiltersChange).toHaveBeenLastCalledWith({ ...defaultDecisionFilters, period: '7d' })
  fireEvent.change(screen.getByLabelText('Bot'), { target: { value: 'b1' } })
  expect(onFiltersChange).toHaveBeenLastCalledWith({ ...defaultDecisionFilters, botId: 'b1' })
})

it('toggles the not-traded filter off when it is already on, and clears conditions', () => {
  const onFiltersChange = vi.fn()
  const on: DecisionFilters = { ...defaultDecisionFilters, outcomes: ['denied', 'halted', 'paused'] }
  panel({ filters: on, onFiltersChange })

  fireEvent.click(screen.getByRole('button', { name: '取引しなかった理由だけ見る' }))
  expect(onFiltersChange).toHaveBeenLastCalledWith({ ...on, outcomes: [] })
  fireEvent.click(screen.getByRole('button', { name: '条件をクリア' }))
  expect(onFiltersChange).toHaveBeenLastCalledWith({ ...defaultDecisionFilters, botId: 'b1' })
})

it('lists the halts of the period with their scope, cause and span', () => {
  panel({
    halts: [halt(), halt({ id: 'h2', scope_type: 'workspace', reason_code: 'user_emergency_stop', level: 'emergency_stopped', released_at: '2026-10-08T03:00:00Z' })],
  })

  const list = screen.getByRole('heading', { name: '新規を止めた取引停止(halt)' }).nextElementSibling as HTMLElement
  expect(list).toHaveTextContent('連敗の上限')
  expect(list).toHaveTextContent('この口座')
  expect(list).toHaveTextContent('解除されていません(継続中)')
  expect(list).toHaveTextContent('利用者の緊急停止')
  expect(list).toHaveTextContent('ワークスペース全体')
})

it('says so when there is no halt, no decision, no bot, or the load failed', () => {
  const { rerender } = panel({ halts: [], items: [], summary: { evaluations: 0, counts: {} } })
  expect(screen.getByText(/このBotの新規を止めた取引停止\(halt\)はありません/)).toBeInTheDocument()
  expect(screen.getByText(/条件に合う判断はありません/)).toBeInTheDocument()

  rerender(
    <DecisionHistoryPanel
      bots={[]}
      botId=""
      filters={defaultDecisionFilters}
      onFiltersChange={() => undefined}
      items={[]}
      summary={{ evaluations: 0, counts: {} }}
      halts={[]}
      hasMore={false}
      loading={false}
      error=""
      loadedAt={null}
      onLoadMore={() => undefined}
      onReload={() => undefined}
    />,
  )
  expect(screen.getByText(/Botがありません/)).toBeInTheDocument()

  rerender(
    <DecisionHistoryPanel
      bots={[{ id: 'b1', name: 'x' }]}
      botId="b1"
      filters={defaultDecisionFilters}
      onFiltersChange={() => undefined}
      items={[]}
      summary={{ evaluations: 0, counts: {} }}
      halts={[]}
      hasMore={false}
      loading={false}
      error="判断の履歴APIへ接続できません。"
      loadedAt={null}
      onLoadMore={() => undefined}
      onReload={() => undefined}
    />,
  )
  expect(screen.getByRole('alert')).toHaveTextContent('接続できません')
  expect(screen.queryByText(/条件に合う判断はありません/)).not.toBeInTheDocument()
})

it('builds the query from the filters', () => {
  const now = new Date('2026-10-08T12:00:00Z')

  const query = new URLSearchParams(
    buildDecisionQuery({ botId: 'b1', period: '7d', outcomes: ['denied', 'halted'] }, now, {
      before: '2026-10-07T00:00:00Z',
      beforeId: 's9',
    }),
  )

  expect(query.getAll('outcome')).toEqual(['denied', 'halted'])
  expect(query.get('from_time')).toBe('2026-10-01T12:00:00.000Z')
  expect(query.get('before_id')).toBe('s9')
  expect(new URLSearchParams(buildDecisionQuery({ ...defaultDecisionFilters, period: 'all' }, now, null)).has('from_time')).toBe(false)
})

it('loads the first bot by default, then older pages, and keeps the halts it has seen', async () => {
  const page = (id: string, haltId: string, next: boolean): DecisionHistory => ({
    items: [item({ signal_id: id })],
    summary,
    halts: [halt({ id: haltId })],
    next_before: next ? '2026-10-07T00:00:00Z' : null,
    next_before_id: next ? id : null,
  })
  const pages = [page('s1', 'h1', true), page('s0', 'h2', false)]
  const urls: string[] = []
  vi.stubGlobal(
    'fetch',
    vi.fn<typeof fetch>(async (input) => {
      urls.push(String(input))
      return new Response(JSON.stringify(pages.shift()), { status: 200 })
    }),
  )
  const { result } = renderHook(() => useDecisionHistory('ws-1', ['b1', 'b2']))

  await act(async () => {
    await new Promise((resolve) => setTimeout(resolve, 20))
  })
  expect(urls[0]).toContain('/bots/b1/decisions')
  expect(result.current.botId).toBe('b1')
  expect(result.current.items.map((i) => i.signal_id)).toEqual(['s1'])
  expect(result.current.hasMore).toBe(true)

  await act(async () => {
    result.current.loadMore()
    await new Promise((resolve) => setTimeout(resolve, 20))
  })
  expect(result.current.items.map((i) => i.signal_id)).toEqual(['s1', 's0'])
  expect(result.current.halts.map((h) => h.id)).toEqual(['h1', 'h2'])
  expect(urls[1]).toContain('before_id=s1')
})

it('loads another bot when it is chosen, and does nothing without a bot', async () => {
  const fetchMock = vi.fn<typeof fetch>(
    async () =>
      new Response(JSON.stringify({ items: [], summary, halts: [], next_before: null, next_before_id: null }), {
        status: 200,
      }),
  )
  vi.stubGlobal('fetch', fetchMock)
  const { result, rerender } = renderHook(({ ids }) => useDecisionHistory('ws-1', ids), {
    initialProps: { ids: [] as string[] },
  })
  await act(async () => {
    await new Promise((resolve) => setTimeout(resolve, 20))
  })
  expect(fetchMock).not.toHaveBeenCalled()

  rerender({ ids: ['b1', 'b2'] })
  act(() => result.current.setFilters({ ...defaultDecisionFilters, botId: 'b2' }))
  await act(async () => {
    await new Promise((resolve) => setTimeout(resolve, 20))
  })

  expect(String(fetchMock.mock.calls.at(-1)?.[0])).toContain('/bots/b2/decisions')
  expect(result.current.botId).toBe('b2')
})

it('reports a failing load as an error', async () => {
  vi.stubGlobal('fetch', vi.fn<typeof fetch>(async () => new Response('{}', { status: 500 })))
  const { result } = renderHook(() => useDecisionHistory('ws-1', ['b1']))

  await act(async () => {
    await new Promise((resolve) => setTimeout(resolve, 20))
  })

  expect(result.current.error).toContain('判断の履歴を取得できませんでした')
  expect(result.current.items).toEqual([])
})
