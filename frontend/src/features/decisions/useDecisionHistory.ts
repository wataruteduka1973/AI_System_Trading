import { useCallback, useEffect, useRef, useState } from 'react'
import { apiBaseUrl, apiErrorMessage, apiFetch } from '../../lib/api'
import { periodHours } from './labels'
import type {
  DecisionFilters,
  DecisionHalt,
  DecisionHistory,
  DecisionItem,
} from './types'
import { defaultDecisionFilters } from './types'

const PAGE_SIZE = 100

export function buildDecisionQuery(
  filters: DecisionFilters,
  now: Date,
  cursor: { before: string; beforeId: string } | null,
): string {
  const params = new URLSearchParams()
  filters.outcomes.forEach((outcome) => params.append('outcome', outcome))
  const hours = periodHours[filters.period]
  if (hours !== null && hours !== undefined) {
    params.set('from_time', new Date(now.getTime() - hours * 3600_000).toISOString())
  }
  params.set('limit', String(PAGE_SIZE))
  if (cursor) {
    params.set('before', cursor.before)
    params.set('before_id', cursor.beforeId)
  }
  return params.toString()
}

/** One bot's decision history: its evaluated bars newest first (older pages on request), the
 * summary over the whole period, and the halts that apply. The bot defaults to the first one. */
export function useDecisionHistory(workspaceId: string, botIds: string[]) {
  const [filters, setFilters] = useState<DecisionFilters>(defaultDecisionFilters)
  const [items, setItems] = useState<DecisionItem[]>([])
  const [summary, setSummary] = useState<DecisionHistory['summary']>({ evaluations: 0, counts: {} })
  const [halts, setHalts] = useState<DecisionHalt[]>([])
  const [cursor, setCursor] = useState<{ before: string; beforeId: string } | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  const [loadedAt, setLoadedAt] = useState<Date | null>(null)
  const generation = useRef(0)
  const startedAt = useRef(new Date())

  // The bot in use: the chosen one, or the first, while the chosen one still exists.
  const botId = botIds.includes(filters.botId) ? filters.botId : (botIds[0] ?? '')

  const load = useCallback(
    async (id: string, bot: string, current: DecisionFilters, at: Date, from: typeof cursor, append: boolean) => {
      const mine = ++generation.current
      setLoading(true)
      try {
        const response = await apiFetch(
          `${apiBaseUrl}/api/v1/workspaces/${id}/bots/${bot}/decisions?${buildDecisionQuery(current, at, from)}`,
        )
        if (mine !== generation.current) return
        if (!response.ok) {
          setError(await apiErrorMessage(response, '判断の履歴を取得できませんでした'))
          return
        }
        const page = (await response.json()) as DecisionHistory
        setItems((existing) => (append ? [...existing, ...page.items] : page.items))
        setSummary(page.summary)
        setHalts((existing) => {
          if (!append) return page.halts
          const known = new Set(existing.map((halt) => halt.id))
          return [...existing, ...page.halts.filter((halt) => !known.has(halt.id))]
        })
        setCursor(
          page.next_before && page.next_before_id
            ? { before: page.next_before, beforeId: page.next_before_id }
            : null,
        )
        setError('')
        setLoadedAt(new Date())
      } catch {
        if (mine === generation.current) setError('判断の履歴APIへ接続できません。')
      } finally {
        if (mine === generation.current) setLoading(false)
      }
    },
    [],
  )

  const reload = useCallback(() => {
    if (!workspaceId || !botId) return
    startedAt.current = new Date()
    void load(workspaceId, botId, filters, startedAt.current, null, false)
  }, [workspaceId, botId, filters, load])

  useEffect(() => {
    if (!workspaceId || !botId) return
    const timer = window.setTimeout(reload, 0)
    return () => window.clearTimeout(timer)
  }, [workspaceId, botId, reload])

  const loadMore = () => {
    if (!workspaceId || !botId || !cursor) return
    void load(workspaceId, botId, filters, startedAt.current, cursor, true)
  }

  return {
    filters,
    setFilters,
    botId,
    items,
    summary,
    halts,
    hasMore: cursor !== null,
    loading,
    error,
    loadedAt,
    loadMore,
    reload,
  }
}
