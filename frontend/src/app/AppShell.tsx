import type { ReactNode } from 'react'
import { NavLink } from 'react-router'
import type { AuthenticatedUser } from '../features/auth/types'
import { connectionPath, marketPath, tradingPath } from './routes'

type AppShellProps = {
  workspaceId: string
  children: ReactNode
  user?: AuthenticatedUser | null
  onLogout?: () => void
}

export default function AppShell({ workspaceId, children, user, onLogout }: AppShellProps) {
  return (
    <div className="application-shell">
      <nav className="main-navigation" aria-label="メインナビゲーション">
        <NavLink to="/" end>開発状態</NavLink>
        {workspaceId ? (
          <>
            <NavLink to={connectionPath(workspaceId)}>接続管理</NavLink>
            <NavLink to={marketPath(workspaceId, 'oanda')}>OANDA市場</NavLink>
            <NavLink to={marketPath(workspaceId, 'binance')}>Binance市場</NavLink>
            <NavLink to={tradingPath(workspaceId)}>Bot管理</NavLink>
          </>
        ) : (
          <span className="navigation-hint">Workspaceを選択すると市場ページを利用できます</span>
        )}
        {user && (
          <span className="session-status">
            <span className="session-user">{user.display_name}</span>
            {onLogout && (
              <button type="button" onClick={onLogout}>
                ログアウト
              </button>
            )}
          </span>
        )}
      </nav>
      {children}
    </div>
  )
}
