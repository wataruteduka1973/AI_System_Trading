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
              </li>
            ))}
          </ul>
        )}
      </section>

      <button type="button" onClick={refreshHealth}>
        再確認
      </button>
      <p className="endpoint">API: {apiBaseUrl}</p>
    </main>
  )
}

export default App
