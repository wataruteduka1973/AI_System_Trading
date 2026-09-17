import { describe, expect, it } from 'vitest'
import {
  buildStreamUrl,
  eventToLiveCandle,
  isHeartbeatStale,
  isRetryableClose,
  isStreamState,
  parseStreamMessage,
  reasonCodeLabel,
  statusLabel,
  wsBaseUrl,
  type StreamEventMessage,
  type StreamStateMessage,
} from './marketStream'

const event = (overrides: Partial<StreamEventMessage> = {}): StreamEventMessage => ({
  event_id: 'evt-1',
  workspace_id: 'ws-1',
  exchange: 'binance',
  symbol: 'BTCJPY',
  timeframe: '1m',
  sequence: 1,
  event_type: 'provisional_update',
  open_time: '2026-09-17T00:00:00Z',
  ohlcv: { open: '100', high: '101', low: '99', close: '100.5', volume: '3' },
  source: 'binance_testnet',
  quality: 'provisional',
  reason_code: null,
  ...overrides,
})

describe('marketStream', () => {
  it('distinguishes the stream_state envelope from ordinary events', () => {
    const state: StreamStateMessage = {
      type: 'stream_state',
      feed_started_at: '2026-09-17T00:00:00Z',
      resume: 'fresh',
    }
    expect(isStreamState(state)).toBe(true)
    expect(isStreamState(event())).toBe(false)
  })

  it('parses a valid message and returns null for invalid JSON', () => {
    expect(parseStreamMessage('{"type":"stream_state","feed_started_at":"x","resume":"fresh"}')).toEqual({
      type: 'stream_state',
      feed_started_at: 'x',
      resume: 'fresh',
    })
    expect(parseStreamMessage('not json')).toBeNull()
    expect(parseStreamMessage('null')).toBeNull()
  })

  it('converts a provisional/finalized event into a ChartCandle, and nothing else', () => {
    const candle = eventToLiveCandle(event())
    expect(candle).toEqual({
      open_time: '2026-09-17T00:00:00Z',
      close_time: '2026-09-17T00:00:00Z',
      open: '100',
      high: '101',
      low: '99',
      close: '100.5',
      volume: '3',
      source: 'binance_testnet',
      quality_status: 'provisional',
    })
    expect(eventToLiveCandle(event({ event_type: 'heartbeat', ohlcv: null }))).toBeNull()
    expect(eventToLiveCandle(event({ event_type: 'gap_notice', ohlcv: null }))).toBeNull()
    expect(eventToLiveCandle(event({ ohlcv: null }))).toBeNull()
  })

  it('labels a finalized candle as final when the server omits quality', () => {
    const candle = eventToLiveCandle(event({ event_type: 'candle_finalized', quality: null }))
    expect(candle?.quality_status).toBe('final')
  })

  it('treats a connection silent past 1.5x the heartbeat interval as stale', () => {
    expect(isHeartbeatStale(0, 44_999, 30_000)).toBe(false)
    expect(isHeartbeatStale(0, 45_001, 30_000)).toBe(true)
  })

  it('builds the WS URL with the ticket and, when present, both resume params', () => {
    expect(buildStreamUrl('http://localhost:8000', 'tkt', null)).toBe(
      'ws://localhost:8000/ws/v1/market-stream?ticket=tkt',
    )
    const params = new URLSearchParams({
      ticket: 'tkt',
      resume_last_sequence: '5',
      resume_feed_started_at: '2026-09-17T00:00:00Z',
    })
    expect(
      buildStreamUrl('https://api.example.com', 'tkt', {
        lastSequence: 5,
        feedStartedAt: '2026-09-17T00:00:00Z',
      }),
    ).toBe(`wss://api.example.com/ws/v1/market-stream?${params.toString()}`)
  })

  it('never retries a ticket rejection or an access-denied close', () => {
    expect(isRetryableClose(4401)).toBe(false)
    expect(isRetryableClose(4403)).toBe(false)
    expect(isRetryableClose(1011)).toBe(true)
    expect(isRetryableClose(1000)).toBe(true)
  })

  it('falls back to the raw reason code for one the label map does not recognize', () => {
    expect(reasonCodeLabel(null)).toBeNull()
    expect(reasonCodeLabel('oanda_unreachable')).toBe('OANDAへ接続できません。')
    expect(reasonCodeLabel('some_future_code')).toBe('some_future_code')
  })

  it('labels every connection status', () => {
    expect(statusLabel('connected')).toBe('リアルタイム受信中')
    expect(statusLabel('disconnected')).toBe('切断（要確認）')
  })

  it('only swaps the scheme when deriving the WS base URL', () => {
    expect(wsBaseUrl('http://localhost:8000')).toBe('ws://localhost:8000')
    expect(wsBaseUrl('https://api.example.com')).toBe('wss://api.example.com')
  })
})
