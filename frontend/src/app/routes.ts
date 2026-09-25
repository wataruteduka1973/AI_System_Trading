export type ExchangeCode = 'oanda' | 'binance'

export type AppRoute =
  | { kind: 'home'; workspaceId: null; exchange: null }
  | { kind: 'connections'; workspaceId: string; exchange: null }
  | { kind: 'market'; workspaceId: string; exchange: ExchangeCode }
  | { kind: 'trading'; workspaceId: string; exchange: null }
  | { kind: 'not-found'; workspaceId: null; exchange: null }

const workspaceRoute =
  /^\/workspaces\/([^/]+)\/(connections|trading|markets\/(oanda|binance))\/?$/

export function resolveAppRoute(pathname: string): AppRoute {
  if (pathname === '/' || pathname === '') {
    return { kind: 'home', workspaceId: null, exchange: null }
  }
  const match = workspaceRoute.exec(pathname)
  if (!match) {
    return { kind: 'not-found', workspaceId: null, exchange: null }
  }
  const workspaceId = decodeURIComponent(match[1])
  if (match[2] === 'connections') {
    return { kind: 'connections', workspaceId, exchange: null }
  }
  if (match[2] === 'trading') {
    return { kind: 'trading', workspaceId, exchange: null }
  }
  return { kind: 'market', workspaceId, exchange: match[3] as ExchangeCode }
}

export const connectionPath = (workspaceId: string) =>
  `/workspaces/${encodeURIComponent(workspaceId)}/connections`

export const marketPath = (workspaceId: string, exchange: ExchangeCode) =>
  `/workspaces/${encodeURIComponent(workspaceId)}/markets/${exchange}`

export const tradingPath = (workspaceId: string) =>
  `/workspaces/${encodeURIComponent(workspaceId)}/trading`
