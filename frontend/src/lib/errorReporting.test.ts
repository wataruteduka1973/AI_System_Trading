// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from 'vitest'
import { installGlobalErrorReporting, reportClientError } from './errorReporting'

afterEach(() => {
  vi.unstubAllGlobals()
})

describe('reportClientError', () => {
  it('posts to /api/v1/client-logs with credentials included, relying on the session cookie', () => {
    const request = vi.fn<typeof fetch>(() => Promise.resolve(new Response(null, { status: 202 })))
    vi.stubGlobal('fetch', request)

    reportClientError({ message: 'boom', stack: 'at foo()', source: 'window.onerror' })

    expect(request).toHaveBeenCalledTimes(1)
    const [url, options] = request.mock.calls[0] as [string, RequestInit]
    expect(url).toMatch(/\/api\/v1\/client-logs$/)
    expect(options.method).toBe('POST')
    expect(options.credentials).toBe('include')
    expect(options.headers).not.toHaveProperty('X-Owner-Token')

    const body = JSON.parse(options.body as string) as Record<string, unknown>
    expect(body).toMatchObject({ message: 'boom', stack: 'at foo()', source: 'window.onerror' })
    expect(typeof body.path).toBe('string')
    expect(typeof body.user_agent).toBe('string')
  })

  it('truncates oversized fields before sending, instead of rejecting client-side', () => {
    const request = vi.fn<typeof fetch>(() => Promise.resolve(new Response(null, { status: 202 })))
    vi.stubGlobal('fetch', request)

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

    expect(() => reportClientError({ message: 'boom', source: 'test' })).not.toThrow()
  })

  it('never throws when not logged in and the backend rejects with 401', () => {
    vi.stubGlobal('fetch', vi.fn<typeof fetch>(() => Promise.resolve(new Response(null, { status: 401 }))))

    expect(() => reportClientError({ message: 'boom', source: 'test' })).not.toThrow()
  })
})

describe('installGlobalErrorReporting', () => {
  it('reports window.onerror and unhandledrejection events, and installs only once', () => {
    const request = vi.fn<typeof fetch>(() => Promise.resolve(new Response(null, { status: 202 })))
    vi.stubGlobal('fetch', request)

    installGlobalErrorReporting()
    installGlobalErrorReporting() // idempotent: must not double-register listeners

    window.dispatchEvent(
      new ErrorEvent('error', { message: 'render blew up', error: new Error('render blew up') }),
    )
    // jsdom (the test DOM used by vitest here) does not implement the
    // PromiseRejectionEvent constructor that real browsers use for this
    // event, so build a plain Event and attach the fields the
    // `unhandledrejection` listener actually reads (`reason`).
    const rejectionEvent = new Event('unhandledrejection') as Event & { reason: unknown }
    rejectionEvent.reason = new Error('promise blew up')
    window.dispatchEvent(rejectionEvent)

    expect(request).toHaveBeenCalledTimes(2)
    const sources = request.mock.calls.map(([, options]) => {
      const body = JSON.parse((options as RequestInit).body as string) as { source: string }
      return body.source
    })
    expect(sources).toEqual(['window.onerror', 'unhandledrejection'])
  })
})
