import type { BotOverview, BotRunSummary, TradingBot } from './types'
import { formatAmount, formatPercent, formatSigned, pnlClass } from './format'

const stateLabel: Record<TradingBot['desired_state'], string> = {
  stopped: '停止中',
  running: '稼働中',
  paused: '一時停止',
  failed: '失敗(要確認)',
}

const stateBadgeClass: Record<TradingBot['desired_state'], string> = {
  stopped: 'connection-badge',
  running: 'connection-badge bot-state-running',
  paused: 'connection-badge bot-state-paused',
  failed: 'connection-badge bot-state-failed',
}

const sideLabel: Record<string, string> = { long: '買い(ロング)', short: '売り(ショート)' }

const exchangeLabel: Record<string, string> = {
  oanda: 'OANDA',
  binance: 'Binance',
  binance_public: '公開履歴',
}

/** One bot: what it trades and where, how it stands, its open position, and its controls. The
 * figures come from `bot-overview` and are absent (a dash) until that has loaded. */
export default function BotCard({
  bot,
  overview,
  latestRun,
  onCommand,
  onConfirmStop,
}: {
  bot: TradingBot
  overview: BotOverview | undefined
  latestRun: BotRunSummary | undefined
  onCommand: (bot: TradingBot, command: 'start' | 'pause' | 'resume' | 'stop') => void
  onConfirmStop: (bot: TradingBot) => void
}) {
  // A bot the worker gave up on has desired_state 'stopped' (the DB allows nothing else there)
  // and actual_state 'failed': show the failure, not an ordinary stop.
  const shownState = bot.actual_state === 'failed' ? 'failed' : bot.desired_state
  const latestSignal = latestRun?.latest_signal
  const asset = overview?.quote_asset ?? ''
  const position = overview?.position ?? null
  return (
    <li className="bot-card">
      <div className="bot-card-head">
        <strong>{bot.name}</strong>
        {overview && <span className="bot-symbol">{overview.symbol}</span>}
        {overview && (
          <span className="connection-badge">
            {exchangeLabel[overview.exchange_code] ?? overview.exchange_code}
          </span>
        )}
        <span>{bot.timeframe}</span>
        <span className={stateBadgeClass[shownState]}>{stateLabel[shownState]}</span>
      </div>
      {overview && (
        <dl className="bot-metrics">
          <div>
            <dt>純資産</dt>
            <dd>
              {formatAmount(overview.equity)} {asset}
            </dd>
          </div>
          <div>
            <dt>入金に対する損益</dt>
            <dd className={pnlClass(overview.return_pct)}>
              {formatPercent(overview.return_pct)}(
              {formatSigned(String(Number(overview.equity) - Number(overview.deposits)))} {asset})
            </dd>
          </div>
          <div>
            <dt>確定損益(手数料前)</dt>
            <dd className={pnlClass(overview.realized_pnl)}>
              {formatSigned(overview.realized_pnl)} {asset}
            </dd>
          </div>
          <div>
            <dt>手数料 / 取引数</dt>
            <dd>
              {formatAmount(overview.fees_paid)} {asset} / {overview.closed_trades}回
            </dd>
          </div>
        </dl>
      )}
      <p className="bot-position">
        {position ? (
          <>
            <strong>建玉</strong> {sideLabel[position.side] ?? position.side}{' '}
            {formatAmount(position.quantity, 6)} @ {formatAmount(position.average_entry_price, 4)}
            {' / '}現在値 {formatAmount(position.mark_price, 4)}
            {' / '}含み損益{' '}
            <span className={pnlClass(position.unrealized_pnl)}>
              {formatSigned(position.unrealized_pnl)} {asset}
            </span>
            {position.stop_price !== null && <> / 損切り {formatAmount(position.stop_price, 4)}</>}
          </>
        ) : (
          overview && <span className="panel-description">建玉なし</span>
        )}
      </p>
      <div className="bot-card-foot">
        <span className="panel-description">
          {latestSignal
            ? `直近シグナル: ${latestSignal.action} (${new Date(latestSignal.created_at).toLocaleString()})`
            : '評価履歴なし'}
        </span>
        <span className="bot-actions">
          {bot.desired_state === 'stopped' && (
            <button type="button" onClick={() => onCommand(bot, 'start')}>
              開始
            </button>
          )}
          <button type="button" className="danger-button" onClick={() => onConfirmStop(bot)}>
            緊急停止
          </button>
          {bot.desired_state === 'running' && (
            <>
              <button type="button" onClick={() => onCommand(bot, 'pause')}>
                一時停止
              </button>
              <button type="button" className="danger-button" onClick={() => onCommand(bot, 'stop')}>
                停止
              </button>
            </>
          )}
          {bot.desired_state === 'paused' && (
            <>
              <button type="button" onClick={() => onCommand(bot, 'resume')}>
                再開
              </button>
              <button type="button" className="danger-button" onClick={() => onCommand(bot, 'stop')}>
                停止
              </button>
            </>
          )}
        </span>
      </div>
    </li>
  )
}
