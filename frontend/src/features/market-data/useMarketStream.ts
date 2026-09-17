import { useEffect, useRef, useState } from 'react'
import type { ChartCandle } from '../../components/marketData'
import { apiBaseUrl } from '../../lib/api'
import {
  buildStreamUrl,
  eventToLiveCandle,
  isHeartbeatStale,
  isRetryableClose,
  isStreamState,
  parseStreamMessage,
  reasonCodeLabel,
  type ResumeState,
  type StreamConnectionStatus,
} from './marketStream'
import type { Timeframe } from './types'

/**
 * Browser-facing WebSocket termination client (work unit 7 of
 * docs/plans/realtime-market-data-stream.md). Requests a short-lived
 * ticket, connects `WS /ws/v1/market-stream`, and on disconnect
 * reconnects with a fresh ticket carrying the previous connection's
 * `resume_last_sequence`/`resume_feed_started_at` (design doc section 7).
 * All message parsing and status/gap-fill decisions are delegated to the
 * pure helpers in marketStream.ts; this hook only owns the WebSocket
 * lifecycle and React state.
 */

const HEARTBEAT_WATCH_INTERVAL_MS = 5000
// The server's actual heartbeat interval (market_stream_heartbeat_interval_seconds,
// default 30s) is not exposed to the browser today; assuming the default is an
// accepted MVP simplification (see docs/plans/realtime-market-data-stream.md
// work unit 7 status log).
const ASSUMED_HEARTBEAT_INTERVAL_MS = 30_000
const RECONNECT_DELAY_MS = 3_000
const TICKET_RETRY_DELAY_MS = 5_000

export type MarketStreamState = {
  connectionStatus: StreamConnectionStatus
  lastDataAt: Date | null
  gapCount: number
  lastGapReason: string | null
  liveCandle: ChartCandle | null
}

export function useMarketStream(
  ownerToken: string,
  workspaceId: string,
  instrumentId: string,
  timeframe: Timeframe,
  enabled: boolean,
  onGapFillRequired: () => void,
): MarketStreamState {
  const [connectionStatus, setConnectionStatus] = useState<StreamConnectionStatus>('idle')
  const [lastDataAt, setLastDataAt] = useState<Date | null>(null)
  const [gapCount, setGapCount] = useState(0)
  const [lastGapReason, setLastGapReason] = useState<string | null>(null)
  const [liveCandle, setLiveCandle] = useState<ChartCandle | null>(null)

  const onGapFillRequiredRef = useRef(onGapFillRequired)
  useEffect(() => {
    onGapFillRequiredRef.current = onGapFillRequired
  }, [onGapFillRequired])

  useEffect(() => {
    let cancelled = false
    let socket: WebSocket | null = null
    let reconnectTimer: ReturnType<typeof window.setTimeout> | null = null
    let heartbeatWatch: ReturnType<typeof window.setInterval> | null = null
    let maxSequence: number | null = null
    let feedStartedAt: string | null = null
    let lastMessageAtMs = Date.now()

    const scheduleReconnect = (delayMs: number) => {
      if (cancelled) return
      setConnectionStatus('reconnecting')
      reconnectTimer = window.setTimeout(() => void connect(), delayMs)
    }

    const connect = async () => {
      if (cancelled) return
      setConnectionStatus((current) => (current === 'idle' ? 'connecting' : current))
      let ticket: string
      try {
        const response = await fetch(
          `${apiBaseUrl}/api/v1/workspaces/${workspaceId}/market-stream-tickets`,
          {
            method: 'POST',
            headers: { 'X-Owner-Token': ownerToken, 'Content-Type': 'application/json' },
            body: JSON.stringify({ instrument_id: instrumentId, timeframe }),
          },
        )
        if (cancelled) return
        if (!response.ok) {
          scheduleReconnect(TICKET_RETRY_DELAY_MS)
          return
        }
        const payload = (await response.json()) as { ticket: string }
        ticket = payload.ticket
      } catch {
        if (!cancelled) scheduleReconnect(TICKET_RETRY_DELAY_MS)
        return
      }
      if (cancelled) return

      const resume: ResumeState | null =
        maxSequence !== null && feedStartedAt !== null
          ? { lastSequence: maxSequence, feedStartedAt }
          : null
      const ws = new WebSocket(buildStreamUrl(apiBaseUrl, ticket, resume))
      socket = ws

      ws.onmessage = (messageEvent) => {
        if (cancelled) return
        const message = parseStreamMessage(String(messageEvent.data))
        if (!message) return
        lastMessageAtMs = Date.now()

        if (isStreamState(message)) {
          feedStartedAt = message.feed_started_at
          setConnectionStatus('connected')
          if (message.resume === 'gap_fill_required') {
            setGapCount((count) => count + 1)
            onGapFillRequiredRef.current()
          }
          return
        }

        maxSequence = maxSequence === null ? message.sequence : Math.max(maxSequence, message.sequence)
        setConnectionStatus((current) =>
          current === 'delayed' || current === 'connecting' || current === 'reconnecting'
            ? 'connected'
            : current,
        )
        if (message.event_type === 'gap_notice') {
          setGapCount((count) => count + 1)
          setLastGapReason(reasonCodeLabel(message.reason_code))
          setConnectionStatus('delayed')
          return
        }
        if (message.event_type === 'heartbeat') return
        const candle = eventToLiveCandle(message)
        if (candle) {
          setLastDataAt(new Date())
          setLiveCandle(candle)
        }
      }

      ws.onclose = (closeEvent) => {
        socket = null
        if (cancelled) return
        if (!isRetryableClose(closeEvent.code)) {
          setConnectionStatus('disconnected')
          return
        }
        scheduleReconnect(RECONNECT_DELAY_MS)
      }
    }

    // Deferred by one tick (mirrors useMarketData.ts's own initial-load
    // effect): calling setState synchronously in an effect body triggers
    // an avoidable extra render, so the reset and the work it gates both
    // happen inside this callback instead of directly in the effect.
    const startTimer = window.setTimeout(() => {
      setConnectionStatus('idle')
      setLastDataAt(null)
      setGapCount(0)
      setLastGapReason(null)
      setLiveCandle(null)
      if (!enabled || !ownerToken || !workspaceId || !instrumentId) return

      heartbeatWatch = window.setInterval(() => {
        if (cancelled) return
        setConnectionStatus((current) =>
          current === 'connected' &&
          isHeartbeatStale(lastMessageAtMs, Date.now(), ASSUMED_HEARTBEAT_INTERVAL_MS)
            ? 'delayed'
            : current,
        )
      }, HEARTBEAT_WATCH_INTERVAL_MS)

      void connect()
    }, 0)

    return () => {
      cancelled = true
      window.clearTimeout(startTimer)
      if (reconnectTimer !== null) window.clearTimeout(reconnectTimer)
      if (heartbeatWatch !== null) window.clearInterval(heartbeatWatch)
      socket?.close(1000, 'client_navigating')
    }
  }, [enabled, ownerToken, workspaceId, instrumentId, timeframe])

  return { connectionStatus, lastDataAt, gapCount, lastGapReason, liveCandle }
}
