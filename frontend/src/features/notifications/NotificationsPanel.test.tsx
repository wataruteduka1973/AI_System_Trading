// @vitest-environment jsdom
import '@testing-library/jest-dom/vitest'
import { act, cleanup, fireEvent, render, renderHook, screen } from '@testing-library/react'
import { afterEach, expect, it, vi } from 'vitest'
import NotificationsPanel from './NotificationsPanel'
import type { AppNotification } from './types'
import { useNotifications } from './useNotifications'

afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
  vi.restoreAllMocks()
})

const notification = (overrides: Partial<AppNotification> = {}): AppNotification => ({
  id: 'n1',
  event_id: 'e1',
  severity: 'critical',
  category: 'risk',
  event_type: 'trading_halt.ledger_mismatch',
  message: '注文と台帳の不整合を検知したため、口座の取引を止めました',
  payload: {},
  occurred_at: '2026-10-07T13:00:00Z',
  status: 'sent',
  acknowledged_at: null,
  ...overrides,
})

it('shows unread notifications with a way to acknowledge them, and read ones as read', () => {
  const onAcknowledge = vi.fn()
  const onAcknowledgeAll = vi.fn()
  render(
    <NotificationsPanel
      items={[notification(), notification({ id: 'n2', severity: 'warning', status: 'acknowledged' })]}
      unacknowledgedCount={1}
      message=""
      onAcknowledge={onAcknowledge}
      onAcknowledgeAll={onAcknowledgeAll}
    />,
  )

  expect(screen.getByText('未確認 1件')).toBeInTheDocument()
  expect(screen.getByText('重大')).toBeInTheDocument()
  expect(screen.getByText('確認済み')).toBeInTheDocument()
  fireEvent.click(screen.getByRole('button', { name: '確認済みにする' }))
  expect(onAcknowledge).toHaveBeenCalledWith('n1')
  fireEvent.click(screen.getByRole('button', { name: 'すべて確認済みにする' }))
  expect(onAcknowledgeAll).toHaveBeenCalled()
})

it('has nothing to acknowledge when there is nothing unread', () => {
  render(
    <NotificationsPanel
      items={[]}
      unacknowledgedCount={0}
      message=""
      onAcknowledge={() => undefined}
      onAcknowledgeAll={() => undefined}
    />,
  )

  expect(screen.getByText('通知はありません。')).toBeInTheDocument()
  expect(screen.getByRole('button', { name: 'すべて確認済みにする' })).toBeDisabled()
})

it('acknowledges through the API and reloads the list', async () => {
  const list = { unacknowledged_count: 1, items: [notification()] }
  const fetchMock = vi.fn<typeof fetch>(async () => new Response(JSON.stringify(list), { status: 200 }))
  vi.stubGlobal('fetch', fetchMock)
  const { result } = renderHook(() => useNotifications('ws-1'))

  await act(async () => {
    await result.current.acknowledge('n1')
    await result.current.acknowledgeAll()
  })

  const posts = fetchMock.mock.calls
    .filter(([, init]) => init?.method === 'POST')
    .map(([url]) => String(url))
  expect(posts).toEqual([
    expect.stringMatching(/\/workspaces\/ws-1\/notifications\/n1\/acknowledge$/),
    expect.stringMatching(/\/workspaces\/ws-1\/notifications\/acknowledge-all$/),
  ])
  expect(result.current.unacknowledged_count).toBe(1)
  expect(result.current.items).toHaveLength(1)
})
