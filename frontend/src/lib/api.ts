export const apiBaseUrl = (import.meta.env.VITE_API_BASE_URL ?? 'http://localhost:8000').replace(
  /\/$/,
  '',
)

export const apiErrorMessage = async (response: Response, fallback: string) => {
  try {
    const payload = (await response.json()) as { detail?: string }
    return payload.detail ? `${fallback}: ${payload.detail}（HTTP ${response.status}）` : fallback
  } catch {
    return `${fallback}（HTTP ${response.status}）`
  }
}

/**
 * Auth (Horizon5 Group A) replaced `X-Owner-Token` with an `HttpOnly`,
 * `SameSite=Strict` `session` cookie (app/security/session.py). JavaScript
 * cannot read or set that cookie -- the browser attaches it automatically,
 * but only when the request opts in with `credentials: 'include'`. Every
 * call into the API must go through this wrapper (instead of bare `fetch`)
 * so that opt-in is never accidentally missed.
 */
export function apiFetch(input: string, init: RequestInit = {}): Promise<Response> {
  return fetch(input, { ...init, credentials: 'include' }).then((response) => {
    if (response.status === 401) notifyUnauthorized()
    return response
  })
}

type UnauthorizedListener = () => void
let unauthorizedListeners: UnauthorizedListener[] = []

/**
 * Lets `useAuth` learn about a 401 that happens *after* the initial
 * `/auth/me` check succeeded (e.g. the session expired mid-session), from
 * deep inside a feature hook that has no direct reference to the auth
 * state. Returns an unsubscribe function.
 */
export function onUnauthorized(listener: UnauthorizedListener): () => void {
  unauthorizedListeners = [...unauthorizedListeners, listener]
  return () => {
    unauthorizedListeners = unauthorizedListeners.filter((entry) => entry !== listener)
  }
}

function notifyUnauthorized(): void {
  unauthorizedListeners.forEach((listener) => listener())
}
