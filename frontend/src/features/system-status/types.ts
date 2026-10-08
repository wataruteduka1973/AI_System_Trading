/** Mirrors `app.schemas.system_status.SystemStatusRead` (GET .../system-status). */

export type WorkerState = 'ok' | 'stalled' | 'idle'

export type SystemStatus = {
  checked_at: string
  overall: 'ok' | 'attention'
  /** Ready to show, in Japanese; empty when all is well. */
  problems: string[]
  trading_worker: {
    status: WorkerState
    active_bots: number
    last_heartbeat_age_seconds: number | null
    stalled_bots: string[]
    stale_after_seconds: number
  }
  market_data_worker: {
    status: WorkerState
    enabled_subscriptions: number
    blocked_subscriptions: number
    overdue_subscriptions: number
    most_overdue_seconds: number | null
    overdue_after_seconds: number
  }
  notifications: {
    pending_events: number
    oldest_pending_age_seconds: number | null
    failed_events_last_7_days: number
  }
  /** Active halts by level. */
  halts: Record<string, number>
  /** Bots by `actual_state`. */
  bots: Record<string, number>
  connections: {
    id: string
    label: string
    environment: string
    status: string
    verification_outcome: string
    last_verified_at: string | null
  }[]
}
