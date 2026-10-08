import type { AppNotification } from './types'

const severityLabel: Record<string, string> = {
  critical: '重大',
  error: 'エラー',
  warning: '警告',
  info: '情報',
}

const severityClass: Record<string, string> = {
  critical: 'connection-badge bot-state-failed',
  error: 'connection-badge bot-state-failed',
  warning: 'connection-badge bot-state-paused',
  info: 'connection-badge',
}

/** In-app notifications (docs/plans/notification-wiring.md): the unread ones stand out, and
 * acknowledging is one click. */
export default function NotificationsPanel({
  items,
  unacknowledgedCount,
  message,
  onAcknowledge,
  onAcknowledgeAll,
}: {
  items: AppNotification[]
  unacknowledgedCount: number
  message: string
  onAcknowledge: (notificationId: string) => void
  onAcknowledgeAll: () => void
}) {
  return (
    <section className="workspace-panel" aria-labelledby="notifications-title">
      <p className="eyebrow">NOTIFICATIONS</p>
      <h2 id="notifications-title">通知</h2>
      <div className="notification-toolbar">
        <span>未確認 {unacknowledgedCount}件</span>
        <button type="button" onClick={onAcknowledgeAll} disabled={unacknowledgedCount === 0}>
          すべて確認済みにする
        </button>
      </div>
      {items.length === 0 ? (
        <p className="panel-description">通知はありません。</p>
      ) : (
        <ul className="notification-list">
          {items.map((item) => (
            <li
              key={item.id}
              className={
                item.status === 'sent' ? 'notification-item notification-unread' : 'notification-item'
              }
            >
              <div className="halt-heading">
                <span className={severityClass[item.severity] ?? 'connection-badge'}>
                  {severityLabel[item.severity] ?? item.severity}
                </span>
                <time dateTime={item.occurred_at}>{new Date(item.occurred_at).toLocaleString()}</time>
              </div>
              <p>{item.message}</p>
              {item.status === 'sent' ? (
                <button type="button" onClick={() => onAcknowledge(item.id)}>
                  確認済みにする
                </button>
              ) : (
                <span className="panel-description">確認済み</span>
              )}
            </li>
          ))}
        </ul>
      )}
      {message && <p className="login-error">{message}</p>}
    </section>
  )
}
