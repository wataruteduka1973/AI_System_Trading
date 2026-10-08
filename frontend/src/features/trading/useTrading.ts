import { useCallback, useEffect, useRef, useState } from 'react'
import { apiBaseUrl, apiErrorMessage, apiFetch } from '../../lib/api'
import { releasePath } from './haltLabels'
import type {
  BotRunSummary,
  EmergencyStopResult,
  TradingAccount,
  TradingBot,
  TradingHalt,
} from './types'

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
  const [halts, setHalts] = useState<TradingHalt[]>([])
  const [tradingMessage, setTradingMessage] = useState('')

  const [accountConnectionId, setAccountConnectionId] = useState('')
  const [accountBaseCurrency, setAccountBaseCurrency] = useState('JPY')
  const [depositAccountId, setDepositAccountId] = useState('')
  const [depositAmount, setDepositAmount] = useState('')
  const [depositAsset, setDepositAsset] = useState('JPY')

  const [botName, setBotName] = useState('')
  const [botAccountId, setBotAccountId] = useState('')
  const [botInstrumentId, setBotInstrumentId] = useState('')

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
      setHalts([])
      return
    }
    try {
      const [accountsResponse, botsResponse, haltsResponse] = await Promise.all([
        apiFetch(`${apiBaseUrl}/api/v1/workspaces/${workspaceId}/trading-accounts`),
        apiFetch(`${apiBaseUrl}/api/v1/workspaces/${workspaceId}/bots`),
        apiFetch(`${apiBaseUrl}/api/v1/workspaces/${workspaceId}/trading-halts`),
      ])
      if (myGeneration !== generation.current) return
      if (haltsResponse.ok) setHalts((await haltsResponse.json()) as TradingHalt[])
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

  const describeStop = (scopeLabel: string, result: EmergencyStopResult): string => {
    const problems =
      result.bot_stop_failures.length + result.close_failures.length
    const base = result.already_active
      ? `${scopeLabel}は既に緊急停止中です。`
      : `${scopeLabel}を緊急停止しました(停止したBot ${result.stopped_bot_ids.length}件)。`
    const closing = result.closing_order_ids.length > 0 ? ` 決済注文 ${result.closing_order_ids.length}件。` : ''
    return problems > 0 ? `${base}${closing} 一部を処理できませんでした(${problems}件)。` : `${base}${closing}`
  }

  const emergencyStop = async (bot: TradingBot | null, closePositions: boolean) => {
    if (!selectedWorkspaceId) return
    const scopeLabel = bot ? bot.name : 'ワークスペース全体'
    const path = bot ? `bots/${bot.id}/emergency-stop` : 'emergency-stop'
    try {
      const response = await apiFetch(
        `${apiBaseUrl}/api/v1/workspaces/${selectedWorkspaceId}/${path}`,
        {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ close_positions: closePositions }),
        },
      )
      setTradingMessage(
        response.ok
          ? describeStop(scopeLabel, (await response.json()) as EmergencyStopResult)
          : await apiErrorMessage(response, `${scopeLabel}の緊急停止に失敗しました`),
      )
      await load(selectedWorkspaceId)
    } catch {
      setTradingMessage('緊急停止APIへ接続できません。API停止時は scripts/emergency_stop.py を使ってください。')
    }
  }

  /** Owner-only on the server. An `emergency_stopped` halt has its own endpoint; the others go
   * through `/release` (a lock halt is released at once, the rest step down one level). */
  const releaseHalt = async (halt: TradingHalt) => {
    if (!selectedWorkspaceId) return
    const emergency = releasePath(halt) === 'emergency-release'
    try {
      const response = await apiFetch(
        `${apiBaseUrl}/api/v1/workspaces/${selectedWorkspaceId}/trading-halts/${halt.id}/${releasePath(halt)}`,
        { method: 'POST' },
      )
      setTradingMessage(
        response.ok
          ? emergency
            ? '緊急停止を解除しました。Botは停止したままなので、必要なら開始してください。'
            : '停止を解除しました(段階的に緩和する停止は、1段下がります)。'
          : await apiErrorMessage(response, '停止を解除できませんでした(Ownerのみ可能です)'),
      )
      await load(selectedWorkspaceId)
    } catch {
      setTradingMessage('停止解除APIへ接続できません。')
    }
  }

  return {
    tradingAccounts,
    bots,
    halts,
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
    load,
    createTradingAccount,
    createDeposit,
    createBot,
    runBotCommand,
    emergencyStop,
    releaseHalt,
  }
}
