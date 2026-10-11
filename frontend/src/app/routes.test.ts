import { describe, expect, it } from 'vitest'
import {
  backtestPath,
  connectionPath,
  decisionsPath,
  eventsPath,
  gapsPath,
  historyPath,
  marketPath,
  resolveAppRoute,
  statusPath,
  tradingPath,
} from './routes'

describe('application routes', () => {
  it('resolves the home page', () => {
    expect(resolveAppRoute('/')).toEqual({ kind: 'home', workspaceId: null, exchange: null })
  })

  it('resolves workspace connection, trading, backtests, and market routes', () => {
    expect(resolveAppRoute('/workspaces/workspace-1/connections')).toEqual({
      kind: 'connections', workspaceId: 'workspace-1', exchange: null,
    })
    expect(resolveAppRoute('/workspaces/workspace-1/trading')).toEqual({
      kind: 'trading', workspaceId: 'workspace-1', exchange: null,
    })
    expect(resolveAppRoute('/workspaces/workspace-1/backtests')).toEqual({
      kind: 'backtests', workspaceId: 'workspace-1', exchange: null,
    })
    expect(resolveAppRoute('/workspaces/workspace-1/status')).toEqual({
      kind: 'status', workspaceId: 'workspace-1', exchange: null,
    })
    expect(statusPath('workspace-1')).toBe('/workspaces/workspace-1/status')
    expect(resolveAppRoute('/workspaces/workspace-1/events')).toEqual({
      kind: 'events', workspaceId: 'workspace-1', exchange: null,
    })
    expect(eventsPath('workspace-1')).toBe('/workspaces/workspace-1/events')
    expect(resolveAppRoute('/workspaces/workspace-1/data-gaps')).toEqual({
      kind: 'gaps', workspaceId: 'workspace-1', exchange: null,
    })
    expect(gapsPath('workspace-1')).toBe('/workspaces/workspace-1/data-gaps')
    expect(resolveAppRoute('/workspaces/workspace-1/trade-history')).toEqual({
      kind: 'history', workspaceId: 'workspace-1', exchange: null,
    })
    expect(historyPath('workspace-1')).toBe('/workspaces/workspace-1/trade-history')
    expect(resolveAppRoute('/workspaces/workspace-1/decisions')).toEqual({
      kind: 'decisions', workspaceId: 'workspace-1', exchange: null,
    })
    expect(decisionsPath('workspace-1')).toBe('/workspaces/workspace-1/decisions')
    expect(resolveAppRoute('/workspaces/workspace-1/markets/binance')).toEqual({
      kind: 'market', workspaceId: 'workspace-1', exchange: 'binance',
    })
    expect(resolveAppRoute('/workspaces/workspace-1/markets/binance_public')).toEqual({
      kind: 'market', workspaceId: 'workspace-1', exchange: 'binance_public',
    })
  })

  it('rejects unknown exchanges and preserves encoded workspace ids', () => {
    expect(resolveAppRoute('/workspaces/workspace-1/markets/unknown').kind).toBe('not-found')
    expect(connectionPath('workspace / one')).toBe('/workspaces/workspace%20%2F%20one/connections')
    expect(marketPath('workspace-1', 'oanda')).toBe('/workspaces/workspace-1/markets/oanda')
    expect(tradingPath('workspace / one')).toBe('/workspaces/workspace%20%2F%20one/trading')
    expect(backtestPath('workspace / one')).toBe('/workspaces/workspace%20%2F%20one/backtests')
  })
})
