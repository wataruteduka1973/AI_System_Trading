import { useCallback, useEffect, useState } from 'react'
import { apiBaseUrl, apiFetch, onUnauthorized } from '../../lib/api'
import type { AuthenticatedUser, CurrentUser, WorkspaceMembership } from './types'

/**
 * OIDC session state (Horizon5 Group A -- app/api/routes/auth.py).
 * On mount, calls `GET /api/v1/auth/me`: the `session` cookie is
 * `HttpOnly`, so this is the only way the frontend can learn whether the
 * browser already holds a valid session. A 401 (never logged in, or a
 * since-expired/-revoked session) means "unauthenticated" -- the caller is
 * expected to show a login affordance that navigates (not fetches) to
 * `GET /api/v1/auth/login`, since that is a redirect to an external IdP.
 *
 * `onUnauthorized` (lib/api.ts) also drives this hook: any `apiFetch` call
 * anywhere in the app that comes back 401 *after* the initial check
 * succeeded (e.g. the session expired mid-session) flips status back to
 * 'unauthenticated' too, so the whole app reacts consistently rather than
 * each feature hook silently failing on its own.
 */
export type AuthStatus = 'loading' | 'authenticated' | 'unauthenticated'

export function useAuth() {
  const [status, setStatus] = useState<AuthStatus>('loading')
  const [user, setUser] = useState<AuthenticatedUser | null>(null)
  const [memberships, setMemberships] = useState<WorkspaceMembership[]>([])

  const refresh = useCallback(async () => {
    setStatus('loading')
    try {
      const response = await apiFetch(`${apiBaseUrl}/api/v1/auth/me`)
      if (!response.ok) {
        setUser(null)
        setMemberships([])
        setStatus('unauthenticated')
        return
      }
      const payload = (await response.json()) as CurrentUser
      setUser(payload.user)
      setMemberships(payload.memberships)
      setStatus('authenticated')
    } catch {
      setUser(null)
      setMemberships([])
      setStatus('unauthenticated')
    }
  }, [])

  useEffect(() => {
    // Deferred by one tick (mirrors useMarketData.ts's own initial-load
    // effect): `refresh` calls `setStatus('loading')` synchronously before
    // its first `await`, and calling that directly from the effect body
    // triggers an avoidable extra render.
    const timer = window.setTimeout(() => void refresh(), 0)
    return () => window.clearTimeout(timer)
  }, [refresh])

  useEffect(
    () =>
      onUnauthorized(() => {
        setUser(null)
        setMemberships([])
        setStatus('unauthenticated')
      }),
    [],
  )

  /** A real top-level navigation, not a fetch: the target is a 307 redirect
   * into an external IdP's own login page, which only works as a browser
   * navigation. */
  const login = useCallback(() => {
    window.location.href = `${apiBaseUrl}/api/v1/auth/login`
  }, [])

  const logout = useCallback(async () => {
    try {
      await apiFetch(`${apiBaseUrl}/api/v1/auth/logout`, { method: 'POST' })
    } finally {
      setUser(null)
      setMemberships([])
      setStatus('unauthenticated')
    }
  }, [])

  return { status, user, memberships, login, logout, refresh }
}
