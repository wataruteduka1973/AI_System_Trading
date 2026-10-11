import { useCallback, useEffect, useRef, useState } from 'react'
import { apiBaseUrl, apiErrorMessage, apiFetch } from '../../lib/api'
import type { GapFilters, GapPage, GapSummary, MarketDataGap } from './types'
import { emptyGapFilters } from './types'

const PAGE_SIZE = 50
const ACTIVE_POLL_MS = 5000
const IDLE_POLL_MS = 60000

export function buildGapQuery(filters: GapFilters, offset: number): string {
  const params = new URLSearchParams()
  params.append('status', 'open')
  if (filters.showIgnored) params.append('status', 'ignored')
  if (filters.showResolved) params.append('status', 'resolved')
  if (filters.instrumentId) params.set('instrument_id', filters.instrumentId)
  if (filters.timeframe) params.set('timeframe', filters.timeframe)
  params.set('limit', String(PAGE_SIZE))
  if (offset > 0) params.set('offset', String(offset))
  return params.toString()
}

const jobIsActive = (gap: MarketDataGap) =>
  gap.latest_job !== null && (gap.latest_job.status === 'queued' || gap.latest_job.status === 'running')

/** The missing-candle report: a per-series summary, the gaps (open ones; ignored and resolved on
 * request) and the action to fill one. It polls fast while a fill is running -- the progress and
 * the re-validation arrive that way -- and slowly otherwise. */
export function useDataGaps(workspaceId: string) {
  const [filters, setFilters] = useState<GapFilters>(emptyGapFilters)
  const [summary, setSummary] = useState<GapSummary[]>([])
  const [gaps, setGaps] = useState<MarketDataGap[]>([])
  const [total, setTotal] = useState(0)
  const [message, setMessage] = useState('')
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(false)
  const [filling, setFilling] = useState<string | null>(null)
  const generation = useRef(0)
  const loadedCount = useRef(PAGE_SIZE)

  /** Reloads the first `count` gaps (so a "load more" survives the refresh) and the summary. */
  const load = useCallback(
    async (id: string, current: GapFilters, count: number) => {
      const mine = ++generation.current
      setLoading(true)
      try {
        const base = `${apiBaseUrl}/api/v1/workspaces/${id}/market-data-gaps`
        const pages: MarketDataGap[] = []
        let totalCount = 0
        for (let offset = 0; offset < count; offset += PAGE_SIZE) {
          const response = await apiFetch(`${base}?${buildGapQuery(current, offset)}`)
          if (mine !== generation.current) return
          if (!response.ok) {
            setError(await apiErrorMessage(response, '欠損データを取得できませんでした'))
            return
          }
          const page = (await response.json()) as GapPage
          pages.push(...page.items)
          totalCount = page.total
          if (pages.length >= totalCount) break
        }
        const summaryResponse = await apiFetch(`${base}/summary`)
        if (mine !== generation.current) return
        setGaps(pages)
        setTotal(totalCount)
        if (summaryResponse.ok) setSummary((await summaryResponse.json()) as GapSummary[])
        setError('')
      } catch {
        if (mine === generation.current) setError('欠損データAPIへ接続できません。')
      } finally {
        if (mine === generation.current) setLoading(false)
      }
    },
    [],
  )

  const reload = useCallback(() => {
    if (workspaceId) void load(workspaceId, filters, loadedCount.current)
  }, [load, workspaceId, filters])

  // A new filter starts again from the first page.
  useEffect(() => {
    if (!workspaceId) return
    loadedCount.current = PAGE_SIZE
    const timer = window.setTimeout(() => void load(workspaceId, filters, PAGE_SIZE), 0)
    return () => window.clearTimeout(timer)
  }, [workspaceId, filters, load])

  const anyActive = gaps.some(jobIsActive)
  useEffect(() => {
    if (!workspaceId) return
    const timer = window.setInterval(
      () => void load(workspaceId, filters, loadedCount.current),
      anyActive ? ACTIVE_POLL_MS : IDLE_POLL_MS,
    )
    return () => window.clearInterval(timer)
  }, [workspaceId, filters, load, anyActive])

  const loadMore = () => {
    loadedCount.current += PAGE_SIZE
    reload()
  }

  const fill = async (gap: MarketDataGap) => {
    if (!workspaceId) return
    setFilling(gap.id)
    try {
      const response = await apiFetch(
        `${apiBaseUrl}/api/v1/workspaces/${workspaceId}/market-data-gaps/${gap.id}/backfill`,
        { method: 'POST' },
      )
      setMessage(
        response.ok
          ? `${gap.symbol} ${gap.timeframe} の欠損の補完を登録しました。取得が終わると再検証の結果が出ます。`
          : await apiErrorMessage(response, '補完を登録できませんでした'),
      )
      reload()
    } catch {
      setMessage('補完APIへ接続できません。')
    } finally {
      setFilling(null)
    }
  }

  return {
    filters,
    setFilters,
    summary,
    gaps,
    total,
    hasMore: gaps.length < total,
    message,
    error,
    loading,
    filling,
    loadMore,
    reload,
    fill,
  }
}
