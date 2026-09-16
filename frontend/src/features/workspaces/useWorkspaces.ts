import { useState } from 'react'
import { apiBaseUrl } from '../../lib/api'
import type { WorkspaceSummary } from './types'

/** `ownerToken` is owned by the caller: every feature's fetch calls need it, so it is
 * plain shared state in App.tsx rather than something this workspace-scoped hook owns.
 *
 * `onWorkspaceSelected` may return a status message; if it does, this hook shows it as the
 * workspace message. This keeps the callback (defined in App.tsx, before this hook is called)
 * from needing this hook's own `setWorkspaceMessage` in its closure. */
export function useWorkspaces(
  routeWorkspaceId: string | null,
  ownerToken: string,
  onWorkspaceSelected: (workspaceId: string) => Promise<string | void>,
) {
  const [workspaces, setWorkspaces] = useState<WorkspaceSummary[]>([])
  const [selectedWorkspaceId, setSelectedWorkspaceId] = useState(routeWorkspaceId ?? '')
  const [workspaceMessage, setWorkspaceMessage] = useState(
    '開発用Owner tokenを入力してWorkspaceを読み込みます。',
  )

  const loadWorkspaces = async () => {
    setWorkspaceMessage('Workspaceを読み込んでいます。')
    try {
      const response = await fetch(`${apiBaseUrl}/api/v1/workspaces`, {
        headers: { 'X-Owner-Token': ownerToken },
      })
      if (!response.ok) {
        setWorkspaceMessage(`認証または取得に失敗しました（HTTP ${response.status}）。`)
        return
      }
      const loaded = (await response.json()) as WorkspaceSummary[]
      setWorkspaces(loaded)
      const routedWorkspace = routeWorkspaceId
      if (routedWorkspace && loaded.some((workspace) => workspace.id === routedWorkspace)) {
        setSelectedWorkspaceId(routedWorkspace)
        const message = await onWorkspaceSelected(routedWorkspace)
        if (message) setWorkspaceMessage(message)
      } else {
        setSelectedWorkspaceId('')
        setWorkspaceMessage(
          loaded.length > 0 ? `${loaded.length}件のWorkspaceを取得しました。` : 'Workspaceは未登録です。',
        )
      }
    } catch {
      setWorkspaceMessage('Workspace APIへ接続できません。')
    }
  }

  const selectWorkspace = async (workspaceId: string) => {
    setSelectedWorkspaceId(workspaceId)
    const message = await onWorkspaceSelected(workspaceId)
    if (message) setWorkspaceMessage(message)
  }

  return {
    workspaces,
    selectedWorkspaceId,
    workspaceMessage,
    setWorkspaceMessage,
    loadWorkspaces,
    selectWorkspace,
  }
}
