import { expect, it } from 'vitest'
import { findStaleBots } from './botStaleness'
import type { BotRunSummary, TradingBot } from './types'

const bot = (overrides: Partial<TradingBot> = {}): TradingBot => ({
  id: 'b1',
  workspace_id: 'ws',
  name: 'btcusdt-4h-donchian',
  execution_mode: 'paper',
  strategy_mode: 'technical',
  account_id: 'a1',
  instrument_id: 'i1',
  timeframe: '4h',
  desired_state: 'running',
  actual_state: 'running',
  live_trading_enabled: false,
  version: 1,
  created_at: '2026-10-01T00:00:00Z',
  ...overrides,
})

const run = (lastSignalAt: string | null, startedAt = '2026-10-02T13:13:00Z'): BotRunSummary => ({
  id: 'r1',
  bot_id: 'b1',
  status: 'running',
  code_version: 'dummy-pipeline-0.1',
  started_at: startedAt,
  stopped_at: null,
  stop_reason: null,
  heartbeat_at: null,
  latest_signal: lastSignalAt ? { id: 's1', action: 'hold', created_at: lastSignalAt } : null,
})

// 04:21 UTC: the newest 4h bar closed at 04:00 UTC, 21 minutes ago (past the 10-minute grace).
const now = new Date('2026-10-03T04:21:00Z')

it('flags a running bot whose last signal predates the newest closed bar', () => {
  // The 2026-10-03 incident: last evaluated 13:30 UTC the previous day.
  const stale = findStaleBots([bot()], { b1: run('2026-10-02T13:30:08Z') }, now)

  expect(stale).toHaveLength(1)
  expect(stale[0].expectedBarClose.toISOString()).toBe('2026-10-03T04:00:00.000Z')
  expect(stale[0].lastEvaluatedAt?.toISOString()).toBe('2026-10-02T13:30:08.000Z')
})

it('does not flag a bot that evaluated the newest bar', () => {
  expect(findStaleBots([bot()], { b1: run('2026-10-03T04:00:40Z') }, now)).toEqual([])
})

it('waits out the grace period right after a bar closes', () => {
  const justAfterClose = new Date('2026-10-03T04:05:00Z')
  expect(findStaleBots([bot()], { b1: run('2026-10-03T00:00:40Z') }, justAfterClose)).toEqual([])
})

it('does not flag a run that started moments ago', () => {
  expect(findStaleBots([bot()], { b1: run(null, '2026-10-03T04:18:00Z') }, now)).toEqual([])
})

it('flags a running bot that has never evaluated anything', () => {
  expect(findStaleBots([bot()], { b1: run(null) }, now)).toHaveLength(1)
})

it.each(['stopped', 'paused', 'failed'] as const)('ignores a %s bot', (state) => {
  expect(
    findStaleBots([bot({ actual_state: state })], { b1: run('2026-10-02T13:30:08Z') }, now),
  ).toEqual([])
})
