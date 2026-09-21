import type { WorkspaceRole } from '../auth/types'

/** Derived from `useAuth`'s `memberships` (GET /api/v1/auth/me), already
 * filtered server-side to workspaces the current user belongs to. */
export type WorkspaceOption = {
  id: string
  name: string
  role: WorkspaceRole
}
