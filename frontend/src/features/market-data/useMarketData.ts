import { useCallback, useEffect, useRef, useState } from 'react'
import type { DisplayedRange } from '../../components/CandleChart'
import { mergeCandlePages, type ChartCandle } from '../../components/marketData'
import { apiBaseUrl, apiErrorMessage, apiFetch } from '../../lib/api'
import type { BackfillJob, CandleCoverage, MarketDataSubscription, Timeframe } from './types'

export function useMarketData(
  selectedWorkspaceId: string,
  activeInstrumentId: string,
) {
  const [timeframe, setTimeframe] = useState<Timeframe>('1m')
  const [candles, setCandles] = useState<ChartCandle[]>([])
  const [marketDataLoading, setMarketDataLoading] = useState(false)
  const [olderCandlesLoading, setOlderCandlesLoading] = useState(false)
  const [hasOlderCandles, setHasOlderCandles] = useState(true)
  const [candleError, setCandleError] = useState<string | null>(null)
  const [displayedRange, setDisplayedRange] = useState<DisplayedRange>(null)
  const [backfillJobs, setBackfillJobs] = useState<BackfillJob[]>([])
  const [subscriptions, setSubscriptions] = useState<MarketDataSubscription[]>([])
  const [coverage, setCoverage] = useState<CandleCoverage | null>(null)
  const marketGeneration = useRef(0)
  const marketRequest = useRef(0)
  const submissionPending = useRef(false)
  const [submittingMarketAction, setSubmittingMarketAction] = useState(false)
  const [marketDataMessage, setMarketDataMessage] = useState(
    '銘柄と時間足を選ぶと、確定済みローソク足を表示できます。',
  )

  const loadMarketData = useCallback(async (
    workspaceId: string,
    instrumentId: string,
    frame: Timeframe,
    replaceCandles = false,
  ) => {
    if (!workspaceId || !instrumentId) return
    const generation = marketGeneration.current
    const request = ++marketRequest.current
    if (replaceCandles) setMarketDataLoading(true)
    try {
      const [candleResponse, jobResponse, subscriptionResponse, coverageResponse] = await Promise.all([
        apiFetch(`${apiBaseUrl}/api/v1/workspaces/${workspaceId}/instruments/${instrumentId}/candles?timeframe=${frame}&limit=500`),
        apiFetch(`${apiBaseUrl}/api/v1/workspaces/${workspaceId}/candle-backfills?instrument_id=${instrumentId}&timeframe=${frame}&limit=10`),
        apiFetch(`${apiBaseUrl}/api/v1/workspaces/${workspaceId}/market-data-subscriptions`),
        apiFetch(`${apiBaseUrl}/api/v1/workspaces/${workspaceId}/instruments/${instrumentId}/candle-coverage?timeframe=${frame}`),
      ])
      const [loaded, jobs, feeds, report] = await Promise.all([
        candleResponse.ok ? candleResponse.json() as Promise<ChartCandle[]> : null,
        jobResponse.ok ? jobResponse.json() as Promise<BackfillJob[]> : null,
        subscriptionResponse.ok ? subscriptionResponse.json() as Promise<MarketDataSubscription[]> : null,
        coverageResponse.ok ? coverageResponse.json() as Promise<CandleCoverage> : null,
      ])
      if (generation !== marketGeneration.current || request !== marketRequest.current) return
      if (loaded !== null) {
        setCandles((current) => replaceCandles ? loaded : mergeCandlePages(current, loaded))
        setHasOlderCandles(loaded.length === 500)
        setCandleError(null)
      } else {
        setCandleError(`ローソク足の取得に失敗しました（HTTP ${candleResponse.status}）。`)
      }
      if (jobs !== null) setBackfillJobs(jobs.filter((job) => job.timeframe === frame))
      if (feeds !== null) setSubscriptions(feeds)
      if (report !== null) setCoverage(report)
    } catch {
      if (generation !== marketGeneration.current || request !== marketRequest.current) return
      setMarketDataMessage('ローソク足APIへ接続できません。')
      setCandleError('ローソク足APIへ接続できません。')
    } finally {
      if (replaceCandles && generation === marketGeneration.current) setMarketDataLoading(false)
    }
  }, [])

  const loadOlderCandles = useCallback(async () => {
    if (
      !selectedWorkspaceId || !activeInstrumentId ||
      olderCandlesLoading || !hasOlderCandles || candles.length === 0
    ) return
    const generation = marketGeneration.current
    setOlderCandlesLoading(true)
    setCandleError(null)
    try {
      const before = encodeURIComponent(candles[0].open_time)
      const response = await apiFetch(
        `${apiBaseUrl}/api/v1/workspaces/${selectedWorkspaceId}/instruments/${activeInstrumentId}/candles?timeframe=${timeframe}&limit=500&before=${before}`,
      )
      if (generation !== marketGeneration.current) return
      if (!response.ok) {
        setCandleError(`古いローソク足の取得に失敗しました（HTTP ${response.status}）。`)
        return
      }
      const loaded = (await response.json()) as ChartCandle[]
      if (generation !== marketGeneration.current) return
      setCandles((current) => mergeCandlePages(current, loaded))
      setHasOlderCandles(loaded.length === 500)
    } catch {
      if (generation !== marketGeneration.current) return
      setCandleError('古いローソク足を取得できません。')
    } finally {
      if (generation === marketGeneration.current) setOlderCandlesLoading(false)
    }
  }, [
    activeInstrumentId,
    candles,
    hasOlderCandles,
    olderCandlesLoading,
    selectedWorkspaceId,
    timeframe,
  ])

  const setAutomaticCollection = async (enabled: boolean) => {
    if (!selectedWorkspaceId || !activeInstrumentId || submissionPending.current) return
    submissionPending.current = true
    setSubmittingMarketAction(true)
    const generation = marketGeneration.current
    try {
      const response = await apiFetch(
        `${apiBaseUrl}/api/v1/workspaces/${selectedWorkspaceId}/market-data-subscriptions`,
        {
          method: 'PUT',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ instrument_id: activeInstrumentId, enabled }),
        },
      )
      if (generation !== marketGeneration.current) return
      const message = response.ok
        ? enabled
          ? 'この銘柄の全時間足の自動取得を開始しました。'
          : 'この銘柄の全時間足の自動取得を停止しました。実行中の取得・手動の過去取得は別です。'
        : await apiErrorMessage(response, '自動取得設定を変更できませんでした')
      if (generation === marketGeneration.current) setMarketDataMessage(message)
    } catch {
      if (generation === marketGeneration.current) setMarketDataMessage('自動取得設定APIへ接続できません。')
    } finally {
      if (generation === marketGeneration.current) await loadMarketData(selectedWorkspaceId, activeInstrumentId, timeframe)
      submissionPending.current = false
      setSubmittingMarketAction(false)
    }
  }

  useEffect(() => {
    if (!selectedWorkspaceId || !activeInstrumentId) return
    const initialLoad = window.setTimeout(() => {
      setCandles([])
      setCoverage(null)
      setBackfillJobs([])
      setOlderCandlesLoading(false)
      setDisplayedRange(null)
      setHasOlderCandles(true)
      setCandleError(null)
      void loadMarketData(selectedWorkspaceId, activeInstrumentId, timeframe, true)
    }, 0)
    const timer = window.setInterval(() => {
      void loadMarketData(selectedWorkspaceId, activeInstrumentId, timeframe)
    }, 5000)
    return () => {
      marketGeneration.current += 1
      window.clearTimeout(initialLoad)
      window.clearInterval(timer)
    }
  }, [loadMarketData, selectedWorkspaceId, activeInstrumentId, timeframe])

  const reloadMarketData = useCallback(() => {
    if (!selectedWorkspaceId || !activeInstrumentId) return
    void loadMarketData(selectedWorkspaceId, activeInstrumentId, timeframe, true)
  }, [loadMarketData, selectedWorkspaceId, activeInstrumentId, timeframe])

  return {
    timeframe,
    setTimeframe,
    candles,
    marketDataLoading,
    olderCandlesLoading,
    hasOlderCandles,
    candleError,
    displayedRange,
    setDisplayedRange,
    backfillJobs,
    subscriptions,
    coverage,
    submittingMarketAction,
    marketDataMessage,
    loadOlderCandles,
    setAutomaticCollection,
    reloadMarketData,
  }
}
