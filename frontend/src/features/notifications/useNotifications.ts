import { useCallback, useEffect, useRef, useState } from 'react'
import { apiBaseUrl, apiErrorMessage, apiFetch } from '../../lib/api'
import type { NotificationList } from './types'

const POLL_MS = 15000

/** The signed-in person's in-app notifications for the workspace, newest first. */
export function useNotifications(workspaceId: string) {
  const [list, setList] = useState<NotificationList>({ unacknowledged_count: 0, items: [] })
  const [message, setMessage] = useState('')
  const generation = useRef(0)

  const load = useCallback(async (id: string) => {
    const mine = ++generation.current
    if (!id) {
      setList({ unacknowledged_count: 0, items: [] })
      return
    }
    try {
      const response = await apiFetch(`${apiBaseUrl}/api/v1/workspaces/${id}/notifications`)
      if (mine !== generation.current) return
      if (response.ok) setList((await response.json()) as NotificationList)
    } catch {
      if (mine === generation.current) setMessage('通知APIへ接続できません。')
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

  const post = async (path: string, failure: string) => {
    if (!workspaceId) return
    try {
      const response = await apiFetch(
        `${apiBaseUrl}/api/v1/workspaces/${workspaceId}/notifications${path}`,
        { method: 'POST' },
      )
      setMessage(response.ok ? '' : await apiErrorMessage(response, failure))
      await load(workspaceId)
    } catch {
      setMessage('通知APIへ接続できません。')
    }
  }

  return {
    ...list,
    message,
    acknowledge: (notificationId: string) =>
      post(`/${notificationId}/acknowledge`, '通知を確認済みにできませんでした'),
    acknowledgeAll: () => post('/acknowledge-all', '通知を確認済みにできませんでした'),
  }
}
