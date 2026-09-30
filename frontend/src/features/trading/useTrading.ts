import { useCallback, useEffect, useRef, useState } from 'react'
import { apiBaseUrl, apiErrorMessage, apiFetch } from '../../lib/api'
import type { Timeframe } from '../market-data/types'
import type { BotRunSummary, TradingAccount, TradingBot } from './types'

/** `selectedWorkspaceId` is shared across features (see useWorkspaces); this hook
 * receives it rather than owning it, matching useConnections/useInstruments.
 *
 * Polls bots + each bot's latest-run on a 5s interval (mirrors
 * useMarketData.ts's own polling shape) whenever a workspace is selected --
 * regardless of which page is currently visible, matching this codebase's
 * existing pattern where every feature hook is instantiated unconditionally in
 * App.tsx. `latestRuns` is a lookup by bot id since `GET .../latest-run` is a
 * per-bot endpoint, not a list. */
export function useTrading(selectedWorkspaceId: string) {
  const [tradingAccounts, setTradingAccounts] = useState<TradingAccount[]>([])
  const [bots, setBots] = useState<TradingBot[]>([])
  const [latestRuns, setLatestRuns] = useState<Record<string, BotRunSummary>>({})
  const [tradingMessage, setTradingMessage] = useState('')

  const [accountConnectionId, setAccountConnectionId] = useState('')
  const [accountBaseCurrency, setAccountBaseCurrency] = useState('JPY')
  const [depositAccountId, setDepositAccountId] = useState('')
  const [depositAmount, setDepositAmount] = useState('')
  const [depositAsset, setDepositAsset] = useState('JPY')

  const [botName, setBotName] = useState('')
  const [botAccountId, setBotAccountId] = useState('')
  const [botInstrumentId, setBotInstrumentId] = useState('')
  const [botTimeframe, setBotTimeframe] = useState<Timeframe>('1m')

  const generation = useRef(0)

  const loadLatestRuns = useCallback(async (workspaceId: string, currentBots: TradingBot[]) => {
    const entries = await Promise.all(
      currentBots.map(async (bot) => {
        try {
          const response = await apiFetch(
            `${apiBaseUrl}/api/v1/workspaces/${workspaceId}/bots/${bot.id}/latest-run`,
          )
          if (!response.ok) return null
          return [bot.id, (await response.json()) as BotRunSummary] as const
        } catch {
          return null
        }
      }),
    )
    return Object.fromEntries(entries.filter((entry) => entry !== null))
  }, [])

  const load = useCallback(async (workspaceId: string) => {
    const myGeneration = ++generation.current
    if (!workspaceId) {
      setTradingAccounts([])
      setBots([])
      setLatestRuns({})
      return
    }
    try {
      const [accountsResponse, botsResponse] = await Promise.all([
        apiFetch(`${apiBaseUrl}/api/v1/workspaces/${workspaceId}/trading-accounts`),
        apiFetch(`${apiBaseUrl}/api/v1/workspaces/${workspaceId}/bots`),
      ])
      if (myGeneration !== generation.current) return
      if (accountsResponse.ok) setTradingAccounts((await accountsResponse.json()) as TradingAccount[])
      if (botsResponse.ok) {
        const loadedBots = (await botsResponse.json()) as TradingBot[]
        setBots(loadedBots)
        const runs = await loadLatestRuns(workspaceId, loadedBots)
        if (myGeneration === generation.current) setLatestRuns(runs)
      }
    } catch {
      if (myGeneration === generation.current) setTradingMessage('取引口座・Bot一覧APIへ接続できません。')
    }
  }, [loadLatestRuns])

  useEffect(() => {
    if (!selectedWorkspaceId) return
    // Deferred by one tick (mirrors useMarketData.ts's own initial-load effect):
    // calling load's setState synchronously in the effect body triggers an
    // avoidable extra render.
    const initialLoad = window.setTimeout(() => void load(selectedWorkspaceId), 0)
    const timer = window.setInterval(() => void load(selectedWorkspaceId), 5000)
    return () => {
      window.clearTimeout(initialLoad)
      window.clearInterval(timer)
    }
  }, [load, selectedWorkspaceId])

  const createTradingAccount = async () => {
    if (!selectedWorkspaceId || !accountConnectionId || !accountBaseCurrency.trim()) return
    try {
      const response = await apiFetch(
        `${apiBaseUrl}/api/v1/workspaces/${selectedWorkspaceId}/trading-accounts`,
        {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            connection_id: accountConnectionId,
            base_currency: accountBaseCurrency.trim(),
          }),
        },
      )
      setTradingMessage(
        response.ok ? '取引口座を作成しました。' : await apiErrorMessage(response, '取引口座の作成に失敗しました'),
      )
      if (response.ok) setAccountBaseCurrency('JPY')
      await load(selectedWorkspaceId)
    } catch {
      setTradingMessage('取引口座作成APIへ接続できません。')
    }
  }

  const createDeposit = async () => {
    if (!selectedWorkspaceId || !depositAccountId || !depositAmount || !depositAsset.trim()) return
    try {
      const response = await apiFetch(
        `${apiBaseUrl}/api/v1/workspaces/${selectedWorkspaceId}/trading-accounts/${depositAccountId}/deposits`,
        {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ amount: depositAmount, asset: depositAsset.trim() }),
        },
      )
      setTradingMessage(response.ok ? '入金を記録しました。' : await apiErrorMessage(response, '入金の記録に失敗しました'))
      if (response.ok) setDepositAmount('')
      await load(selectedWorkspaceId)
    } catch {
      setTradingMessage('入金APIへ接続できません。')
    }
  }

  const createBot = async () => {
    if (!selectedWorkspaceId || !botName.trim() || !botAccountId || !botInstrumentId) return
    try {
      const response = await apiFetch(`${apiBaseUrl}/api/v1/workspaces/${selectedWorkspaceId}/bots`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          name: botName.trim(),
          account_id: botAccountId,
          instrument_id: botInstrumentId,
          timeframe: botTimeframe,
        }),
      })
      setTradingMessage(response.ok ? 'Botを作成しました。' : await apiErrorMessage(response, 'Botの作成に失敗しました'))
      if (response.ok) setBotName('')
      await load(selectedWorkspaceId)
    } catch {
      setTradingMessage('Bot作成APIへ接続できません。')
    }
  }

  const runBotCommand = async (bot: TradingBot, command: 'start' | 'pause' | 'resume' | 'stop') => {
    if (!selectedWorkspaceId) return
    const label = { start: '開始', pause: '一時停止', resume: '再開', stop: '停止' }[command]
    try {
      const response = await apiFetch(
        `${apiBaseUrl}/api/v1/workspaces/${selectedWorkspaceId}/bots/${bot.id}/${command}`,
        { method: 'POST' },
      )
      setTradingMessage(
        response.ok
          ? `${bot.name}を${label}しました。`
          : await apiErrorMessage(response, `${bot.name}の${label}に失敗しました`),
      )
      await load(selectedWorkspaceId)
    } catch {
      setTradingMessage(`${label}APIへ接続できません。`)
    }
  }

  return {
    tradingAccounts,
    bots,
    latestRuns,
    tradingMessage,
    accountConnectionId,
    setAccountConnectionId,
    accountBaseCurrency,
    setAccountBaseCurrency,
    depositAccountId,
    setDepositAccountId,
    depositAmount,
    setDepositAmount,
    depositAsset,
    setDepositAsset,
    botName,
    setBotName,
    botAccountId,
    setBotAccountId,
    botInstrumentId,
    setBotInstrumentId,
    botTimeframe,
    setBotTimeframe,
    load,
    createTradingAccount,
    createDeposit,
    createBot,
    runBotCommand,
  }
}
