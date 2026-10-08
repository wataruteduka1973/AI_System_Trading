import { useCallback, useEffect, useState } from 'react'
import { apiBaseUrl, apiErrorMessage, apiFetch } from '../../lib/api'
import type { Timeframe } from '../market-data/types'
import type {
  BacktestBatchResponse,
  BacktestRun,
  BacktestRunDetail,
  BacktestTrade,
  EquityCurve,
} from './types'

/** The server runs at most this many instruments in one batch (`MAX_BATCH_INSTRUMENTS`). */
export const MAX_BATCH_INSTRUMENTS = 12

/** `selectedWorkspaceId` is shared across features (see useWorkspaces); this hook
 * receives it rather than owning it, matching useConnections/useTrading.
 *
 * Unlike useTrading's bots, a backtest run completes synchronously inside the
 * `POST` response (see app/trading/application/backtest_provisioning.py's own
 * docstring: there is no job queue in this codebase) -- there is nothing to
 * poll for, so this hook loads the list once per workspace selection (mirrors
 * useInstruments.ts's shape) plus after creating a run, not on an interval. */
export function useBacktests(selectedWorkspaceId: string) {
  const [backtests, setBacktests] = useState<BacktestRun[]>([])
  const [backtestMessage, setBacktestMessage] = useState('')
  const [selectedRunDetail, setSelectedRunDetail] = useState<BacktestRunDetail | null>(null)

  const [instrumentId, setInstrumentId] = useState('')
  const [batchMode, setBatchMode] = useState(false)
  const [batchInstrumentIds, setBatchInstrumentIds] = useState<string[]>([])
  const [batchResult, setBatchResult] = useState<BacktestBatchResponse | null>(null)
  const [timeframe, setTimeframe] = useState<Timeframe>('1m')
  const [fromTime, setFromTime] = useState('')
  const [toTime, setToTime] = useState('')
  const [initialEquity, setInitialEquity] = useState('10000')
  const [spread, setSpread] = useState('0')
  const [walkForward, setWalkForward] = useState(false)
  const [trainRatio, setTrainRatio] = useState('0.7')

  const load = useCallback(async (workspaceId: string) => {
    if (!workspaceId) {
      setBacktests([])
      return
    }
    try {
      const response = await apiFetch(`${apiBaseUrl}/api/v1/workspaces/${workspaceId}/backtests`)
      if (response.ok) setBacktests((await response.json()) as BacktestRun[])
    } catch {
      setBacktestMessage('バックテスト一覧APIへ接続できません。')
    }
  }, [])

  useEffect(() => {
    if (!selectedWorkspaceId) return
    // Deferred by one tick (mirrors useMarketData.ts's own initial-load effect):
    // calling load's setState synchronously in the effect body triggers an
    // avoidable extra render.
    const timer = window.setTimeout(() => {
      void load(selectedWorkspaceId)
    }, 0)
    return () => window.clearTimeout(timer)
  }, [load, selectedWorkspaceId])

  const createBacktest = async () => {
    if (!selectedWorkspaceId || !instrumentId || !fromTime || !toTime || !initialEquity) return
    setBacktestMessage('バックテストを実行しています…')
    try {
      const response = await apiFetch(
        `${apiBaseUrl}/api/v1/workspaces/${selectedWorkspaceId}/backtests`,
        {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            instrument_id: instrumentId,
            timeframe,
            from_time: new Date(fromTime).toISOString(),
            to_time: new Date(toTime).toISOString(),
            initial_equity: initialEquity,
            spread,
            mode: walkForward ? 'walk_forward' : 'single',
            train_ratio: trainRatio,
          }),
        },
      )
      setBacktestMessage(
        response.ok
          ? 'バックテストが完了しました。'
          : await apiErrorMessage(response, 'バックテストの実行に失敗しました'),
      )
      await load(selectedWorkspaceId)
    } catch {
      setBacktestMessage('バックテストAPIへ接続できません。')
    }
  }

  const toggleBatchInstrument = (id: string) =>
    setBatchInstrumentIds((current) =>
      current.includes(id)
        ? current.filter((entry) => entry !== id)
        : current.length < MAX_BATCH_INSTRUMENTS
          ? [...current, id]
          : current,
    )

  /** One request for several instruments; the server refuses the whole batch when the bars add up
   * to more than its budget, and says which instruments are how large. */
  const createBatch = async () => {
    if (!selectedWorkspaceId || batchInstrumentIds.length === 0 || !fromTime || !toTime || !initialEquity)
      return
    setBacktestMessage(`${batchInstrumentIds.length}銘柄のバックテストを実行しています…`)
    setBatchResult(null)
    try {
      const response = await apiFetch(
        `${apiBaseUrl}/api/v1/workspaces/${selectedWorkspaceId}/backtests/batch`,
        {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            instrument_ids: batchInstrumentIds,
            timeframe,
            from_time: new Date(fromTime).toISOString(),
            to_time: new Date(toTime).toISOString(),
            initial_equity: initialEquity,
            spread,
            mode: walkForward ? 'walk_forward' : 'single',
            train_ratio: trainRatio,
          }),
        },
      )
      if (!response.ok) {
        setBacktestMessage(await apiErrorMessage(response, '複数銘柄のバックテストを実行できませんでした'))
        return
      }
      const result = (await response.json()) as BacktestBatchResponse
      setBatchResult(result)
      const failed = result.items.filter((item) => item.error_code !== null).length
      setBacktestMessage(
        failed === 0
          ? `${result.items.length}銘柄のバックテストが完了しました。`
          : `${result.items.length}銘柄のうち${failed}銘柄は実行できませんでした(下の表に理由を出しています)。`,
      )
      await load(selectedWorkspaceId)
    } catch {
      setBacktestMessage('バックテストAPIへ接続できません。')
    }
  }

  const loadRunDetail = async (run: BacktestRun) => {
    if (!selectedWorkspaceId) return
    const base = `${apiBaseUrl}/api/v1/workspaces/${selectedWorkspaceId}/backtests/${run.id}`
    try {
      const [tradesResponse, curveResponse] = await Promise.all([
        apiFetch(`${base}/trades`),
        apiFetch(`${base}/equity-curve`),
      ])
      if (!tradesResponse.ok) {
        setBacktestMessage(await apiErrorMessage(tradesResponse, '取引一覧を取得できませんでした'))
        return
      }
      const emptyCurve: EquityCurve = { initial_equity: null, points: [] }
      setSelectedRunDetail({
        runId: run.id,
        trades: (await tradesResponse.json()) as BacktestTrade[],
        equityCurve: curveResponse.ok ? ((await curveResponse.json()) as EquityCurve) : emptyCurve,
      })
    } catch {
      setBacktestMessage('バックテスト詳細APIへ接続できません。')
    }
  }

  return {
    backtests,
    backtestMessage,
    selectedRunDetail,
    instrumentId,
    setInstrumentId,
    batchMode,
    setBatchMode,
    batchInstrumentIds,
    toggleBatchInstrument,
    batchResult,
    createBatch,
    timeframe,
    setTimeframe,
    fromTime,
    setFromTime,
    toTime,
    setToTime,
    initialEquity,
    setInitialEquity,
    spread,
    setSpread,
    walkForward,
    setWalkForward,
    trainRatio,
    setTrainRatio,
    load,
    createBacktest,
    loadRunDetail,
  }
}
