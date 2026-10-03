import type { BotRunSummary, TradingBot } from './types'

const TIMEFRAME_MS: Record<string, number> = {
  '1m': 60_000,
  '5m': 5 * 60_000,
  '15m': 15 * 60_000,
  '30m': 30 * 60_000,
  '1h': 60 * 60_000,
  '4h': 4 * 60 * 60_000,
  '1d': 24 * 60 * 60_000,
}

/** How long after a bar closes its evaluation may still be on the way (price refresh,
 * the Worker's poll interval) before the bot counts as behind. */
export const EVALUATION_GRACE_MS = 10 * 60_000

export type StaleBot = {
  bot: TradingBot
  /** Close time of the newest bar the bot should have evaluated by now. */
  expectedBarClose: Date
  lastEvaluatedAt: Date | null
}

/** Running bots that have not recorded a signal since their newest closed bar. Bars are
 * aligned to UTC multiples of the timeframe (Binance klines), so the newest close is
 * computable here. Every evaluation records a signal (hold included), so a missing one
 * means the trading Worker is not running -- e.g. the launcher window was closed or the
 * PC slept, which nothing else on screen reveals (2026-10-02/03: 3 bars went unevaluated). */
export function findStaleBots(
  bots: TradingBot[],
  latestRuns: Record<string, BotRunSummary>,
  now: Date,
  graceMs: number = EVALUATION_GRACE_MS,
): StaleBot[] {
  const stale: StaleBot[] = []
  for (const bot of bots) {
    const timeframeMs = TIMEFRAME_MS[bot.timeframe]
    const run = latestRuns[bot.id]
    if (bot.actual_state !== 'running' || timeframeMs === undefined || run === undefined) continue
    const expectedBarClose = Math.floor(now.getTime() / timeframeMs) * timeframeMs
    if (now.getTime() - expectedBarClose < graceMs) continue
    // A just-started run evaluates the newest bar on its first pass; give it the grace too.
    if (now.getTime() - new Date(run.started_at).getTime() < graceMs) continue
    const lastEvaluatedAt = run.latest_signal ? new Date(run.latest_signal.created_at) : null
    if (lastEvaluatedAt === null || lastEvaluatedAt.getTime() < expectedBarClose) {
      stale.push({ bot, expectedBarClose: new Date(expectedBarClose), lastEvaluatedAt })
    }
  }
  return stale
}
