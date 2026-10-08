/** Mirrors `app.schemas.events` (GET .../events, GET .../events/facets). */

export type SystemEvent = {
  id: string
  occurred_at: string
  severity: 'debug' | 'info' | 'warning' | 'error' | 'critical' | string
  category: string
  event_type: string
  reason_code: string | null
  source_type: string
  source_id: string | null
  target_type: string | null
  target_id: string | null
  correlation_id: string
  message: string
  payload: Record<string, unknown>
}

export type EventPage = {
  items: SystemEvent[]
  /** Pass both back for the next (older) page; null at the end. */
  next_before: string | null
  next_before_id: string | null
}

export type EventFacets = {
  severities: string[]
  categories: string[]
  event_types: string[]
  reason_codes: string[]
}

export type Period = '1h' | '24h' | '7d' | 'all'

export type EventFilters = {
  severities: string[]
  category: string
  eventType: string
  reasonCode: string
  botId: string
  correlationId: string
  period: Period
  text: string
}

export const emptyFilters: EventFilters = {
  severities: [],
  category: '',
  eventType: '',
  reasonCode: '',
  botId: '',
  correlationId: '',
  period: '7d',
  text: '',
}
