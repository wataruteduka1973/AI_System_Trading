/** Mirrors `app.schemas.notifications` (GET .../notifications). */

export type NotificationSeverity = 'info' | 'warning' | 'error' | 'critical'

export type AppNotification = {
  id: string
  event_id: string
  severity: NotificationSeverity | string
  category: string
  event_type: string
  message: string
  payload: Record<string, unknown>
  occurred_at: string
  /** `sent` until the person acknowledges it, then `acknowledged`. */
  status: 'sent' | 'acknowledged' | string
  acknowledged_at: string | null
}

export type NotificationList = {
  /** Over all of the person's notifications in the workspace, not just `items`. */
  unacknowledged_count: number
  items: AppNotification[]
}
