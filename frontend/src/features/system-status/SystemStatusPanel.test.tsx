// @vitest-environment jsdom
import '@testing-library/jest-dom/vitest'
import { act, cleanup, render, renderHook, screen } from '@testing-library/react'
import { afterEach, expect, it, vi } from 'vitest'
import SystemStatusPanel from './SystemStatusPanel'
import { formatAge } from './formatAge'
import type { SystemStatus } from './types'
import { useSystemStatus } from './useSystemStatus'

afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
  vi.restoreAllMocks()
})

const healthy = (overrides: Partial<SystemStatus> = {}): SystemStatus => ({
  checked_at: '2026-10-07T13:00:00Z',
  overall: 'ok',
  problems: [],
  trading_worker: {
    status: 'ok',
    active_bots: 2,
    last_heartbeat_age_seconds: 40,
    stalled_bots: [],
    stale_after_seconds: 180,
  },
  market_data_worker: {
    status: 'ok',
    enabled_subscriptions: 7,
    blocked_subscriptions: 0,
    overdue_subscriptions: 0,
    most_overdue_seconds: null,
    overdue_after_seconds: 600,
  },
  notifications: { pending_events: 0, oldest_pending_age_seconds: null, failed_events_last_7_days: 0 },
  halts: {},
  bots: { running: 2, stopped: 1 },
  connections: [
    {
      id: 'c1',
      label: 'Binance Spot Testnet',
      environment: 'testnet',
      status: 'verified',
      verification_outcome: 'authentication_failed',
      last_verified_at: '2026-09-26T07:12:37Z',
    },
  ],
  ...overrides,
})

it('says normal when nothing is wrong', () => {
  render(<SystemStatusPanel status={healthy()} error="" />)

  expect(screen.getByRole('status')).toHaveTextContent('正常')
  expect(screen.getByRole('status')).toHaveTextContent('問題は検知されていません')
  expect(screen.getByText('稼働中のBot: 2件')).toBeInTheDocument()
  expect(screen.getByText('稼働中: 2件')).toBeInTheDocument()
  expect(screen.getByText('認証失敗')).toBeInTheDocument() // the outcome, in words
})

it('leads with the problems the server found, and marks the stalled worker', () => {
  const stalled = healthy({
    overall: 'attention',
    problems: ['トレーディングWorkerが止まっている可能性があります(対象Bot: a, b)'],
    trading_worker: {
      status: 'stalled',
      active_bots: 7,
      last_heartbeat_age_seconds: 2400,
      stalled_bots: ['a', 'b', 'c', 'd', 'e', 'f', 'g'],
      stale_after_seconds: 180,
    },
  })
  render(<SystemStatusPanel status={stalled} error="" />)

  expect(screen.getByRole('status')).toHaveTextContent('要確認')
  expect(screen.getByRole('status')).toHaveTextContent('トレーディングWorkerが止まっている可能性があります')
  expect(screen.getByText('止まっている可能性')).toBeInTheDocument()
  expect(screen.getByText('最後の確認: 40分前')).toBeInTheDocument()
  expect(screen.getByText(/対象: a, b, c, d, e ほか2件/)).toBeInTheDocument()
})

it('does not keep an all-clear on screen when the status cannot be read', () => {
  render(<SystemStatusPanel status={healthy()} error="システム状態APIへ接続できません。" />)

  expect(screen.getByRole('alert')).toHaveTextContent('システム状態APIへ接続できません')
  expect(screen.getByRole('alert')).toHaveTextContent('正常とは言えません')
  expect(screen.queryByText('正常')).not.toBeInTheDocument()
})

it('formats ages in the largest sensible unit', () => {
  expect(formatAge(null)).toBe('記録なし')
  expect(formatAge(90)).toBe('90秒前')
  expect(formatAge(212)).toBe('3分前')
  expect(formatAge(9000)).toBe('2時間前')
  expect(formatAge(212, false)).toBe('3分')
})

it('reports a failing status call as an error and recovers on the next success', async () => {
  let failing = true
  const fetchMock = vi.fn<typeof fetch>(async () =>
    failing ? new Response('{}', { status: 500 }) : new Response(JSON.stringify(healthy()), { status: 200 }),
  )
  vi.stubGlobal('fetch', fetchMock)
  const { result } = renderHook(() => useSystemStatus('ws-1'))

  await act(async () => {
    await result.current.reload()
  })
  expect(result.current.error).toContain('HTTP 500')
  expect(result.current.status).toBeNull()

  failing = false
  await act(async () => {
    await result.current.reload()
  })
  expect(result.current.error).toBe('')
  expect(result.current.status?.overall).toBe('ok')
  expect(String(fetchMock.mock.calls[0][0])).toContain('/api/v1/workspaces/ws-1/system-status')
})
