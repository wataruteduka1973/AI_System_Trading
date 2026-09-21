export type WorkspaceRole = 'viewer' | 'operator' | 'owner'

export type AuthenticatedUser = {
  id: string
  email: string
  display_name: string
  status: string
}

export type WorkspaceMembership = {
  workspace_id: string
  workspace_name: string
  role: WorkspaceRole
}

/** Mirrors `app.schemas.auth.CurrentUserRead` (GET /api/v1/auth/me). */
export type CurrentUser = {
  user: AuthenticatedUser
  memberships: WorkspaceMembership[]
}
