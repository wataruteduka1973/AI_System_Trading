import { useCallback, useEffect, useState } from 'react'
import { apiBaseUrl, apiErrorMessage, apiFetch } from '../../lib/api'
import type { Timeframe } from '../market-data/types'
import type { BacktestRun, BacktestTrade } from './types'

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
  const [selectedRunTrades, setSelectedRunTrades] = useState<
    { runId: string; trades: BacktestTrade[] } | null
  >(null)

  const [instrumentId, setInstrumentId] = useState('')
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
    const timer = window.setTimeout(() => void load(selectedWorkspaceId), 0)
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

  const loadTrades = async (run: BacktestRun) => {
    if (!selectedWorkspaceId) return
    try {
      const response = await apiFetch(
        `${apiBaseUrl}/api/v1/workspaces/${selectedWorkspaceId}/backtests/${run.id}/trades`,
      )
      if (response.ok) {
        setSelectedRunTrades({ runId: run.id, trades: (await response.json()) as BacktestTrade[] })
      }
    } catch {
      setBacktestMessage('取引一覧APIへ接続できません。')
    }
  }

  return {
    backtests,
    backtestMessage,
    selectedRunTrades,
    instrumentId,
    setInstrumentId,
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
    loadTrades,
  }
}
