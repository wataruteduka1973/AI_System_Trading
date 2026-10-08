import type { ReactNode } from 'react'
import { Link, NavLink, useLocation } from 'react-router'
import type { AuthenticatedUser } from '../features/auth/types'
import {
  backtestPath,
  connectionPath,
  eventsPath,
  marketPath,
  statusPath,
  tradingPath,
} from './routes'

/** What the whole-app banner and the navigation badge say about the workspace's health. */
export type StatusAlert = {
  /** The status API's own problem list (Japanese, ready to show). */
  problems: string[]
  unacknowledgedNotifications: number
}

type AppShellProps = {
  workspaceId: string
  children: ReactNode
  user?: AuthenticatedUser | null
  onLogout?: () => void
  alert?: StatusAlert | null
}

export default function AppShell({ workspaceId, children, user, onLogout, alert }: AppShellProps) {
  const location = useLocation()
  const onStatusPage = workspaceId !== '' && location.pathname === statusPath(workspaceId)
  const unread = alert?.unacknowledgedNotifications ?? 0
  const problems = alert?.problems ?? []
  const showBanner = workspaceId !== '' && !onStatusPage && (problems.length > 0 || unread > 0)
  return (
    <div className="application-shell">
      <nav className="main-navigation" aria-label="メインナビゲーション">
        <NavLink to="/" end>開発状態</NavLink>
        {workspaceId ? (
          <>
            <NavLink to={statusPath(workspaceId)}>
              システム状態
              {unread > 0 && (
                <span className="nav-badge" aria-label={`未確認の通知 ${unread}件`}>
                  {unread}
                </span>
              )}
              {unread === 0 && problems.length > 0 && (
                <span className="nav-badge" aria-label="要確認の項目があります">!</span>
              )}
            </NavLink>
            <NavLink to={eventsPath(workspaceId)}>イベントログ</NavLink>
            <NavLink to={connectionPath(workspaceId)}>接続管理</NavLink>
            <NavLink to={marketPath(workspaceId, 'oanda')}>OANDA市場</NavLink>
            <NavLink to={marketPath(workspaceId, 'binance')}>Binance市場</NavLink>
            <NavLink to={marketPath(workspaceId, 'binance_public')}>公開履歴(検証用)</NavLink>
            <NavLink to={tradingPath(workspaceId)}>Bot管理</NavLink>
            <NavLink to={backtestPath(workspaceId)}>バックテスト</NavLink>
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
      {showBanner && (
        <div className="status-banner" role="alert">
          <strong>要確認</strong>
          <span>
            {problems.length > 0
              ? `${problems[0]}${problems.length > 1 ? `(ほか${problems.length - 1}件)` : ''}`
              : `未確認の通知が${unread}件あります`}
          </span>
          <Link to={statusPath(workspaceId)}>システム状態を見る</Link>
        </div>
      )}
      {children}
    </div>
  )
}
