// @vitest-environment jsdom
import { cleanup, renderHook, waitFor } from '@testing-library/react'
import { afterEach, expect, it } from 'vitest'
import type { WorkspaceMembership } from '../auth/types'
import { useWorkspaces } from './useWorkspaces'

afterEach(() => { cleanup() })

const membership = (id: string, name: string): WorkspaceMembership => ({
  workspace_id: id,
  workspace_name: name,
  role: 'owner',
})

const noop = async () => undefined

/** Regression tests (/code-review finding): opening a deep link to a
 * workspace the user is not a member of -- or simply having no memberships
 * at all -- used to leave `workspaceMessage` stuck on its initial
 * "Workspaceを読み込んでいます。" (loading) text forever, with no error shown
 * and (for the deep-link case) `selectedWorkspaceId` still pointing at the
 * inaccessible workspace. */

it('shows an error and clears the selection for a deep link to a workspace the user is not a member of', async () => {
  const { result } = renderHook(() =>
    useWorkspaces('not-mine', [membership('ws-1', 'Personal')], noop),
  )

  await waitFor(() => {
    expect(result.current.workspaceMessage).not.toBe('Workspaceを読み込んでいます。')
  })

  expect(result.current.workspaceMessage).toBe('指定されたWorkspaceが見つからないか、アクセス権がありません。')
  expect(result.current.selectedWorkspaceId).toBe('')
})

it('shows an empty-state message instead of loading forever when the user has no memberships', async () => {
  const { result } = renderHook(() => useWorkspaces(null, [], noop))

  await waitFor(() => {
    expect(result.current.workspaceMessage).not.toBe('Workspaceを読み込んでいます。')
  })

  expect(result.current.workspaceMessage).toBe('利用できるWorkspaceがありません。')
  expect(result.current.selectedWorkspaceId).toBe('')
})

it('still auto-selects a deep-linked workspace the user does belong to', async () => {
  const { result } = renderHook(() =>
    useWorkspaces('ws-1', [membership('ws-1', 'Personal')], noop),
  )

  await waitFor(() => {
    expect(result.current.selectedWorkspaceId).toBe('ws-1')
  })
})
