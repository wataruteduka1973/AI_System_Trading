import type { ReactNode } from 'react'
import { formatAge } from './formatAge'
import type { SystemStatus, WorkerState } from './types'

const workerLabel: Record<WorkerState, string> = {
  ok: '稼働中',
  stalled: '止まっている可能性',
  idle: '待機中(対象なし)',
}

const workerClass: Record<WorkerState, string> = {
  ok: 'connection-badge bot-state-running',
  stalled: 'connection-badge bot-state-failed',
  idle: 'connection-badge',
}

const verificationLabel: Record<string, string> = {
  success: '検証成功',
  authentication_failed: '認証失敗',
  communication_failed: '通信失敗',
  not_verified: '未検証',
}

const botStateLabel: Record<string, string> = {
  running: '稼働中',
  paused: '一時停止',
  stopped: '停止中',
  failed: '失敗(要確認)',
}

function Card({ title, badge, children }: { title: string; badge?: ReactNode; children: ReactNode }) {
  return (
    <article className="status-detail-card">
      <div className="status-heading">
        <h3>{title}</h3>
        {badge}
      </div>
      {children}
    </article>
  )
}

/** The workspace's health in one place (docs/plans/system-status.md): the verdict first, with the
 * reasons in plain words, then each part. `error` replaces the verdict: when the status cannot
 * be read, the page must not keep showing the last "all clear". */
export default function SystemStatusPanel({
  status,
  error,
}: {
  status: SystemStatus | null
  error: string
}) {
  if (error) {
    return (
      <section className="workspace-panel" role="alert">
        <h2>システム状態</h2>
        <p className="login-error">{error}</p>
        <p className="panel-description">状態を確認できていないため、正常とは言えません。</p>
      </section>
    )
  }
  if (!status) {
    return (
      <section className="workspace-panel">
        <h2>システム状態</h2>
        <p className="panel-description">読み込んでいます…</p>
      </section>
    )
  }
  const attention = status.overall === 'attention'
  const trading = status.trading_worker
  const market = status.market_data_worker
  return (
    <section className="workspace-panel" aria-labelledby="system-status-title">
      <p className="eyebrow">SYSTEM STATUS</p>
      <h2 id="system-status-title">システム状態</h2>
      <div
        className={attention ? 'status-verdict status-verdict-attention' : 'status-verdict'}
        role="status"
      >
        <strong>{attention ? '要確認' : '正常'}</strong>
        {attention ? (
          <ul>
            {status.problems.map((problem) => (
              <li key={problem}>{problem}</li>
            ))}
          </ul>
        ) : (
          <span>問題は検知されていません。</span>
        )}
      </div>
      <div className="status-detail-grid">
        <Card
          title="トレーディングWorker"
          badge={<span className={workerClass[trading.status]}>{workerLabel[trading.status]}</span>}
        >
          <p>稼働中のBot: {trading.active_bots}件</p>
          <p>最後の確認: {formatAge(trading.last_heartbeat_age_seconds)}</p>
          <p className="panel-description">
            {trading.stale_after_seconds}秒を超えると「止まっている」と判断します。
          </p>
          {trading.stalled_bots.length > 0 && (
            <p>
              対象: {trading.stalled_bots.slice(0, 5).join(', ')}
              {trading.stalled_bots.length > 5 && ` ほか${trading.stalled_bots.length - 5}件`}
            </p>
          )}
        </Card>
        <Card
          title="市場データWorker"
          badge={<span className={workerClass[market.status]}>{workerLabel[market.status]}</span>}
        >
          <p>
            自動取得の購読: {market.enabled_subscriptions}件(停止中 {market.blocked_subscriptions}件)
          </p>
          <p>
            遅れている購読: {market.overdue_subscriptions}件
            {market.most_overdue_seconds !== null &&
              `(最大 ${formatAge(market.most_overdue_seconds, false)}遅れ)`}
          </p>
          <p className="panel-description">
            {market.overdue_after_seconds}秒を超えると「遅れている」と判断します。
          </p>
        </Card>
        <Card title="通知の配信">
          <p>配信待ち: {status.notifications.pending_events}件</p>
          {status.notifications.oldest_pending_age_seconds !== null && (
            <p>最も古い待ち: {formatAge(status.notifications.oldest_pending_age_seconds, false)}</p>
          )}
          <p>直近7日に届けられなかった通知: {status.notifications.failed_events_last_7_days}件</p>
        </Card>
        <Card title="Bot">
          {Object.keys(status.bots).length === 0 ? (
            <p className="panel-description">Botはありません。</p>
          ) : (
            <ul className="plain-list">
              {Object.entries(status.bots).map(([state, count]) => (
                <li key={state}>
                  {botStateLabel[state] ?? state}: {count}件
                </li>
              ))}
            </ul>
          )}
        </Card>
      </div>
      <h3>取引所接続</h3>
      {status.connections.length === 0 ? (
        <p className="panel-description">接続はありません。</p>
      ) : (
        <ul className="connection-list status-connections">
          {status.connections.map((connection) => (
            <li key={connection.id}>
              <strong>{connection.label}</strong>
              <span>{connection.environment}</span>
              <span className="connection-badge">{connection.status}</span>
              <span className={`connection-badge verification-${connection.verification_outcome}`}>
                {verificationLabel[connection.verification_outcome] ?? connection.verification_outcome}
              </span>
              <span>
                {connection.last_verified_at
                  ? `最後の検証 ${new Date(connection.last_verified_at).toLocaleString()}`
                  : '未検証'}
              </span>
            </li>
          ))}
        </ul>
      )}
      <p className="panel-description">
        確認時刻: {new Date(status.checked_at).toLocaleString()}(15秒ごとに更新)
      </p>
    </section>
  )
}
