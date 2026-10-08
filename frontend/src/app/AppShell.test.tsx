// @vitest-environment jsdom
import '@testing-library/jest-dom/vitest'
import { cleanup, render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router'
import { afterEach, describe, expect, it } from 'vitest'
import AppShell from './AppShell'

afterEach(cleanup)

describe('AppShell', () => {
  it('shows workspace-scoped navigation when a workspace is selected', () => {
    render(
      <MemoryRouter initialEntries={['/workspaces/workspace-1/connections']}>
        <AppShell workspaceId="workspace-1"><p>content</p></AppShell>
      </MemoryRouter>,
    )

    expect(screen.getByRole('link', { name: '接続管理' })).toHaveAttribute(
      'href', '/workspaces/workspace-1/connections',
    )
    expect(screen.getByRole('link', { name: 'Binance市場' })).toHaveAttribute(
      'href', '/workspaces/workspace-1/markets/binance',
    )
    expect(screen.getByRole('link', { name: 'Bot管理' })).toHaveAttribute(
      'href', '/workspaces/workspace-1/trading',
    )
    expect(screen.getByRole('link', { name: 'バックテスト' })).toHaveAttribute(
      'href', '/workspaces/workspace-1/backtests',
    )
  })

  it('does not invent workspace navigation before selection', () => {
    render(
      <MemoryRouter><AppShell workspaceId=""><p>content</p></AppShell></MemoryRouter>,
    )

    expect(screen.queryByRole('link', { name: '接続管理' })).not.toBeInTheDocument()
    expect(screen.getByText(/Workspaceを選択/)).toBeInTheDocument()
  })

  const withAlert = (path: string, alert: { problems: string[]; unacknowledgedNotifications: number }) =>
    render(
      <MemoryRouter initialEntries={[path]}>
        <AppShell workspaceId="workspace-1" alert={alert}><p>content</p></AppShell>
      </MemoryRouter>,
    )

  it('links to the status page and counts unread notifications on the link', () => {
    withAlert('/workspaces/workspace-1/trading', { problems: [], unacknowledgedNotifications: 3 })

    expect(screen.getByRole('link', { name: 'システム状態 未確認の通知 3件' })).toHaveAttribute(
      'href', '/workspaces/workspace-1/status',
    )
    expect(screen.getByLabelText('未確認の通知 3件')).toHaveTextContent('3')
  })

  it('shows the first problem on every page, with how many more there are', () => {
    withAlert('/workspaces/workspace-1/trading', {
      problems: ['トレーディングWorkerが止まっている可能性があります', '市場データWorkerが止まっている可能性があります'],
      unacknowledgedNotifications: 0,
    })

    const banner = screen.getByRole('alert')
    expect(banner).toHaveTextContent('トレーディングWorkerが止まっている可能性があります(ほか1件)')
    expect(screen.getByRole('link', { name: 'システム状態を見る' })).toHaveAttribute(
      'href', '/workspaces/workspace-1/status',
    )
    expect(screen.getByLabelText('要確認の項目があります')).toBeInTheDocument()
  })

  it('says so when only notifications are unread', () => {
    withAlert('/workspaces/workspace-1/connections', { problems: [], unacknowledgedNotifications: 2 })

    expect(screen.getByRole('alert')).toHaveTextContent('未確認の通知が2件あります')
  })

  it('does not repeat the banner on the status page itself, nor show it when all is well', () => {
    const { unmount } = withAlert('/workspaces/workspace-1/status', {
      problems: ['何か'],
      unacknowledgedNotifications: 1,
    })
    expect(screen.queryByRole('alert')).not.toBeInTheDocument()
    unmount()

    withAlert('/workspaces/workspace-1/trading', { problems: [], unacknowledgedNotifications: 0 })
    expect(screen.queryByRole('alert')).not.toBeInTheDocument()
  })
})
