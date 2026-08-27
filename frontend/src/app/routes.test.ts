import { describe, expect, it } from 'vitest'
import { connectionPath, marketPath, resolveAppRoute } from './routes'

describe('application routes', () => {
  it('resolves the home page', () => {
    expect(resolveAppRoute('/')).toEqual({ kind: 'home', workspaceId: null, exchange: null })
  })

  it('resolves workspace connection and market routes', () => {
    expect(resolveAppRoute('/workspaces/workspace-1/connections')).toEqual({
      kind: 'connections', workspaceId: 'workspace-1', exchange: null,
    })
    expect(resolveAppRoute('/workspaces/workspace-1/markets/binance')).toEqual({
      kind: 'market', workspaceId: 'workspace-1', exchange: 'binance',
    })
  })

  it('rejects unknown exchanges and preserves encoded workspace ids', () => {
    expect(resolveAppRoute('/workspaces/workspace-1/markets/unknown').kind).toBe('not-found')
    expect(connectionPath('workspace / one')).toBe('/workspaces/workspace%20%2F%20one/connections')
    expect(marketPath('workspace-1', 'oanda')).toBe('/workspaces/workspace-1/markets/oanda')
  })
})
