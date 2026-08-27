import {
  CandlestickSeries,
  ColorType,
  CrosshairMode,
  createChart,
  type Time,
  type UTCTimestamp,
} from 'lightweight-charts'
import { useCallback, useEffect, useRef, useState } from 'react'
import { Route, Routes, useLocation, useNavigate } from 'react-router'
import AppShell from './app/AppShell'
import { connectionPath, resolveAppRoute } from './app/routes'
import ConnectionManagementPage from './pages/ConnectionManagementPage'
import ExchangeMarketPage from './pages/ExchangeMarketPage'
import HomePage from './pages/HomePage'
import NotFoundPage from './pages/NotFoundPage'
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

function CandleChart({ candles, instrument }: { candles: Candle[]; instrument: WorkspaceInstrument }) {
  const containerRef = useRef<HTMLDivElement>(null)
  const [hovered, setHovered] = useState<Candle | null>(null)

  useEffect(() => {
    if (!containerRef.current || candles.length === 0) return
    const dateTime = new Intl.DateTimeFormat('ja-JP', {
      timeZone: 'Asia/Tokyo',
      month: '2-digit',
      day: '2-digit',
      hour: '2-digit',
      minute: '2-digit',
    })
    const chart = createChart(containerRef.current, {
      autoSize: true,
      height: 360,
      layout: { background: { type: ColorType.Solid, color: '#081426' }, textColor: '#b9cbe0' },
      grid: {
        vertLines: { color: 'rgba(75, 104, 139, 0.2)' },
        horzLines: { color: 'rgba(75, 104, 139, 0.2)' },
      },
      crosshair: { mode: CrosshairMode.Normal },
      rightPriceScale: { borderColor: '#38506f' },
      timeScale: {
        borderColor: '#38506f',
        timeVisible: true,
        secondsVisible: false,
        tickMarkFormatter: (time: Time) => dateTime.format(new Date(Number(time) * 1000)),
      },
      localization: {
        timeFormatter: (time: Time) =>
          new Date(Number(time) * 1000).toLocaleString('ja-JP', { timeZone: 'Asia/Tokyo' }),
      },
    })
    const series = chart.addSeries(CandlestickSeries, {
      upColor: '#26a69a',
      downColor: '#ef5350',
      borderVisible: false,
      wickUpColor: '#26a69a',
      wickDownColor: '#ef5350',
      priceFormat: {
        type: 'price',
        precision: instrument.price_scale,
        minMove: Number(instrument.tick_size),
      },
      title: `${instrument.symbol} / ${instrument.quote_asset}`,
    })
    const indexed = new Map<number, Candle>()
    const chartData = candles.map((candle) => {
      const time = Math.floor(new Date(candle.open_time).getTime() / 1000) as UTCTimestamp
      indexed.set(time, candle)
      return {
        time,
        open: Number(candle.open),
        high: Number(candle.high),
        low: Number(candle.low),
        close: Number(candle.close),
      }
    })
    series.setData(chartData)
    chart.timeScale().fitContent()
    chart.subscribeCrosshairMove((parameter) => {
      const time = typeof parameter.time === 'number' ? parameter.time : null
      setHovered(time === null ? null : indexed.get(time) ?? null)
    })
    return () => chart.remove()
  }, [candles, instrument])

  if (candles.length === 0) return <p className="chart-empty">表示できる確定足がまだありません。</p>
  const detail = hovered ?? candles[candles.length - 1]
  return (
    <div className="candle-chart" role="img" aria-label={`${instrument.symbol}のローソク足`}>
      <div className="chart-tooltip" aria-live="polite">
        <strong>{new Date(detail.open_time).toLocaleString('ja-JP', { timeZone: 'Asia/Tokyo' })}</strong>
        <span>始値 {detail.open}</span><span>高値 {detail.high}</span>
        <span>安値 {detail.low}</span><span>終値 {detail.close}</span>
        <span>出来高 {detail.volume ?? '未提供'}</span>
      </div>
      <div ref={containerRef} className="candle-chart-canvas" />
    </div>
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
  const [backfillJobs, setBackfillJobs] = useState<BackfillJob[]>([])
  const [subscriptions, setSubscriptions] = useState<MarketDataSubscription[]>([])
  const [coverage, setCoverage] = useState<CandleCoverage | null>(null)
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

  const loadMarketData = useCallback(async (workspaceId: string, instrumentId: string, frame: Timeframe) => {
    if (!workspaceId || !instrumentId || !ownerToken) return
    try {
      const headers = { 'X-Owner-Token': ownerToken }
      const [candleResponse, jobResponse, subscriptionResponse, coverageResponse] = await Promise.all([
        fetch(`${apiBaseUrl}/api/v1/workspaces/${workspaceId}/instruments/${instrumentId}/candles?timeframe=${frame}&limit=500`, { headers }),
        fetch(`${apiBaseUrl}/api/v1/workspaces/${workspaceId}/candle-backfills?instrument_id=${instrumentId}&limit=10`, { headers }),
        fetch(`${apiBaseUrl}/api/v1/workspaces/${workspaceId}/market-data-subscriptions`, { headers }),
        fetch(`${apiBaseUrl}/api/v1/workspaces/${workspaceId}/instruments/${instrumentId}/candle-coverage?timeframe=${frame}`, { headers }),
      ])
      if (candleResponse.ok) setCandles((await candleResponse.json()) as Candle[])
      if (jobResponse.ok) setBackfillJobs((await jobResponse.json()) as BackfillJob[])
      if (subscriptionResponse.ok) {
        setSubscriptions((await subscriptionResponse.json()) as MarketDataSubscription[])
      }
      if (coverageResponse.ok) setCoverage((await coverageResponse.json()) as CandleCoverage)
    } catch {
      setMarketDataMessage('ローソク足APIへ接続できません。')
    }
  }, [ownerToken])

  const startBackfill = async () => {
    if (!selectedWorkspaceId || !activeInstrumentId) return
    setMarketDataMessage('過去1年分の取得を開始しています。処理中も画面を閉じられます。')
    const response = await fetch(
      `${apiBaseUrl}/api/v1/workspaces/${selectedWorkspaceId}/candle-backfills`,
      {
        method: 'POST',
        headers: { ...ownerHeaders, 'Content-Type': 'application/json' },
        body: JSON.stringify({ instrument_id: activeInstrumentId, timeframe, days: 365 }),
      },
    )
    if (!response.ok) {
      setMarketDataMessage(await apiErrorMessage(response, '過去データ取得の開始に失敗しました'))
      return
    }
    setMarketDataMessage('過去1年分の取得を受け付けました。進捗はこの画面に自動反映されます。')
    await loadMarketData(selectedWorkspaceId, activeInstrumentId, timeframe)
  }

  const setAutomaticCollection = async (enabled: boolean) => {
    if (!selectedWorkspaceId || !activeInstrumentId) return
    const response = await fetch(
      `${apiBaseUrl}/api/v1/workspaces/${selectedWorkspaceId}/market-data-subscription`,
      {
        method: 'PUT',
        headers: { ...ownerHeaders, 'Content-Type': 'application/json' },
        body: JSON.stringify({ instrument_id: activeInstrumentId, timeframe, enabled }),
      },
    )
    setMarketDataMessage(
      response.ok
        ? enabled
          ? '1分間隔の自動取得を有効にしました。画面を閉じてもバックエンドで継続します。'
          : '自動取得を停止しました。保存済みデータは残ります。'
        : await apiErrorMessage(response, enabled ? '自動取得の開始に失敗しました' : '自動取得の停止に失敗しました'),
    )
    await loadMarketData(selectedWorkspaceId, activeInstrumentId, timeframe)
  }

  useEffect(() => {
    if (!selectedWorkspaceId || !activeInstrumentId) return
    const initialLoad = window.setTimeout(() => {
      void loadMarketData(selectedWorkspaceId, activeInstrumentId, timeframe)
    }, 0)
    const timer = window.setInterval(() => {
      void loadMarketData(selectedWorkspaceId, activeInstrumentId, timeframe)
    }, 5000)
    return () => {
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
            <button type="button" onClick={() => void startBackfill()}>過去1年を取得</button>
            {subscriptions.some(
              (item) => item.instrument_id === activeInstrumentId && item.timeframe === timeframe && item.enabled,
            ) ? (
              <button type="button" className="secondary-button" onClick={() => void setAutomaticCollection(false)}>
                自動取得を停止
              </button>
            ) : (
              <button type="button" onClick={() => void setAutomaticCollection(true)}>1分ごとの自動取得を開始</button>
            )}
          </div>
          <p className="workspace-message">{marketDataMessage}</p>
          {subscriptions
            .filter((item) => item.instrument_id === activeInstrumentId && item.timeframe === timeframe)
            .map((item) => (
              <div className={`collection-status ${item.enabled ? 'enabled' : 'disabled'}`} key={item.id}>
                <strong>{item.enabled ? '自動取得中' : '自動取得停止中'}</strong>
                <span>最終成功: {item.last_success_at ? new Date(item.last_success_at).toLocaleString('ja-JP') : 'まだありません'}</span>
                {item.last_error_code && <span>直近エラー: {item.last_error_code}</span>}
              </div>
            ))}
          {backfillJobs.length > 0 && (
            <div className="backfill-status">
              <strong>過去取得: {backfillJobs[0].status}</strong>
              <span>保存件数: {backfillJobs[0].rows_written.toLocaleString()}</span>
              {backfillJobs[0].error_code && <span>エラー: {backfillJobs[0].error_code}</span>}
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
              candles={candles}
              instrument={visibleInstruments.find((item) => item.id === activeInstrumentId)!}
            />
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
