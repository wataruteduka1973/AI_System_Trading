import { useCallback, useEffect, useRef, useState } from 'react'
import { apiBaseUrl, apiErrorMessage, apiFetch } from '../../lib/api'
import { periodHours } from './labels'
import type {
  FillPage,
  FillRow,
  FillTotals,
  HistoryFilters,
  HistoryTab,
  PositionPage,
  PositionRow,
} from './types'
import { defaultHistoryFilters } from './types'

const PAGE_SIZE = 50

export function buildFillQuery(
  filters: HistoryFilters,
  now: Date,
  cursor: { before: string; beforeId: string } | null,
): string {
  const params = new URLSearchParams()
  if (filters.botId) params.set('bot_id', filters.botId)
  if (filters.side) params.set('side', filters.side)
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

export function buildPositionQuery(filters: HistoryFilters, offset: number): string {
  const params = new URLSearchParams()
  params.append('status', 'open')
  if (filters.showClosed) params.append('status', 'closed')
  if (filters.botId) params.set('bot_id', filters.botId)
  params.set('limit', String(PAGE_SIZE))
  if (offset > 0) params.set('offset', String(offset))
  return params.toString()
}

/** The trade history of the workspace: fills (newest first, older pages on request, totals over
 * everything the filters match) and positions. A newer request replaces one still in flight. */
export function useTradeHistory(workspaceId: string) {
  const [tab, setTab] = useState<HistoryTab>('fills')
  const [filters, setFilters] = useState<HistoryFilters>(defaultHistoryFilters)
  const [fills, setFills] = useState<FillRow[]>([])
  const [totals, setTotals] = useState<FillTotals[]>([])
  const [cursor, setCursor] = useState<{ before: string; beforeId: string } | null>(null)
  const [positions, setPositions] = useState<PositionRow[]>([])
  const [positionTotal, setPositionTotal] = useState(0)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  const [loadedAt, setLoadedAt] = useState<Date | null>(null)
  const generation = useRef(0)
  const startedAt = useRef(new Date())

  const loadFills = useCallback(
    async (id: string, current: HistoryFilters, at: Date, from: typeof cursor, append: boolean) => {
      const mine = ++generation.current
      setLoading(true)
      try {
        const response = await apiFetch(
          `${apiBaseUrl}/api/v1/workspaces/${id}/fills?${buildFillQuery(current, at, from)}`,
        )
        if (mine !== generation.current) return
        if (!response.ok) {
          setError(await apiErrorMessage(response, '約定の履歴を取得できませんでした'))
          return
        }
        const page = (await response.json()) as FillPage
        setFills((existing) => (append ? [...existing, ...page.items] : page.items))
        setTotals(page.totals)
        setCursor(
          page.next_before && page.next_before_id
            ? { before: page.next_before, beforeId: page.next_before_id }
            : null,
        )
        setError('')
        setLoadedAt(new Date())
      } catch {
        if (mine === generation.current) setError('取引履歴APIへ接続できません。')
      } finally {
        if (mine === generation.current) setLoading(false)
      }
    },
    [],
  )

  const loadPositions = useCallback(async (id: string, current: HistoryFilters, offset: number) => {
    const mine = ++generation.current
    setLoading(true)
    try {
      const response = await apiFetch(
        `${apiBaseUrl}/api/v1/workspaces/${id}/position-history?${buildPositionQuery(current, offset)}`,
      )
      if (mine !== generation.current) return
      if (!response.ok) {
        setError(await apiErrorMessage(response, '建玉の履歴を取得できませんでした'))
        return
      }
      const page = (await response.json()) as PositionPage
      setPositions((existing) => (offset > 0 ? [...existing, ...page.items] : page.items))
      setPositionTotal(page.total)
      setError('')
      setLoadedAt(new Date())
    } catch {
      if (mine === generation.current) setError('取引履歴APIへ接続できません。')
    } finally {
      if (mine === generation.current) setLoading(false)
    }
  }, [])

  const reload = useCallback(() => {
    if (!workspaceId) return
    startedAt.current = new Date()
    if (tab === 'fills') void loadFills(workspaceId, filters, startedAt.current, null, false)
    else void loadPositions(workspaceId, filters, 0)
  }, [workspaceId, tab, filters, loadFills, loadPositions])

  // Load again when the workspace, the tab or a filter changes.
  useEffect(() => {
    if (!workspaceId) return
    const timer = window.setTimeout(reload, 0)
    return () => window.clearTimeout(timer)
  }, [workspaceId, reload])

  const loadMore = () => {
    if (!workspaceId) return
    if (tab === 'fills' && cursor) void loadFills(workspaceId, filters, startedAt.current, cursor, true)
    if (tab === 'positions') void loadPositions(workspaceId, filters, positions.length)
  }

  return {
    tab,
    setTab,
    filters,
    setFilters,
    fills,
    totals,
    positions,
    positionTotal,
    hasMore: tab === 'fills' ? cursor !== null : positions.length < positionTotal,
    loading,
    error,
    loadedAt,
    loadMore,
    reload,
  }
}
