// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from 'vitest'
import {
  installGlobalErrorReporting,
  reportClientError,
  setOwnerTokenForErrorReporting,
} from './errorReporting'

afterEach(() => {
  vi.unstubAllGlobals()
  // Reset the module-level token so tests don't leak state into each other.
  setOwnerTokenForErrorReporting('')
})

describe('reportClientError', () => {
  it('does not call fetch before an owner token has been set', () => {
    const request = vi.fn()
    vi.stubGlobal('fetch', request)

    reportClientError({ message: 'boom', source: 'test' })

    expect(request).not.toHaveBeenCalled()
  })

  it('posts to /api/v1/client-logs with the owner token once one is set', () => {
    const request = vi.fn<typeof fetch>(() => Promise.resolve(new Response(null, { status: 202 })))
    vi.stubGlobal('fetch', request)
    setOwnerTokenForErrorReporting('owner-token-123')

    reportClientError({ message: 'boom', stack: 'at foo()', source: 'window.onerror' })

    expect(request).toHaveBeenCalledTimes(1)
    const [url, options] = request.mock.calls[0] as [string, RequestInit]
    expect(url).toMatch(/\/api\/v1\/client-logs$/)
    expect(options.method).toBe('POST')
    expect((options.headers as Record<string, string>)['X-Owner-Token']).toBe('owner-token-123')

    const body = JSON.parse(options.body as string) as Record<string, unknown>
    expect(body).toMatchObject({ message: 'boom', stack: 'at foo()', source: 'window.onerror' })
    expect(typeof body.path).toBe('string')
    expect(typeof body.user_agent).toBe('string')
  })

  it('truncates oversized fields before sending, instead of rejecting client-side', () => {
    const request = vi.fn<typeof fetch>(() => Promise.resolve(new Response(null, { status: 202 })))
    vi.stubGlobal('fetch', request)
    setOwnerTokenForErrorReporting('owner-token-123')

    reportClientError({
      message: 'x'.repeat(5000),
      stack: 'y'.repeat(20000),
      source: 'z'.repeat(500),
    })

    const [, options] = request.mock.calls[0] as [string, RequestInit]
    const body = JSON.parse(options.body as string) as Record<string, string>
    expect(body.message).toHaveLength(2000)
    expect(body.stack).toHaveLength(8000)
    expect(body.source).toHaveLength(200)
  })

  it('never throws when the fetch itself fails (best effort only)', () => {
    vi.stubGlobal('fetch', vi.fn<typeof fetch>(() => Promise.reject(new Error('network down'))))
    setOwnerTokenForErrorReporting('owner-token-123')

    expect(() => reportClientError({ message: 'boom', source: 'test' })).not.toThrow()
  })
})

describe('installGlobalErrorReporting', () => {
  it('reports window.onerror and unhandledrejection events, and installs only once', () => {
    const request = vi.fn<typeof fetch>(() => Promise.resolve(new Response(null, { status: 202 })))
    vi.stubGlobal('fetch', request)
    setOwnerTokenForErrorReporting('owner-token-123')

    installGlobalErrorReporting()
    installGlobalErrorReporting() // idempotent: must not double-register listeners

    window.dispatchEvent(
      new ErrorEvent('error', { message: 'render blew up', error: new Error('render blew up') }),
    )
    window.dispatchEvent(
      new PromiseRejectionEvent('unhandledrejection', {
        promise: Promise.reject(new Error('promise blew up')).catch(() => undefined),
        reason: new Error('promise blew up'),
      }),
    )

    expect(request).toHaveBeenCalledTimes(2)
    const sources = request.mock.calls.map(([, options]) => {
      const body = JSON.parse((options as RequestInit).body as string) as { source: string }
      return body.source
    })
    expect(sources).toEqual(['window.onerror', 'unhandledrejection'])
  })
})
