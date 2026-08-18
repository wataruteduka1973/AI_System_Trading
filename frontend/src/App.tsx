import { useCallback, useEffect, useState } from 'react'
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
  account_ref_masked: string
  alias: string | null
  currency: string
  status: string
  selected: boolean
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
  const [selectedWorkspaceId, setSelectedWorkspaceId] = useState('')
  const [connections, setConnections] = useState<ConnectionSummary[]>([])
  const [workspaceAccounts, setWorkspaceAccounts] = useState<WorkspaceAccount[]>([])
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
      setSelectedWorkspaceId('')
      setWorkspaceMessage(
        loaded.length > 0 ? `${loaded.length}件のWorkspaceを取得しました。` : 'Workspaceは未登録です。',
      )
    } catch {
      setWorkspaceMessage('Workspace APIへ接続できません。')
    }
  }

  const loadConnections = async (workspaceId: string) => {
    setSelectedWorkspaceId(workspaceId)
    setConnections([])
    setWorkspaceAccounts([])
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
      setWorkspaceMessage(`選択中のWorkspaceには${loaded.length}件の取引所接続があります。`)
    } catch {
      setWorkspaceMessage('接続一覧APIへ接続できません。')
    }
  }

  const manageConnection = async (connection: ConnectionSummary, action: 'disable' | 'delete') => {
    if (!selectedWorkspaceId) return
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
  }

  const selectAccount = async (account: WorkspaceAccount) => {
    if (!selectedWorkspaceId) return
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
    <main className="dashboard-shell">
      <header>
        <p className="eyebrow">AI SYSTEM TRADING</p>
        <h1>開発環境ステータス</h1>
        <p className="subtitle">React → FastAPI → PostgreSQL の接続状態</p>
      </header>

      <section className="status-grid" aria-live="polite">
        <StatusCard title="Backend API" state={apiHealth} />
        <StatusCard title="Database" state={dbHealth} />
      </section>

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
              onChange={(event) => void loadConnections(event.target.value)}
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
        {connections.length > 0 && (
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
                  onClick={() => void manageConnection(connection, 'delete')}
                >
                  削除
                </button>
              </li>
            ))}
          </ul>
        )}
        {workspaceAccounts.length > 0 && (
          <div className="account-selection">
            <h3>Workspaceで利用する口座</h3>
            <ul className="account-list">
              {workspaceAccounts.map((account) => (
                <li key={account.id}>
                  <strong>{account.alias || account.account_ref_masked}</strong>
                  <span>{account.exchange_code.toUpperCase()} / {account.connection_label}</span>
                  <span>{account.currency}</span>
                  <button
                    type="button"
                    disabled={account.selected || account.status !== 'active'}
                    onClick={() => void selectAccount(account)}
                  >
                    {account.selected ? '選択中' : 'この口座を利用'}
                  </button>
                </li>
              ))}
            </ul>
          </div>
        )}
      </section>

      {selectedWorkspaceId && (
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

      {selectedWorkspaceId && (
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

      <button type="button" onClick={refreshHealth}>
        再確認
      </button>
      <p className="endpoint">API: {apiBaseUrl}</p>
    </main>
  )
}

export default App
