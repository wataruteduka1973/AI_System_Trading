import { apiBaseUrl } from './api'

/**
 * Best-effort relay of frontend errors to the backend, which writes them
 * to logs/frontend.log (see app/api/routes/client_logs.py). Only the
 * fields below are ever sent -- never a request payload, form field
 * value, or anything else that could carry a credential.
 *
 * The backend endpoint requires any authenticated user (no workspace role),
 * enforced via the `session` cookie. That cookie is `HttpOnly`, so it
 * cannot be read or attached manually -- `credentials: 'include'` is both
 * necessary and sufficient for the browser to send it. If the user is not
 * logged in yet, the backend rejects with 401 and the request is dropped
 * silently (see the `.catch` below): errors before login are visible in
 * the browser console only.
 */

function truncate(value: string, maxLength: number): string {
  return value.length > maxLength ? value.slice(0, maxLength) : value
}

export interface ReportableError {
  message: string
  stack?: string
  source: string
}

export function reportClientError(entry: ReportableError): void {
  const payload = {
    message: truncate(entry.message, 2000),
    stack: entry.stack ? truncate(entry.stack, 8000) : undefined,
    source: truncate(entry.source, 200),
    path: window.location.pathname,
    user_agent: navigator.userAgent,
  }

  void fetch(`${apiBaseUrl}/api/v1/client-logs`, {
    method: 'POST',
    credentials: 'include',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  }).catch(() => {
    // Best-effort only: reporting a failure to report must never itself
    // throw, retry-loop, or surface to the user.
  })
}

let installed = false

/** Call once at startup (see main.tsx). Idempotent. */
export function installGlobalErrorReporting(): void {
  if (installed) return
  installed = true

  window.addEventListener('error', (event: ErrorEvent) => {
    reportClientError({
      message: event.message,
      stack: event.error instanceof Error ? event.error.stack : undefined,
      source: 'window.onerror',
    })
  })

  window.addEventListener('unhandledrejection', (event: PromiseRejectionEvent) => {
    const reason: unknown = event.reason
    reportClientError({
      message: reason instanceof Error ? reason.message : String(reason),
      stack: reason instanceof Error ? reason.stack : undefined,
      source: 'unhandledrejection',
    })
  })
}
