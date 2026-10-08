// @vitest-environment jsdom
import '@testing-library/jest-dom/vitest'
import { act, cleanup, fireEvent, render, renderHook, screen } from '@testing-library/react'
import { afterEach, expect, it, vi } from 'vitest'
import HaltCenter from './HaltCenter'
import { releasePath, scopeLabel } from './haltLabels'
import type { TradingHalt } from './types'
import { useTrading } from './useTrading'

afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
  vi.restoreAllMocks()
})

const halt = (overrides: Partial<TradingHalt> = {}): TradingHalt => ({
  id: 'h1',
  scope_type: 'account',
  scope_id: 'a1',
  level: 'emergency_stopped',
  reason_code: 'ledger_mismatch',
  status: 'active',
  halted_at: '2026-10-07T13:00:00Z',
  released_at: null,
  ...overrides,
})

const names = { botNames: { b1: 'btc-4h' }, accountNames: { a1: 'USDT口座 (ac11ea0b)' } }

it('says plainly when nothing is halted', () => {
  render(<HaltCenter halts={[]} isOwner message="" onRelease={() => undefined} {...names} />)

  expect(screen.getByText(/止めている停止\(halt\)はありません/)).toBeInTheDocument()
})

it('explains each halt: the cause, the scope, what still works and how it ends', () => {
  render(
    <HaltCenter
      halts={[
        halt(),
        halt({ id: 'h2', scope_type: 'bot', scope_id: 'b1', level: 'entry_halted', reason_code: 'consecutive_loss_limit' }),
      ]}
      isOwner
      message=""
      onRelease={() => undefined}
      {...names}
    />,
  )

  expect(screen.getByText('注文・台帳の不整合')).toBeInTheDocument()
  expect(screen.getByText(/口座: USDT口座 \(ac11ea0b\)/)).toBeInTheDocument()
  expect(screen.getByText(/不整合が残っていると解除できません/)).toBeInTheDocument()
  expect(screen.getByText('連敗の上限')).toBeInTheDocument()
  expect(screen.getByText(/Bot: btc-4h/)).toBeInTheDocument()
  expect(screen.getByText(/新規・増し玉は止まります。決済はできます/)).toBeInTheDocument()
  expect(screen.getByText(/連敗を数え直します/)).toBeInTheDocument()
})

it('offers the release to an Owner only', () => {
  const onRelease = vi.fn()
  const { rerender } = render(
    <HaltCenter halts={[halt()]} isOwner message="" onRelease={onRelease} {...names} />,
  )
  fireEvent.click(screen.getByRole('button', { name: '緊急停止を解除' }))
  expect(onRelease).toHaveBeenCalledWith(halt())

  rerender(<HaltCenter halts={[halt()]} isOwner={false} message="" onRelease={onRelease} {...names} />)
  expect(screen.queryByRole('button', { name: /解除/ })).not.toBeInTheDocument()
  expect(screen.getByText('解除はOwnerのみ可能です。')).toBeInTheDocument()
})

it('shows the message of the last action, such as a refused release', () => {
  render(
    <HaltCenter
      halts={[halt()]}
      isOwner
      message="停止を解除できませんでした: 台帳の不整合が解消されていないため解除できません(HTTP 409)"
      onRelease={() => undefined}
      {...names}
    />,
  )

  expect(screen.getByText(/台帳の不整合が解消されていないため解除できません\(HTTP 409\)/)).toBeInTheDocument()
})

it('routes a release to the endpoint for its level', () => {
  expect(releasePath(halt())).toBe('emergency-release')
  expect(releasePath(halt({ level: 'entry_halted' }))).toBe('release')
  expect(scopeLabel(halt({ scope_type: 'workspace', scope_id: null }), { bots: {}, accounts: {} })).toBe(
    'ワークスペース全体',
  )
  expect(scopeLabel(halt(), { bots: {}, accounts: {} })).toBe('口座: a1')
})

it('releases an emergency halt and a lock halt through their own endpoints', async () => {
  const fetchMock = vi.fn<typeof fetch>(async () => new Response('[]', { status: 200 }))
  vi.stubGlobal('fetch', fetchMock)
  const { result } = renderHook(() => useTrading('ws-1'))

  await act(async () => {
    await result.current.releaseHalt(halt())
    await result.current.releaseHalt(halt({ id: 'h2', level: 'entry_halted', reason_code: 'peak_drawdown_limit' }))
  })

  const posts = fetchMock.mock.calls
    .filter(([, init]) => init?.method === 'POST')
    .map(([url]) => String(url))
  expect(posts).toEqual([
    expect.stringMatching(/\/workspaces\/ws-1\/trading-halts\/h1\/emergency-release$/),
    expect.stringMatching(/\/workspaces\/ws-1\/trading-halts\/h2\/release$/),
  ])
})

it('shows why a refused release was refused', async () => {
  const detail = '台帳の不整合が解消されていないため解除できません(建玉の数量が合いません)'
  const fetchMock = vi.fn<typeof fetch>(async (_input, init) =>
    init?.method === 'POST'
      ? new Response(JSON.stringify({ detail }), { status: 409 })
      : new Response('[]', { status: 200 }),
  )
  vi.stubGlobal('fetch', fetchMock)
  const { result } = renderHook(() => useTrading('ws-1'))

  await act(async () => {
    await result.current.releaseHalt(halt())
  })

  expect(result.current.tradingMessage).toContain(detail)
})
