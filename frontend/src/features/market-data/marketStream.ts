import type { ChartCandle } from '../../components/marketData'

/**
 * Wire types and pure helpers for the browser-facing realtime market-data
 * WebSocket (`WS /ws/v1/market-stream?ticket=<ticket>`). Deliberately
 * independent of WebSocket/React so the reconnect/gap-fill and status
 * logic can be unit tested directly -- mirrors the backend's own
 * separation of pure protocol code from the actual transport in
 * app/market_data/infrastructure/stream_protocol.py (see
 * docs/design/modules/realtime-market-data-stream.md sections 5 and 7).
 * The actual WebSocket wiring lives in useMarketStream.ts.
 */

export type StreamEventType =
  | 'provisional_update'
  | 'candle_finalized'
  | 'heartbeat'
  | 'gap_notice'

export type StreamOhlcv = {
  open: string
  high: string
  low: string
  close: string
  volume: string
}

export type StreamEventMessage = {
  event_id: string
  workspace_id: string
  exchange: string
  symbol: string
  timeframe: string
  sequence: number
  event_type: StreamEventType
  open_time: string | null
  ohlcv: StreamOhlcv | null
  source: string
  quality: string | null
  reason_code: string | null
}

export type ResumeMode = 'fresh' | 'replayed' | 'gap_fill_required'

export type StreamStateMessage = {
  type: 'stream_state'
  feed_started_at: string
  resume: ResumeMode
}

export type StreamMessage = StreamStateMessage | StreamEventMessage

/** Event messages never carry a `type` key (design doc section 5/7) --
 * only the one-time `stream_state` envelope sent as the first message on
 * every successful connect does. */
export function isStreamState(message: StreamMessage): message is StreamStateMessage {
  return (message as StreamStateMessage).type === 'stream_state'
}

export function parseStreamMessage(raw: string): StreamMessage | null {
  try {
    const parsed = JSON.parse(raw) as unknown
    if (!parsed || typeof parsed !== 'object') return null
    return parsed as StreamMessage
  } catch {
    return null
  }
}

/** Converts a provisional/finalized stream event into the same shape the
 * REST candle endpoints already return, so CandleChart can feed it to
 * `series.update()` exactly like any other ChartCandle. Returns null for
 * event types that carry no OHLCV (heartbeat, gap_notice) or a malformed
 * event -- callers must not call series.update() for those. */
export function eventToLiveCandle(event: StreamEventMessage): ChartCandle | null {
  if (event.event_type !== 'provisional_update' && event.event_type !== 'candle_finalized') {
    return null
  }
  if (!event.ohlcv || !event.open_time) return null
  return {
    open_time: event.open_time,
    close_time: event.open_time,
    open: event.ohlcv.open,
    high: event.ohlcv.high,
    low: event.ohlcv.low,
    close: event.ohlcv.close,
    volume: event.ohlcv.volume,
    source: event.source,
    quality_status: event.quality ?? (event.event_type === 'candle_finalized' ? 'final' : 'provisional'),
  }
}

export type StreamConnectionStatus =
  | 'idle'
  | 'connecting'
  | 'connected'
  | 'reconnecting'
  | 'delayed'
  | 'disconnected'

const STATUS_LABEL: Record<StreamConnectionStatus, string> = {
  idle: '未接続',
  connecting: '接続中',
  connected: 'リアルタイム受信中',
  reconnecting: '再接続中',
  delayed: '受信遅延',
  disconnected: '切断（要確認）',
}

export function statusLabel(status: StreamConnectionStatus): string {
  return STATUS_LABEL[status]
}

/** design doc section 5/8: `reason_code` is a closed set of safe
 * classification codes, never a raw exception message. An unrecognized
 * code (e.g. one added server-side later) still displays, just
 * untranslated, rather than being hidden. */
const GAP_REASON_LABEL: Record<string, string> = {
  oanda_unreachable: 'OANDAへ接続できません。',
  oanda_authentication_failed: 'OANDAの認証に失敗しました。',
  binance_unreachable: 'Binanceへ接続できません。',
  binance_authentication_failed: 'Binanceの認証に失敗しました。',
}

export function reasonCodeLabel(code: string | null): string | null {
  if (!code) return null
  return GAP_REASON_LABEL[code] ?? code
}

const HEARTBEAT_STALE_MULTIPLIER = 1.5

/** A connection with no message (event or heartbeat) for more than 1.5x
 * the server's heartbeat interval is treated as delayed -- the server
 * sends a heartbeat every interval even with no market activity (design
 * doc section 5), so silence past that margin means the connection
 * itself has gone quiet, not just a quiet market. */
export function isHeartbeatStale(
  lastMessageAtMs: number,
  nowMs: number,
  heartbeatIntervalMs: number,
): boolean {
  return nowMs - lastMessageAtMs > heartbeatIntervalMs * HEARTBEAT_STALE_MULTIPLIER
}

export function wsBaseUrl(httpBaseUrl: string): string {
  return httpBaseUrl.replace(/^http/, 'ws')
}

export type ResumeState = { lastSequence: number; feedStartedAt: string }

/** Design doc section 7 / work unit 6: `ticket` is always required;
 * `resume_last_sequence` and `resume_feed_started_at` are included
 * together only when both are known from a previous connection -- a
 * partial resume state is not sent (the server treats either one alone
 * as a fresh connect anyway, see stream_protocol.py `ResumeRequest`). */
export function buildStreamUrl(
  httpBaseUrl: string,
  ticket: string,
  resume: ResumeState | null,
): string {
  const params = new URLSearchParams({ ticket })
  if (resume) {
    params.set('resume_last_sequence', String(resume.lastSequence))
    params.set('resume_feed_started_at', resume.feedStartedAt)
  }
  return `${wsBaseUrl(httpBaseUrl)}/ws/v1/market-stream?${params.toString()}`
}

/** Close codes the server sends from app/market_data/infrastructure/
 * stream_session.py: CLOSE_TICKET_REJECTED=4401, CLOSE_ACCESS_DENIED=4403
 * (both permanent -- a fresh ticket cannot fix an invalid/replayed ticket
 * or a revoked Workspace/account, so retrying would just hot-loop) and
 * CLOSE_FEED_START_FAILED=1011 (an exchange-side start failure, treated
 * as transient like an ordinary network drop). */
const NON_RETRYABLE_CLOSE_CODES = new Set([4401, 4403])

export function isRetryableClose(code: number): boolean {
  return !NON_RETRYABLE_CLOSE_CODES.has(code)
}
