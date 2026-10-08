import { useCallback, useEffect, useRef, useState } from 'react'
import { apiBaseUrl, apiFetch } from '../../lib/api'
import type { SystemStatus } from './types'

const POLL_MS = 15000

/** Polls `GET .../system-status` for the selected workspace. `error` is set while the call
 * fails, so the page can say it does not know -- a stale "all clear" must not stay on screen. */
export function useSystemStatus(workspaceId: string) {
  const [status, setStatus] = useState<SystemStatus | null>(null)
  const [error, setError] = useState('')
  const generation = useRef(0)

  const load = useCallback(async (id: string) => {
    const mine = ++generation.current
    if (!id) {
      setStatus(null)
      setError('')
      return
    }
    try {
      const response = await apiFetch(`${apiBaseUrl}/api/v1/workspaces/${id}/system-status`)
      if (mine !== generation.current) return
      if (!response.ok) {
        setError(`システム状態を取得できません(HTTP ${response.status})。`)
        return
      }
      setStatus((await response.json()) as SystemStatus)
      setError('')
    } catch {
      if (mine === generation.current) setError('システム状態APIへ接続できません。')
    }
  }, [])

  useEffect(() => {
    if (!workspaceId) return
    const initial = window.setTimeout(() => void load(workspaceId), 0)
    const timer = window.setInterval(() => void load(workspaceId), POLL_MS)
    return () => {
      window.clearTimeout(initial)
      window.clearInterval(timer)
    }
  }, [load, workspaceId])

  return { status, error, reload: () => load(workspaceId) }
}
