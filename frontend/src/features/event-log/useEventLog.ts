import { useCallback, useEffect, useRef, useState } from 'react'
import { apiBaseUrl, apiErrorMessage, apiFetch } from '../../lib/api'
import { periodHours } from './eventLabels'
import type { EventFacets, EventFilters, EventPage, SystemEvent } from './types'
import { emptyFilters } from './types'

const PAGE_SIZE = 50
const TEXT_DEBOUNCE_MS = 400

/** The query string for one page of the log. `now` is passed in so a period resolves to the same
 * instant for every page of one search. */
export function buildEventQuery(
  filters: EventFilters,
  now: Date,
  cursor: { before: string; beforeId: string } | null,
): string {
  const params = new URLSearchParams()
  filters.severities.forEach((severity) => params.append('severity', severity))
  if (filters.category) params.set('category', filters.category)
  if (filters.eventType) params.set('event_type', filters.eventType)
  if (filters.reasonCode) params.set('reason_code', filters.reasonCode)
  if (filters.botId) params.set('bot_id', filters.botId)
  if (filters.correlationId) params.set('correlation_id', filters.correlationId)
  if (filters.text.trim()) params.set('q', filters.text.trim())
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

/** The workspace's event log: a search that is redone when a filter changes (the search word
 * after a short pause), and older pages on request. A newer search replaces an older one that is
 * still in flight. */
export function useEventLog(workspaceId: string) {
  const [filters, setFilters] = useState<EventFilters>(emptyFilters)
  const [events, setEvents] = useState<SystemEvent[]>([])
  const [facets, setFacets] = useState<EventFacets | null>(null)
  const [cursor, setCursor] = useState<{ before: string; beforeId: string } | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  const [searchedAt, setSearchedAt] = useState<Date | null>(null)
  const generation = useRef(0)
  const searchedFilters = useRef<{ filters: EventFilters; at: Date } | null>(null)

  const fetchPage = useCallback(
    async (id: string, current: EventFilters, at: Date, from: typeof cursor, append: boolean) => {
      const mine = ++generation.current
      setLoading(true)
      try {
        const response = await apiFetch(
          `${apiBaseUrl}/api/v1/workspaces/${id}/events?${buildEventQuery(current, at, from)}`,
        )
        if (mine !== generation.current) return
        if (!response.ok) {
          setError(await apiErrorMessage(response, 'イベントを取得できませんでした'))
          return
        }
        const page = (await response.json()) as EventPage
        setEvents((existing) => (append ? [...existing, ...page.items] : page.items))
        setCursor(
          page.next_before && page.next_before_id
            ? { before: page.next_before, beforeId: page.next_before_id }
            : null,
        )
        setError('')
      } catch {
        if (mine === generation.current) setError('イベントログAPIへ接続できません。')
      } finally {
        if (mine === generation.current) setLoading(false)
      }
    },
    [],
  )

  const search = useCallback(
    (id: string, current: EventFilters) => {
      const at = new Date()
      searchedFilters.current = { filters: current, at }
      setSearchedAt(at)
      void fetchPage(id, current, at, null, false)
    },
    [fetchPage],
  )

  // The facets (the filter choices) are loaded once per workspace.
  useEffect(() => {
    if (!workspaceId) return
    let active = true
    apiFetch(`${apiBaseUrl}/api/v1/workspaces/${workspaceId}/events/facets`)
      .then(async (response) => {
        if (active && response.ok) setFacets((await response.json()) as EventFacets)
      })
      .catch(() => undefined)
    return () => {
      active = false
    }
  }, [workspaceId])

  // Search again when a filter changes; the search word waits for a pause in typing.
  useEffect(() => {
    if (!workspaceId) return
    const timer = window.setTimeout(() => search(workspaceId, filters), filters.text ? TEXT_DEBOUNCE_MS : 0)
    return () => window.clearTimeout(timer)
  }, [workspaceId, filters, search])

  const loadMore = () => {
    const last = searchedFilters.current
    if (!workspaceId || !cursor || !last) return
    void fetchPage(workspaceId, last.filters, last.at, cursor, true)
  }

  const reload = () => {
    if (workspaceId) search(workspaceId, filters)
  }

  return {
    filters,
    setFilters,
    events,
    facets,
    hasMore: cursor !== null,
    loading,
    error,
    searchedAt,
    loadMore,
    reload,
  }
}
