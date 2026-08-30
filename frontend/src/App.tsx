import { useCallback, useEffect, useRef, useState } from 'react'
import { Route, Routes, useLocation, useNavigate } from 'react-router'
import AppShell from './app/AppShell'
import { connectionPath, resolveAppRoute } from './app/routes'
import ConnectionManagementPage from './pages/ConnectionManagementPage'
import ExchangeMarketPage from './pages/ExchangeMarketPage'
import HomePage from './pages/HomePage'
import NotFoundPage from './pages/NotFoundPage'
import CandleChart, { type DisplayedRange } from './components/CandleChart'
import { mergeCandlePages } from './components/marketData'
import './App.css'

type HealthState = {
  status: 'loading' | 'ok' | 'error'
  message: string
}

type WorkspaceSummary = {
  id: string
  name: string
  status: string
}

type ConnectionSummary = {
  id: string
  exchange_id: string
  label: string
  environment: string
  status: string
  credentials_status: 'saved' | 'missing'
  credentials_updated_at: string | null
  verification_outcome:
    | 'not_verified'
    | 'success'
    | 'authentication_failed'
    | 'communication_failed'
}

type WorkspaceAccount = {
  id: string
  exchange_code: 'oanda' | 'binance'
  connection_label: string
  connection_status: string
  account_ref_masked: string
  alias: string | null
  currency: string
  status: string
  selected: boolean
}

type WorkspaceInstrument = {
  id: string
  exchange_code: string
  market_code: string
  symbol: string
  quote_asset: string
  price_scale: number
  tick_size: string
  step_size: string
  min_quantity: string | null
  min_notional: string | null
  status: string
  rules_synced_at: string | null
}

type Timeframe = '1m' | '5m' | '15m' | '30m' | '1h' | '4h' | '1d'

type Candle = {
  open_time: string
  close_time: string
  open: string
  high: string
  low: string
  close: string
  volume: string | null
  source: string
  quality_status: string
}

type BackfillJob = {
  id: string
  instrument_id: string
  timeframe: string
  status: 'queued' | 'running' | 'succeeded' | 'failed'
  rows_written: number
  error_code: string | null
  created_at: string
}

type MarketDataSubscription = {
  id: string
  instrument_id: string
  timeframe: string
  enabled: boolean
  poll_interval_seconds: number
  last_polled_at: string | null
  last_success_at: string | null
  last_error_code: string | null
}

type CandleCoverage = {
  timeframe: Timeframe
  requested_from: string | null
  requested_to: string | null
  actual_from: string | null
  actual_to: string | null
  stored_count: number
  expected_count: number | null
  missing_count: number | null
  coverage_status: 'complete' | 'partial_source_limit' | 'partial_gaps' | 'empty' | 'checking'
  source_limitation: string | null
}

type OandaVerification = {
  status: string
  accounts: Array<{
    account_ref_masked: string
    alias: string | null
    currency: string
    usd_jpy_tradeable: boolean
  }>
}

type BinanceVerification = {
  status: string
  accounts: Array<{
    account_ref_masked: string
    account_type: string
    permissions: string[]
    can_trade: boolean
    nonzero_asset_count: number
    btc_jpy_tradeable: boolean
  }>
}

const apiBaseUrl = (import.meta.env.VITE_API_BASE_URL ?? 'http://localhost:8000').replace(
  /\/$/,
  '',
)

const checkEndpoint = async (path: string, okMessage: string): Promise<HealthState> => {
  try {
    const response = await fetch(`${apiBaseUrl}${path}`)
    if (!response.ok) {
      if (path.endsWith('/health/db') && response.status === 503) {
        return {
          status: 'error',
          message: 'FastAPIには接続できましたが、PostgreSQLの認証または接続に失敗しました。',
        }
      }
      return { status: 'error', message: `応答エラー: HTTP ${response.status}` }
    }
    return { status: 'ok', message: okMessage }
  } catch {
    return { status: 'error', message: 'サービスへ接続できません。' }
  }
}

const fetchHealth = () =>
  Promise.all([
    checkEndpoint('/api/v1/health', 'FastAPIは正常に稼働しています。'),
    checkEndpoint('/api/v1/health/db', 'PostgreSQLへ接続できています。'),
  ])

const apiErrorMessage = async (response: Response, fallback: string) => {
  try {
    const payload = (await response.json()) as { detail?: string }
    return payload.detail ? `${fallback}: ${payload.detail}（HTTP ${response.status}）` : fallback
  } catch {
    return `${fallback}（HTTP ${response.status}）`
  }
}

const verificationLabel = (outcome: ConnectionSummary['verification_outcome']) =>
  ({
    not_verified: '未検証',
    success: '認証成功',
    authentication_failed: '認証失敗',
    communication_failed: '通信失敗',
  })[outcome]

const connectionStatusLabel = (status: string) =>
  ({
    verified: '認証成功',
    verifying: '検証中（再検証が必要）',
    invalid: '認証または通信に失敗',
    disabled: '無効',
    pending_credentials: '資格情報待ち',
  })[status] ?? status

const marketErrorLabel = (code: string) => ({
  credentials_unreadable: '保存済み資格情報を復号できません。接続管理でAPI資格情報を更新し再検証してください。',
  credentials_missing: '資格情報が不足しています。接続管理でAPI資格情報を登録してください。',
  configuration_error: '接続設定を確認してください。暗号化キー変更後はAPI資格情報の再登録が必要です。',
  worker_interrupted: '前回の取得処理が中断されました。再取得できます。',
})[code] ?? code

