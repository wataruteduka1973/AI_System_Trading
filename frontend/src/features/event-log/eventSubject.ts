import type { SystemEvent } from './types'

/** What an event is about, in words: the bot's name when it is about a bot (or names one in its
 * payload), otherwise the kind of target and the start of its id. */
export function eventSubject(event: SystemEvent, botNames: Record<string, string>): string | null {
  if (event.target_type === 'bot' && event.target_id) {
    return `Bot: ${botNames[event.target_id] ?? event.target_id.slice(0, 8)}`
  }
  const named = event.payload.bot_name
  if (typeof named === 'string') return `Bot: ${named}`
  const names = event.payload.bot_names
  if (Array.isArray(names) && names.length > 0) {
    return `Bot: ${names.slice(0, 3).join(', ')}${names.length > 3 ? ` ほか${names.length - 3}件` : ''}`
  }
  if (event.target_type && event.target_id) return `${event.target_type}: ${event.target_id.slice(0, 8)}`
  return null
}
