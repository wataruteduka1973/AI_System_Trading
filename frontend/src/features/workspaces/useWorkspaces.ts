import { useEffect, useRef, useState } from 'react'
import type { WorkspaceMembership } from '../auth/types'
import type { WorkspaceOption } from './types'

/** `memberships` come from `useAuth` (GET /api/v1/auth/me), already
 * role-filtered server-side -- this hook no longer fetches its own,
 * unfiltered workspace list. It only derives display options from them and
 * owns which workspace is currently selected.
 *
 * `onWorkspaceSelected` may return a status message; if it does, this hook shows it as the
 * workspace message. This keeps the callback (defined in App.tsx, before this hook is called)
 * from needing this hook's own `setWorkspaceMessage` in its closure. */
export function useWorkspaces(
  routeWorkspaceId: string | null,
  memberships: WorkspaceMembership[],
  onWorkspaceSelected: (workspaceId: string) => Promise<string | void>,
) {
  const [selectedWorkspaceId, setSelectedWorkspaceId] = useState(routeWorkspaceId ?? '')
  const [workspaceMessage, setWorkspaceMessage] = useState('Workspaceを読み込んでいます。')
  // Guards against re-running the route-driven auto-select every time
  // `memberships` is replaced with a new (but equal) array reference.
  const appliedRouteWorkspaceId = useRef<string | null>(null)
  // `onWorkspaceSelected` is a fresh closure every render (App.tsx), so it is read
  // through a ref rather than added to the effect's dependency array below.
  const onWorkspaceSelectedRef = useRef(onWorkspaceSelected)
  useEffect(() => {
    onWorkspaceSelectedRef.current = onWorkspaceSelected
  }, [onWorkspaceSelected])

  const workspaces: WorkspaceOption[] = memberships.map((membership) => ({
    id: membership.workspace_id,
    name: membership.workspace_name,
    role: membership.role,
  }))

  useEffect(() => {
    // Deferred by one tick (mirrors useMarketData.ts's own initial-load
    // effect): calling setState synchronously in an effect body triggers
    // an avoidable extra render, so every setState call below (including the
    // empty/not-found error cases -- /code-review finding: these used to
    // return before ever leaving the initial "Workspaceを読み込んでいます。"
    // message, so a memberless user or an invalid deep link froze on a
    // loading state forever with no error and no way out) happens inside
    // this callback instead.
    const timer = window.setTimeout(() => {
      if (memberships.length === 0) {
        appliedRouteWorkspaceId.current = null
        setSelectedWorkspaceId('')
        setWorkspaceMessage('利用できるWorkspaceがありません。')
        return
      }
      if (routeWorkspaceId && routeWorkspaceId !== appliedRouteWorkspaceId.current) {
        appliedRouteWorkspaceId.current = routeWorkspaceId
        const routedMembership = memberships.some((m) => m.workspace_id === routeWorkspaceId)
        if (routedMembership) {
          setSelectedWorkspaceId(routeWorkspaceId)
          void onWorkspaceSelectedRef.current(routeWorkspaceId).then((message) => {
            if (message) setWorkspaceMessage(message)
          })
          return
        }
        setSelectedWorkspaceId('')
        setWorkspaceMessage('指定されたWorkspaceが見つからないか、アクセス権がありません。')
        return
      }
      if (!routeWorkspaceId) {
        setWorkspaceMessage(`${memberships.length}件のWorkspaceを利用できます。`)
      }
    }, 0)
    return () => window.clearTimeout(timer)
  }, [memberships, routeWorkspaceId])

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
    selectWorkspace,
  }
}
