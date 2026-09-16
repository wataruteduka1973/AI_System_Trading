import type { HealthState } from './useHealth'

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

export default function HealthPanel({
  apiHealth,
  dbHealth,
}: {
  apiHealth: HealthState
  dbHealth: HealthState
}) {
  return (
    <>
      <header>
        <p className="eyebrow">AI SYSTEM TRADING</p>
        <h1>開発環境ステータス</h1>
        <p className="subtitle">React → FastAPI → PostgreSQL の接続状態</p>
      </header>
      <section className="status-grid" aria-live="polite">
        <StatusCard title="Backend API" state={apiHealth} />
        <StatusCard title="Database" state={dbHealth} />
      </section>
    </>
  )
}
