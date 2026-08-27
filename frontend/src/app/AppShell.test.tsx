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
  })

  it('does not invent workspace navigation before selection', () => {
    render(
      <MemoryRouter><AppShell workspaceId=""><p>content</p></AppShell></MemoryRouter>,
    )

    expect(screen.queryByRole('link', { name: '接続管理' })).not.toBeInTheDocument()
    expect(screen.getByText(/Workspaceを選択/)).toBeInTheDocument()
  })
})