function StatusCard({ title, state }: { title: string; state: HealthState }) {
  return (
    <article className={`status-card status-${state.status}`}>
      <div className="status-heading">
        <h2>{title}</h2>
        <span className="status-badge">{state.status}</span>
      </div>
      <p>{state.message}</p>
    </article>
  )
}

function App() {
  const location = useLocation()
  const navigate = useNavigate()
  const route = resolveAppRoute(location.pathname)
  const [apiHealth, setApiHealth] = useState<HealthState>({
    status: 'loading',
    message: 'FastAPIへ接続しています。',
  })
  const [dbHealth, setDbHealth] = useState<HealthState>({
    status: 'loading',
    message: 'PostgreSQL接続を確認しています。',
  })
  const [ownerToken, setOwnerToken] = useState('')
  const [workspaces, setWorkspaces] = useState<WorkspaceSummary[]>([])
  const [selectedWorkspaceId, setSelectedWorkspaceId] = useState(route.workspaceId ?? '')
  const [connections, setConnections] = useState<ConnectionSummary[]>([])
  const [workspaceAccounts, setWorkspaceAccounts] = useState<WorkspaceAccount[]>([])
  const [workspaceInstruments, setWorkspaceInstruments] = useState<WorkspaceInstrument[]>([])
  const [instrumentMessage, setInstrumentMessage] = useState(
    '利用口座を選択すると、取引所から最新の銘柄ルールを同期できます。',
  )
  const [workspaceMessage, setWorkspaceMessage] = useState(
    '開発用Owner tokenを入力してWorkspaceを読み込みます。',
  )
  const [connectionLabel, setConnectionLabel] = useState('OANDA practice')
  const [oandaToken, setOandaToken] = useState('')
  const [registrationMessage, setRegistrationMessage] = useState(
    'OANDA practice Tokenは登録後すぐに暗号化され、検証後に入力欄から消去されます。',
  )
  const [verifiedAccounts, setVerifiedAccounts] = useState<OandaVerification['accounts']>([])
  const [selectedOandaConnectionId, setSelectedOandaConnectionId] = useState('')
  const [binanceLabel, setBinanceLabel] = useState('Binance Spot Testnet')
  const [binanceApiKey, setBinanceApiKey] = useState('')
  const [binanceSecretKey, setBinanceSecretKey] = useState('')
  const [binanceMessage, setBinanceMessage] = useState(
    'API KeyとSecret Keyは暗号化保存され、検証後に入力欄から消去されます。',
  )
  const [binanceAccounts, setBinanceAccounts] = useState<BinanceVerification['accounts']>([])
  const [selectedBinanceConnectionId, setSelectedBinanceConnectionId] = useState('')
  const [selectedInstrumentId, setSelectedInstrumentId] = useState('')
  const [timeframe, setTimeframe] = useState<Timeframe>('1m')
  const [candles, setCandles] = useState<Candle[]>([])
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
  const visibleInstruments = route.kind === 'market'
    ? workspaceInstruments.filter((instrument) => instrument.exchange_code === route.exchange)
    : workspaceInstruments
  const activeInstrumentId = visibleInstruments.some(
    (instrument) => instrument.id === selectedInstrumentId,
  ) ? selectedInstrumentId : visibleInstruments[0]?.id ?? ''

  const loadHealth = useCallback(async () => {
    const [api, database] = await fetchHealth()
    setApiHealth(api)
    setDbHealth(database)
  }, [])

  const refreshHealth = () => {
    setApiHealth({ status: 'loading', message: 'FastAPIへ接続しています。' })
    setDbHealth({ status: 'loading', message: 'PostgreSQL接続を確認しています。' })
    void loadHealth()
  }

  const ownerHeaders = { 'X-Owner-Token': ownerToken }

  const loadWorkspaces = async () => {
    setWorkspaceMessage('Workspaceを読み込んでいます。')
    try {
      const response = await fetch(`${apiBaseUrl}/api/v1/workspaces`, {
        headers: ownerHeaders,
      })
      if (!response.ok) {
        setWorkspaceMessage(`認証または取得に失敗しました（HTTP ${response.status}）。`)
        return
      }
      const loaded = (await response.json()) as WorkspaceSummary[]
      setWorkspaces(loaded)
      const routedWorkspace = route.workspaceId
      if (routedWorkspace && loaded.some((workspace) => workspace.id === routedWorkspace)) {
        await loadConnections(routedWorkspace)
      } else {
        setSelectedWorkspaceId('')
        setWorkspaceMessage(
          loaded.length > 0 ? `${loaded.length}件のWorkspaceを取得しました。` : 'Workspaceは未登録です。',
        )
      }
    } catch {
      setWorkspaceMessage('Workspace APIへ接続できません。')
    }
  }

  const loadConnections = async (workspaceId: string) => {
    setSelectedWorkspaceId(workspaceId)
    setConnections([])
    setWorkspaceAccounts([])
    setWorkspaceInstruments([])
    setSelectedInstrumentId('')
    setCandles([])
    setBackfillJobs([])
    setSubscriptions([])
    setCoverage(null)
    setSelectedOandaConnectionId('')
    setSelectedBinanceConnectionId('')
    if (!workspaceId) return
    try {
      const response = await fetch(
        `${apiBaseUrl}/api/v1/workspaces/${workspaceId}/connections`,
        { headers: ownerHeaders },
      )
      if (!response.ok) {
        setWorkspaceMessage(`接続一覧の取得に失敗しました（HTTP ${response.status}）。`)
        return
      }
      const loaded = (await response.json()) as ConnectionSummary[]
      setConnections(loaded)
      const accountsResponse = await fetch(
        `${apiBaseUrl}/api/v1/workspaces/${workspaceId}/accounts`,
        { headers: ownerHeaders },
      )
      if (accountsResponse.ok) {
        setWorkspaceAccounts((await accountsResponse.json()) as WorkspaceAccount[])
      }
      const instrumentsResponse = await fetch(
        `${apiBaseUrl}/api/v1/workspaces/${workspaceId}/instruments`,
        { headers: ownerHeaders },
      )
      if (instrumentsResponse.ok) {
        const instruments = (await instrumentsResponse.json()) as WorkspaceInstrument[]
        setWorkspaceInstruments(instruments)
        setSelectedInstrumentId(instruments[0]?.id ?? '')
      }
      setWorkspaceMessage(`選択中のWorkspaceには${loaded.length}件の取引所接続があります。`)
    } catch {
      setWorkspaceMessage('接続一覧APIへ接続できません。')
    }
  }

  const selectWorkspace = async (workspaceId: string) => {
    await loadConnections(workspaceId)
    navigate(workspaceId ? connectionPath(workspaceId) : '/')
  }

  const syncInstruments = async () => {
    if (!selectedWorkspaceId) return
    setInstrumentMessage('選択済み口座から銘柄ルールを同期しています。')
    try {
      const response = await fetch(
        `${apiBaseUrl}/api/v1/workspaces/${selectedWorkspaceId}/instruments/sync`,
        { method: 'POST', headers: ownerHeaders },
      )
      if (!response.ok) {
        setInstrumentMessage(await apiErrorMessage(response, '銘柄ルールの同期に失敗しました'))
        return
      }
      const payload = (await response.json()) as { instruments: WorkspaceInstrument[] }
      setWorkspaceInstruments(payload.instruments)
      setSelectedInstrumentId((current) => current || payload.instruments[0]?.id || '')
      setInstrumentMessage(`${payload.instruments.length}件の銘柄ルールを同期しました。`)
    } catch {
      setInstrumentMessage('取引所または銘柄同期APIへ接続できません。')
    }
  }

  const loadMarketData = useCallback(async (
    workspaceId: string,
    instrumentId: string,
    frame: Timeframe,
    replaceCandles = false,
  ) => {
    if (!workspaceId || !instrumentId || !ownerToken) return
    const generation = marketGeneration.current
    const request = ++marketRequest.current
    if (replaceCandles) setMarketDataLoading(true)
    try {
      const headers = { 'X-Owner-Token': ownerToken }
      const [candleResponse, jobResponse, subscriptionResponse, coverageResponse] = await Promise.all([
        fetch(`${apiBaseUrl}/api/v1/workspaces/${workspaceId}/instruments/${instrumentId}/candles?timeframe=${frame}&limit=500`, { headers }),
        fetch(`${apiBaseUrl}/api/v1/workspaces/${workspaceId}/candle-backfills?instrument_id=${instrumentId}&timeframe=${frame}&limit=10`, { headers }),
        fetch(`${apiBaseUrl}/api/v1/workspaces/${workspaceId}/market-data-subscriptions`, { headers }),
        fetch(`${apiBaseUrl}/api/v1/workspaces/${workspaceId}/instruments/${instrumentId}/candle-coverage?timeframe=${frame}`, { headers }),
      ])
      const [loaded, jobs, feeds, report] = await Promise.all([
        candleResponse.ok ? candleResponse.json() as Promise<Candle[]> : null,
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
  }, [ownerToken])

  const loadOlderCandles = useCallback(async () => {
    if (
      !selectedWorkspaceId || !activeInstrumentId || !ownerToken ||
      olderCandlesLoading || !hasOlderCandles || candles.length === 0
    ) return
    const generation = marketGeneration.current
    setOlderCandlesLoading(true)
    setCandleError(null)
    try {
      const before = encodeURIComponent(candles[0].open_time)
      const response = await fetch(
        `${apiBaseUrl}/api/v1/workspaces/${selectedWorkspaceId}/instruments/${activeInstrumentId}/candles?timeframe=${timeframe}&limit=500&before=${before}`,
        { headers: { 'X-Owner-Token': ownerToken } },
      )
      if (generation !== marketGeneration.current) return
      if (!response.ok) {
        setCandleError(`古いローソク足の取得に失敗しました（HTTP ${response.status}）。`)
        return
      }
      const loaded = (await response.json()) as Candle[]
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
    ownerToken,
    selectedWorkspaceId,
    timeframe,
  ])

  const startBackfill = async () => {
    if (!selectedWorkspaceId || !activeInstrumentId || submissionPending.current) return
    submissionPending.current = true
    setSubmittingMarketAction(true)
    const generation = marketGeneration.current
    try {
      setMarketDataMessage('過去1年分の取得を開始しています。処理中も画面を閉じられます。')
      const response = await fetch(
        `${apiBaseUrl}/api/v1/workspaces/${selectedWorkspaceId}/candle-backfills`,
        {
          method: 'POST',
          headers: { ...ownerHeaders, 'Content-Type': 'application/json' },
          body: JSON.stringify({ instrument_id: activeInstrumentId, timeframe, days: 365 }),
        },
      )
      if (generation !== marketGeneration.current) return
      if (!response.ok) {
        const message = await apiErrorMessage(response, '過去データ取得の開始に失敗しました')
        if (generation === marketGeneration.current) setMarketDataMessage(message)
        return
      }
      setMarketDataMessage('過去1年分の取得を受け付けました。進捗はこの画面に自動反映されます。')
      await loadMarketData(selectedWorkspaceId, activeInstrumentId, timeframe)
    } catch {
      if (generation === marketGeneration.current) setMarketDataMessage('過去取得APIへ接続できません。')
    } finally {
      submissionPending.current = false
      setSubmittingMarketAction(false)
    }
  }

  const setAutomaticCollection = async (enabled: boolean) => {
    if (!selectedWorkspaceId || !activeInstrumentId || submissionPending.current) return
    submissionPending.current = true
    setSubmittingMarketAction(true)
    const generation = marketGeneration.current
    try {
      const response = await fetch(
        `${apiBaseUrl}/api/v1/workspaces/${selectedWorkspaceId}/market-data-subscriptions`,
        {
          method: 'PUT',
          headers: { ...ownerHeaders, 'Content-Type': 'application/json' },
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

  const manageConnection = async (connection: ConnectionSummary, action: 'disable' | 'delete') => {
    if (!selectedWorkspaceId) return
    try {
      const response = await fetch(
      `${apiBaseUrl}/api/v1/workspaces/${selectedWorkspaceId}/connections/${connection.id}${
        action === 'disable' ? '/disable' : ''
      }`,
      { method: action === 'disable' ? 'POST' : 'DELETE', headers: ownerHeaders },
    )
      setWorkspaceMessage(
      response.ok
        ? action === 'disable'
          ? '接続を無効化しました。選択中だった口座も解除しました。'
          : '接続と暗号化済み資格情報を削除しました。'
        : await apiErrorMessage(response, action === 'disable' ? '無効化に失敗しました' : '削除に失敗しました'),
    )
      await loadConnections(selectedWorkspaceId)
    } catch {
      setWorkspaceMessage(
        action === 'disable'
          ? '接続の無効化APIへ接続できません。バックエンドのログを確認してください。'
          : '接続の削除APIへ接続できません。バックエンドのログを確認してください。',
      )
    }
  }

  const selectAccount = async (account: WorkspaceAccount) => {
    if (!selectedWorkspaceId) return
    try {
      const response = await fetch(
        `${apiBaseUrl}/api/v1/workspaces/${selectedWorkspaceId}/account-selections/${account.exchange_code}`,
        {
          method: 'PUT',
          headers: { ...ownerHeaders, 'Content-Type': 'application/json' },
          body: JSON.stringify({ external_account_id: account.id }),
        },
      )
      setWorkspaceMessage(
        response.ok
          ? `${account.exchange_code.toUpperCase()}で利用する口座を選択しました。`
          : await apiErrorMessage(response, '口座選択に失敗しました'),
      )
      await loadConnections(selectedWorkspaceId)
    } catch {
      setWorkspaceMessage('口座選択APIへ接続できません。')
    }
  }

  const verifyOandaConnection = async (connectionId: string) => {
    if (!selectedWorkspaceId) return
    setRegistrationMessage('TokenをOANDA practiceで検証しています。')
    setVerifiedAccounts([])
    try {
      const response = await fetch(
        `${apiBaseUrl}/api/v1/workspaces/${selectedWorkspaceId}/connections/${connectionId}/verify`,
        { method: 'POST', headers: ownerHeaders },
      )
      if (!response.ok) {
        setRegistrationMessage(await apiErrorMessage(response, 'OANDA検証に失敗しました'))
        await loadConnections(selectedWorkspaceId)
        return
      }
      const verification = (await response.json()) as OandaVerification
      setVerifiedAccounts(verification.accounts)
      setRegistrationMessage(
        `OANDA接続を検証し、${verification.accounts.length}件の口座を同期しました。`,
      )
      await loadConnections(selectedWorkspaceId)
    } catch {
      setRegistrationMessage('OANDA APIへの通信に失敗しました。')
    }
  }

  const registerAndVerifyOanda = async () => {
    const isUpdate = Boolean(selectedOandaConnectionId)
    if (!selectedWorkspaceId || !oandaToken || (!isUpdate && !connectionLabel.trim())) return
    setRegistrationMessage(
      isUpdate ? '保存済みTokenを暗号化更新して再検証しています。' : '接続情報を暗号化して登録しています。',
    )
    setVerifiedAccounts([])
    try {
      const saveResponse = await fetch(
        isUpdate
          ? `${apiBaseUrl}/api/v1/workspaces/${selectedWorkspaceId}/connections/${selectedOandaConnectionId}/credentials`
          : `${apiBaseUrl}/api/v1/workspaces/${selectedWorkspaceId}/connections`,
        {
          method: isUpdate ? 'PUT' : 'POST',
          headers: { ...ownerHeaders, 'Content-Type': 'application/json' },
          body: JSON.stringify(
            isUpdate
              ? { credentials: { token: oandaToken } }
              : {
                  exchange_code: 'oanda',
                  label: connectionLabel.trim(),
                  environment: 'practice',
                  api_base_url: 'https://api-fxpractice.oanda.com',
                  credentials: { token: oandaToken },
                },
          ),
        },
      )
      setOandaToken('')
      if (!saveResponse.ok) {
        setRegistrationMessage(
          await apiErrorMessage(saveResponse, isUpdate ? 'Token更新・再検証に失敗しました' : '接続登録に失敗しました'),
        )
        await loadConnections(selectedWorkspaceId)
        return
      }
      if (isUpdate) {
        const verification = (await saveResponse.json()) as OandaVerification
        setVerifiedAccounts(verification.accounts)
        setRegistrationMessage('新しいTokenを暗号化保存し、OANDAでの再検証に成功しました。')
        await loadConnections(selectedWorkspaceId)
      } else {
        const connection = (await saveResponse.json()) as ConnectionSummary
        await verifyOandaConnection(connection.id)
      }
    } catch {
      setOandaToken('')
      setRegistrationMessage('接続登録またはOANDA APIへの通信に失敗しました。')
    }
  }

  const verifyBinanceConnection = async (connectionId: string) => {
    if (!selectedWorkspaceId) return
    setBinanceMessage('Binance Spot TestnetでAPI資格情報を検証しています。')
    setBinanceAccounts([])
    try {
      const response = await fetch(
        `${apiBaseUrl}/api/v1/workspaces/${selectedWorkspaceId}/connections/${connectionId}/verify`,
        { method: 'POST', headers: ownerHeaders },
      )
      if (!response.ok) {
        setBinanceMessage(await apiErrorMessage(response, 'Binance検証に失敗しました'))
        await loadConnections(selectedWorkspaceId)
        return
      }
      const verification = (await response.json()) as BinanceVerification
      setBinanceAccounts(verification.accounts)
      setBinanceMessage('Binance Spot Testnet接続を検証し、口座情報を同期しました。')
      await loadConnections(selectedWorkspaceId)
    } catch {
      setBinanceMessage('Binance Spot Testnet APIへの通信に失敗しました。')
    }
  }

  const registerAndVerifyBinance = async () => {
    const isUpdate = Boolean(selectedBinanceConnectionId)
    if (
      !selectedWorkspaceId ||
      !binanceApiKey ||
      !binanceSecretKey ||
      (!isUpdate && !binanceLabel.trim())
    ) return
    setBinanceMessage(
      isUpdate ? '保存済みAPI資格情報を暗号化更新して再検証しています。' : '接続情報を暗号化して登録しています。',
    )
    setBinanceAccounts([])
    try {
      const saveResponse = await fetch(
        isUpdate
          ? `${apiBaseUrl}/api/v1/workspaces/${selectedWorkspaceId}/connections/${selectedBinanceConnectionId}/credentials`
          : `${apiBaseUrl}/api/v1/workspaces/${selectedWorkspaceId}/connections`,
        {
          method: isUpdate ? 'PUT' : 'POST',
          headers: { ...ownerHeaders, 'Content-Type': 'application/json' },
          body: JSON.stringify(
            isUpdate
              ? { credentials: { api_key: binanceApiKey, secret_key: binanceSecretKey } }
              : {
                  exchange_code: 'binance',
                  label: binanceLabel.trim(),
                  environment: 'testnet',
                  api_base_url: 'https://testnet.binance.vision',
                  credentials: { api_key: binanceApiKey, secret_key: binanceSecretKey },
                },
          ),
        },
      )
      setBinanceApiKey('')
      setBinanceSecretKey('')
      if (!saveResponse.ok) {
        setBinanceMessage(
          await apiErrorMessage(saveResponse, isUpdate ? 'API資格情報の更新・再検証に失敗しました' : '接続登録に失敗しました'),
        )
        await loadConnections(selectedWorkspaceId)
        return
      }
      if (isUpdate) {
        const verification = (await saveResponse.json()) as BinanceVerification
        setBinanceAccounts(verification.accounts)
        setBinanceMessage('新しいAPI資格情報を暗号化保存し、Binanceでの再検証に成功しました。')
        await loadConnections(selectedWorkspaceId)
      } else {
        const connection = (await saveResponse.json()) as ConnectionSummary
        await verifyBinanceConnection(connection.id)
      }
    } catch {
      setBinanceApiKey('')
      setBinanceSecretKey('')
      setBinanceMessage('接続登録またはBinance APIへの通信に失敗しました。')
    }
  }

  useEffect(() => {
    let active = true
    void fetchHealth().then(([api, database]) => {
      if (active) {
        setApiHealth(api)
        setDbHealth(database)
      }
    })
    return () => {
      active = false
    }
  }, [])

  return (
    <AppShell workspaceId={selectedWorkspaceId}>
    <main className="dashboard-shell">
      <Routes>
        <Route
          path="/"
          element={(
            <HomePage>
              <header>
                <p className="eyebrow">AI SYSTEM TRADING</p>
                <h1>開発環境ステータス</h1>
                <p className="subtitle">React → FastAPI → PostgreSQL の接続状態</p>
              </header>
              <section className="status-grid" aria-live="polite">
                <StatusCard title="Backend API" state={apiHealth} />
                <StatusCard title="Database" state={dbHealth} />
              </section>
            </HomePage>
          )}
        />
        <Route
          path="/workspaces/:workspaceId/connections"
          element={<ConnectionManagementPage>{null}</ConnectionManagementPage>}
        />
        <Route
          path="/workspaces/:workspaceId/markets/oanda"
          element={<ExchangeMarketPage exchange="oanda">{null}</ExchangeMarketPage>}
        />
        <Route
          path="/workspaces/:workspaceId/markets/binance"
          element={<ExchangeMarketPage exchange="binance">{null}</ExchangeMarketPage>}
        />
        <Route path="*" element={<NotFoundPage />} />
      </Routes>

      {route.kind !== 'not-found' && (
      <section className="workspace-panel">
        <div>
          <p className="eyebrow">DEVELOPMENT OWNER</p>
          <h2>Workspace選択</h2>
          <p className="panel-description">
            Tokenはこの画面のメモリ上だけで使用し、ブラウザへ保存しません。
          </p>
        </div>
        <div className="workspace-controls">
          <label>
            Owner token
            <input
              type="password"
              value={ownerToken}
              onChange={(event) => setOwnerToken(event.target.value)}
              autoComplete="off"
            />
          </label>
          <button type="button" onClick={() => void loadWorkspaces()} disabled={!ownerToken}>
            読み込む
          </button>
          <label>
            Workspace
            <select
              value={selectedWorkspaceId}
              onChange={(event) => void selectWorkspace(event.target.value)}
              disabled={workspaces.length === 0}
            >
              <option value="">選択してください</option>
              {workspaces.map((workspace) => (
                <option key={workspace.id} value={workspace.id}>
                  {workspace.name} ({workspace.status})
                </option>
              ))}
            </select>
          </label>
        </div>
        <p className="workspace-message">{workspaceMessage}</p>
        {route.kind === 'connections' && connections.length > 0 && (
          <ul className="connection-list">
            {connections.map((connection) => (
              <li key={connection.id}>
                <strong>{connection.label}</strong>
                <span>{connection.environment}</span>
                <span className={`connection-badge credential-${connection.credentials_status}`}>
                  {connection.credentials_status === 'saved' ? '資格情報保存済み' : '資格情報未保存'}
                </span>
                <span className={`connection-badge verification-${connection.verification_outcome}`}>
                  {verificationLabel(connection.verification_outcome)}
                </span>
                <button
                  type="button"
                  onClick={() =>
                    void (connection.environment === 'testnet'
                      ? verifyBinanceConnection(connection.id)
                      : verifyOandaConnection(connection.id))
                  }
                >
                  検証
                </button>
                <button
                  type="button"
                  className="secondary-button"
                  disabled={connection.status === 'disabled'}
                  onClick={() => void manageConnection(connection, 'disable')}
                >
                  無効化
                </button>
                <button
                  type="button"
                  className="danger-button"
                  disabled={!['disabled', 'invalid', 'revoked', 'pending_credentials'].includes(connection.status)}
                  title={
                    ['disabled', 'invalid', 'revoked', 'pending_credentials'].includes(connection.status)
                      ? '保存済み資格情報を含む接続を削除します'
                      : '先に接続を無効化してください'
                  }
                  onClick={() => void manageConnection(connection, 'delete')}
                >
                  削除
                </button>
              </li>
            ))}
          </ul>
        )}
        {route.kind === 'connections' && workspaceAccounts.length > 0 && (
          <div className="account-selection">
            <h3>Workspaceで利用する口座</h3>
            <ul className="account-list">
              {workspaceAccounts.map((account) => (
                <li key={account.id}>
                  <strong>{account.alias || account.account_ref_masked}</strong>
                  <span>{account.exchange_code.toUpperCase()} / {account.connection_label}</span>
                  <span>接続状態: {connectionStatusLabel(account.connection_status)}</span>
                  <span>{account.currency}</span>
                  <button
                    type="button"
                    disabled={
                      account.selected ||
                      account.status !== 'active' ||
                      account.connection_status !== 'verified'
                    }
                    onClick={() => void selectAccount(account)}
                  >
                    {account.selected
                      ? '選択中'
                      : account.connection_status !== 'verified'
                        ? '接続を再検証してください'
                        : 'この口座を利用'}
                  </button>
                </li>
              ))}
            </ul>
          </div>
        )}
      </section>
      )}

      {route.kind === 'connections' && selectedWorkspaceId && (
        <section className="workspace-panel instrument-panel">
          <div>
            <p className="eyebrow">MARKET RULES</p>
            <h2>銘柄同期</h2>
            <p className="panel-description">
              OANDA USD/JPY・Binance BTC/JPYの価格刻みと最小数量を参照専用APIから取得します。
              注文は送信しません。
            </p>
          </div>
          <button
            type="button"
            onClick={() => void syncInstruments()}
            disabled={!workspaceAccounts.some((account) => account.selected)}
          >
            選択済み口座から同期
          </button>
          <p className="workspace-message">{instrumentMessage}</p>
          {workspaceInstruments.length > 0 && (
            <ul className="instrument-list">
              {workspaceInstruments.map((instrument) => (
                <li key={instrument.id}>
                  <strong>{instrument.exchange_code.toUpperCase()} {instrument.symbol}</strong>
                  <span>価格刻み: {instrument.tick_size}</span>
                  <span>数量刻み: {instrument.step_size}</span>
                  <span>最小数量: {instrument.min_quantity ?? '未提供'}</span>
                  <span>最小金額: {instrument.min_notional ?? '未提供'}</span>
                  <span>
                    同期: {instrument.rules_synced_at
                      ? new Date(instrument.rules_synced_at).toLocaleString('ja-JP')
                      : '未同期'}
                  </span>
                </li>
              ))}
            </ul>
          )}
        </section>
      )}

      {route.kind === 'market' && selectedWorkspaceId && visibleInstruments.length > 0 && (
        <section className="workspace-panel market-data-panel">
          <div>
            <p className="eyebrow">CANDLE DATA</p>
            <h2>ローソク足取得・保存</h2>
            <p className="panel-description">
              確定済みデータだけを保存します。過去取得は最大1年、自動取得はバックエンドで1分ごとに続きます。
            </p>
          </div>
          <div className="market-data-controls">
            <label>
              銘柄
              <select value={activeInstrumentId} onChange={(event) => setSelectedInstrumentId(event.target.value)}>
                {visibleInstruments.map((instrument) => (
                  <option key={instrument.id} value={instrument.id}>
                    {instrument.exchange_code.toUpperCase()} {instrument.symbol}
                  </option>
                ))}
              </select>
            </label>
            <label>
              時間足
              <select value={timeframe} onChange={(event) => setTimeframe(event.target.value as Timeframe)}>
                {(['1m', '5m', '15m', '30m', '1h', '4h', '1d'] as Timeframe[]).map((frame) => (
                  <option key={frame} value={frame}>{frame}</option>
                ))}
              </select>
            </label>
            <button type="button" disabled={submittingMarketAction} onClick={() => void startBackfill()}>過去1年を取得</button>
            <button type="button" disabled={submittingMarketAction} onClick={() => void setAutomaticCollection(true)}>この銘柄の全時間足を開始</button>
            <button type="button" disabled={submittingMarketAction} onClick={() => void setAutomaticCollection(false)}>この銘柄の全時間足を停止</button>
          </div>
          <p className="workspace-message">{marketDataMessage}</p>
          <p>時間足の選択は表示と手動の過去取得に使用します。自動取得の開始・停止は全7時間足に適用します。</p>
          <p>自動取得中の時間足: {subscriptions.filter((item) => item.instrument_id === activeInstrumentId && item.enabled).map((item) => item.timeframe).join(', ') || 'なし'}。画面の5秒ごとの更新は保存済みデータの読込であり、取引所からの自動取得とは別です。</p>
          {subscriptions
            .filter((item) => item.instrument_id === activeInstrumentId && item.timeframe === timeframe)
            .map((item) => (
              <div className={`collection-status ${item.enabled ? 'enabled' : 'disabled'}`} key={item.id}>
                <strong>{item.enabled ? '自動取得中' : '自動取得停止中'}</strong>
                <span>最終成功: {item.last_success_at ? new Date(item.last_success_at).toLocaleString('ja-JP') : 'まだありません'}</span>
                {item.last_error_code && <span>直近エラー: {marketErrorLabel(item.last_error_code)}</span>}
              </div>
            ))}
          {backfillJobs.length > 0 && (
            <div className="backfill-status">
              <strong>過去取得 ({timeframe}): {backfillJobs[0].status}</strong>
              <span>完了後は5秒以内に再読込します。古い保存済みデータはチャートを左へ移動して表示できます。</span>
              <span>保存件数: {backfillJobs[0].rows_written.toLocaleString()}</span>
              {backfillJobs[0].error_code && <span>エラー: {marketErrorLabel(backfillJobs[0].error_code)}</span>}
            </div>
          )}
          {coverage && (
            <div className={`coverage-summary coverage-${coverage.coverage_status}`}>
              <strong>取得範囲: {coverage.coverage_status}</strong>
              <span>
                要求: {coverage.requested_from ? new Date(coverage.requested_from).toLocaleDateString('ja-JP') : '指定なし'}
                {' 〜 '}
                {coverage.requested_to ? new Date(coverage.requested_to).toLocaleDateString('ja-JP') : '指定なし'}
              </span>
              <span>
                保存済み: {coverage.actual_from ? new Date(coverage.actual_from).toLocaleString('ja-JP') : 'データなし'}
                {' 〜 '}
                {coverage.actual_to ? new Date(coverage.actual_to).toLocaleString('ja-JP') : 'データなし'}
              </span>
              <span>保存件数: {coverage.stored_count.toLocaleString()}</span>
              {coverage.source_limitation === 'binance_testnet_periodic_reset' && (
                <span>Binance Testnetの定期リセットにより、要求した1年より短い範囲です。</span>
              )}
            </div>
          )}
          {visibleInstruments.find((item) => item.id === activeInstrumentId) && (
            <CandleChart
              key={`${selectedWorkspaceId}:${activeInstrumentId}:${timeframe}`}
              candles={candles}
              instrument={visibleInstruments.find((item) => item.id === activeInstrumentId)!}
              loadingInitial={marketDataLoading}
              loadingOlder={olderCandlesLoading}
              error={candleError}
              hasOlder={hasOlderCandles}
              onLoadOlder={() => void loadOlderCandles()}
              onDisplayedRangeChange={setDisplayedRange}
            />
          )}
          {displayedRange && (
            <div className="chart-range">
              <strong>チャート表示中</strong>
              <span>{new Date(displayedRange.from).toLocaleString('ja-JP')} 〜 {new Date(displayedRange.to).toLocaleString('ja-JP')}</span>
              <span>{candles.length.toLocaleString()}件をブラウザに読込済み</span>
            </div>
          )}
          {candles.length > 0 && (
            <div className="latest-candle">
              <strong>最新の確定足</strong>
              <span>{new Date(candles[candles.length - 1].open_time).toLocaleString('ja-JP')}</span>
              <span>始値 {candles[candles.length - 1].open}</span>
              <span>高値 {candles[candles.length - 1].high}</span>
              <span>安値 {candles[candles.length - 1].low}</span>
              <span>終値 {candles[candles.length - 1].close}</span>
            </div>
          )}
        </section>
      )}

      {route.kind === 'market' && selectedWorkspaceId && visibleInstruments.length === 0 && (
        <section className="workspace-panel market-empty-state">
          <h2>表示できる銘柄がありません</h2>
          <p>接続管理ページで利用口座を選択し、銘柄ルールを同期してください。</p>
        </section>
      )}

      {route.kind === 'connections' && selectedWorkspaceId && (
        <section className="workspace-panel connection-registration">
          <div>
            <p className="eyebrow">OANDA PRACTICE</p>
            <h2>取引所接続を登録</h2>
            <p className="panel-description">
              読取専用の口座確認を行います。外部注文は送信しません。
            </p>
          </div>
          <div className="registration-grid">
            <label>
              操作
              <select
                value={selectedOandaConnectionId}
                onChange={(event) => setSelectedOandaConnectionId(event.target.value)}
              >
                <option value="">新しい接続を登録</option>
                {connections
                  .filter((connection) => connection.environment === 'practice')
                  .map((connection) => (
                    <option key={connection.id} value={connection.id}>
                      {connection.label}を更新 ({connection.status})
                    </option>
                  ))}
              </select>
            </label>
            <label>
              接続名
              <input
                value={connectionLabel}
                onChange={(event) => setConnectionLabel(event.target.value)}
                disabled={Boolean(selectedOandaConnectionId)}
              />
            </label>
            <label>
              OANDA personal access token
              <input
                type="password"
                value={oandaToken}
                onChange={(event) => setOandaToken(event.target.value)}
                autoComplete="off"
              />
            </label>
            <button
              type="button"
              onClick={() => void registerAndVerifyOanda()}
              disabled={!oandaToken || (!selectedOandaConnectionId && !connectionLabel.trim())}
            >
              {selectedOandaConnectionId ? 'Tokenを更新して再検証' : '暗号化保存して検証'}
            </button>
          </div>
          <p className="workspace-message">{registrationMessage}</p>
          {verifiedAccounts.length > 0 && (
            <ul className="account-list">
              {verifiedAccounts.map((account) => (
                <li key={account.account_ref_masked}>
                  <strong>{account.alias || account.account_ref_masked}</strong>
                  <span>{account.currency}</span>
                  <span>USD/JPY: {account.usd_jpy_tradeable ? '利用可能' : '利用不可'}</span>
                </li>
              ))}
            </ul>
          )}
        </section>
      )}

      {route.kind === 'connections' && selectedWorkspaceId && (
        <section className="workspace-panel connection-registration">
          <div>
            <p className="eyebrow">BINANCE SPOT TESTNET</p>
            <h2>Binance接続を登録</h2>
            <p className="panel-description">
              署名付きの口座照会だけを行います。外部注文は送信しません。
            </p>
          </div>
          <div className="registration-grid">
            <label>
              操作
              <select
                value={selectedBinanceConnectionId}
                onChange={(event) => setSelectedBinanceConnectionId(event.target.value)}
              >
                <option value="">新しい接続を登録</option>
                {connections
                  .filter((connection) => connection.environment === 'testnet')
                  .map((connection) => (
                    <option key={connection.id} value={connection.id}>
                      {connection.label}を更新 ({connection.status})
                    </option>
                  ))}
              </select>
            </label>
            <label>
              接続名
              <input
                value={binanceLabel}
                onChange={(event) => setBinanceLabel(event.target.value)}
                disabled={Boolean(selectedBinanceConnectionId)}
              />
            </label>
            <label>
              Binance API Key
              <input
                type="password"
                value={binanceApiKey}
                onChange={(event) => setBinanceApiKey(event.target.value)}
                autoComplete="off"
              />
            </label>
            <label>
              Binance Secret Key
              <input
                type="password"
                value={binanceSecretKey}
                onChange={(event) => setBinanceSecretKey(event.target.value)}
                autoComplete="off"
              />
            </label>
            <button
              type="button"
              onClick={() => void registerAndVerifyBinance()}
              disabled={
                !binanceApiKey ||
                !binanceSecretKey ||
                (!selectedBinanceConnectionId && !binanceLabel.trim())
              }
            >
              {selectedBinanceConnectionId
                ? 'API資格情報を更新して再検証'
                : '暗号化保存して検証'}
            </button>
          </div>
          <p className="workspace-message">{binanceMessage}</p>
          {binanceAccounts.length > 0 && (
            <ul className="account-list">
              {binanceAccounts.map((account) => (
                <li key={account.account_ref_masked}>
                  <strong>{account.account_type} ({account.account_ref_masked})</strong>
                  <span>残高あり資産: {account.nonzero_asset_count}</span>
                  <span>BTC/JPY: {account.btc_jpy_tradeable ? '利用可能' : '利用不可'}</span>
                  <span>取引権限: {account.can_trade ? '有効' : '無効'}</span>
                </li>
              ))}
            </ul>
          )}
        </section>
      )}

      {route.kind === 'home' && <button type="button" onClick={refreshHealth}>
        再確認
      </button>}
      {route.kind === 'home' && <p className="endpoint">API: {apiBaseUrl}</p>}
    </main>
    </AppShell>
  )
}

export default App
