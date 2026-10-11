import { useCallback, useEffect, useRef, useState } from 'react'
import { apiBaseUrl, apiFetch } from '../../lib/api'
import type { FillPage, FillRow, PositionPage, PositionRow } from '../trade-history/types'

const PAGE_SIZE = 200
const MAX_PAGES = 3
const POLL_MS = 30000

/** The workspace's own fills and positions on one instrument, for the chart to draw over the
 * candles. Empty (and nothing is requested) while `enabled` is false -- only the market pages ask.
 * A failed refresh keeps what the chart already shows: a missing overlay is not worth an error
 * over a working chart. */
export function useChartTrades(workspaceId: string, instrumentId: string, enabled: boolean) {
  const [fills, setFills] = useState<FillRow[]>([])
  const [positions, setPositions] = useState<PositionRow[]>([])
  const generation = useRef(0)

  const load = useCallback(async (workspace: string, instrument: string) => {
    const mine = ++generation.current
    try {
      const base = `${apiBaseUrl}/api/v1/workspaces/${workspace}`
      const collected: FillRow[] = []
      let cursor = ''
      for (let page = 0; page < MAX_PAGES; page += 1) {
        const response = await apiFetch(
          `${base}/fills?instrument_id=${instrument}&limit=${PAGE_SIZE}${cursor}`,
        )
        if (mine !== generation.current || !response.ok) return
        const body = (await response.json()) as FillPage
        collected.push(...body.items)
        if (!body.next_before || !body.next_before_id) break
        cursor = `&before=${encodeURIComponent(body.next_before)}&before_id=${body.next_before_id}`
      }
      const positionResponse = await apiFetch(
        `${base}/position-history?instrument_id=${instrument}&status=open&status=closed&limit=${PAGE_SIZE}`,
      )
      if (mine !== generation.current || !positionResponse.ok) return
      const positionPage = (await positionResponse.json()) as PositionPage
      setFills(collected)
      setPositions(positionPage.items)
    } catch {
      // keep the last overlay
    }
  }, [])

  useEffect(() => {
    if (!enabled || !workspaceId || !instrumentId) {
      generation.current += 1
      const clear = window.setTimeout(() => {
        setFills([])
        setPositions([])
      }, 0)
      return () => window.clearTimeout(clear)
    }
    const first = window.setTimeout(() => {
      setFills([])
      setPositions([])
      void load(workspaceId, instrumentId)
    }, 0)
    const timer = window.setInterval(() => void load(workspaceId, instrumentId), POLL_MS)
    return () => {
      window.clearTimeout(first)
      window.clearInterval(timer)
    }
  }, [enabled, workspaceId, instrumentId, load])

  return { fills, positions }
}
