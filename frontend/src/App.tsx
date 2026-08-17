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
  label: string
  environment: string
  status: string
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
  const [workspaceMessage, setWorkspaceMessage] = useState(
    '開発用Owner tokenを入力してWorkspaceを読み込みます。',
  )
  const [connectionLabel, setConnectionLabel] = useState('OANDA practice')
  const [oandaToken, setOandaToken] = useState('')
  const [registrationMessage, setRegistrationMessage] = useState(
    'OANDA practice Tokenは登録後すぐに暗号化され、検証後に入力欄から消去されます。',
  )
  const [verifiedAccounts, setVerifiedAccounts] = useState<OandaVerification['accounts']>([])

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
      setWorkspaceMessage(`選択中のWorkspaceには${loaded.length}件の取引所接続があります。`)
    } catch {
      setWorkspaceMessage('接続一覧APIへ接続できません。')
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
        setRegistrationMessage(`OANDA検証に失敗しました（HTTP ${response.status}）。`)
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
    if (!selectedWorkspaceId || !oandaToken || !connectionLabel.trim()) return
    setRegistrationMessage('接続情報を暗号化して登録しています。')
    setVerifiedAccounts([])
    try {
      const createResponse = await fetch(
        `${apiBaseUrl}/api/v1/workspaces/${selectedWorkspaceId}/connections`,
        {
          method: 'POST',
          headers: { ...ownerHeaders, 'Content-Type': 'application/json' },
          body: JSON.stringify({
            exchange_code: 'oanda',
            label: connectionLabel.trim(),
            environment: 'practice',
            api_base_url: 'https://api-fxpractice.oanda.com',
            credentials: { token: oandaToken },
          }),
        },
      )
      setOandaToken('')
      if (!createResponse.ok) {
        setRegistrationMessage(`接続登録に失敗しました（HTTP ${createResponse.status}）。`)
        return
      }
      const connection = (await createResponse.json()) as ConnectionSummary
      await verifyOandaConnection(connection.id)
    } catch {
      setOandaToken('')
      setRegistrationMessage('接続登録またはOANDA APIへの通信に失敗しました。')
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
                <span>{connection.status}</span>
                <button type="button" onClick={() => void verifyOandaConnection(connection.id)}>
                  検証
                </button>
              </li>
            ))}
          </ul>
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
              接続名
              <input
                value={connectionLabel}
                onChange={(event) => setConnectionLabel(event.target.value)}
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
              disabled={!oandaToken || !connectionLabel.trim()}
            >
              暗号化保存して検証
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

      <button type="button" onClick={refreshHealth}>
        再確認
      </button>
      <p className="endpoint">API: {apiBaseUrl}</p>
    </main>
  )
}

export default App
