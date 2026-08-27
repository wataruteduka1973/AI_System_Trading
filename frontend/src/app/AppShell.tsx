import type { ReactNode } from 'react'
import { NavLink } from 'react-router'
import { connectionPath, marketPath } from './routes'

type AppShellProps = {
  workspaceId: string
  children: ReactNode
}

export default function AppShell({ workspaceId, children }: AppShellProps) {
  return (
    <div className="application-shell">
      <nav className="main-navigation" aria-label="メインナビゲーション">
        <NavLink to="/" end>開発状態</NavLink>
        {workspaceId ? (
          <>
            <NavLink to={connectionPath(workspaceId)}>接続管理</NavLink>
            <NavLink to={marketPath(workspaceId, 'oanda')}>OANDA市場</NavLink>
            <NavLink to={marketPath(workspaceId, 'binance')}>Binance市場</NavLink>
          </>
        ) : (
          <span className="navigation-hint">Workspaceを選択すると市場ページを利用できます</span>
        )}
      </nav>
      {children}
    </div>
  )
}
