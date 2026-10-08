// @vitest-environment jsdom
import '@testing-library/jest-dom/vitest'
import { cleanup, fireEvent, render, screen, within } from '@testing-library/react'
import { afterEach, expect, it, vi } from 'vitest'
import BotCard from './BotCard'
import TradingPanel from './TradingPanel'
import { accountLabel, formatAmount, formatPercent, formatSigned, pnlClass } from './format'
import type { AccountOverview, BotOverview, TradingBot } from './types'

afterEach(cleanup)

const bot: TradingBot = {
  id: 'b1',
  workspace_id: 'w1',
  name: 'btcusdt-4h-donchian',
  execution_mode: 'paper',
  strategy_mode: 'technical',
  account_id: 'ac11ea0b-0000',
  instrument_id: 'i1',
  timeframe: '4h',
  desired_state: 'running',
  actual_state: 'running',
  live_trading_enabled: false,
  version: 1,
  created_at: '2026-10-01T00:00:00Z',
}

const overview = (overrides: Partial<BotOverview> = {}): BotOverview => ({
  bot_id: 'b1',
  name: bot.name,
  symbol: 'BTCUSDT',
  exchange_code: 'binance_public',
  timeframe: '4h',
  desired_state: 'running',
  actual_state: 'running',
  account_id: bot.account_id,
  quote_asset: 'USDT',
  deposits: '166667.000000000000000000',
  cash: '166667.000000000000000000',
  equity: '166667.000000000000000000',
  return_pct: '0',
  realized_pnl: '0',
  fees_paid: '0',
  closed_trades: 0,
  position: null,
  ...overrides,
})

const renderCard = (item: BotOverview | undefined) =>
  render(
    <ul>
      <BotCard
        bot={bot}
        overview={item}
        latestRun={undefined}
        onCommand={() => undefined}
        onConfirmStop={() => undefined}
      />
    </ul>,
  )

it('shows what the bot trades and where, and how it stands', () => {
  renderCard(
    overview({
      equity: '170000',
      return_pct: '1.99982',
      realized_pnl: '3333.5',
      fees_paid: '12.5',
      closed_trades: 4,
    }),
  )

  expect(screen.getByText('BTCUSDT')).toBeInTheDocument()
  expect(screen.getByText('公開履歴')).toBeInTheDocument()
  expect(screen.getByText('170,000 USDT')).toBeInTheDocument()
  expect(screen.getByText(/\+2%\(\+3,333 USDT\)/)).toBeInTheDocument()
  expect(screen.getByText('+3,333.5 USDT')).toBeInTheDocument()
  expect(screen.getByText('12.5 USDT / 4回')).toBeInTheDocument()
  expect(screen.getByText('建玉なし')).toBeInTheDocument()
})

it('shows an open position with its mark price, unrealized result and stop', () => {
  renderCard(
    overview({
      position: {
        side: 'long',
        quantity: '2',
        average_entry_price: '100',
        mark_price: '110',
        unrealized_pnl: '20',
        stop_price: '92.5',
        opened_at: null,
      },
    }),
  )

  const position = screen.getByText('建玉').closest('p') as HTMLElement
  expect(position).toHaveTextContent('買い(ロング) 2 @ 100')
  expect(position).toHaveTextContent('現在値 110')
  expect(within(position).getByText('+20 USDT')).toHaveClass('pnl-positive')
  expect(position).toHaveTextContent('損切り 92.5')
})

it('colours a loss and a gain differently and always shows the sign', () => {
  renderCard(overview({ realized_pnl: '-348.6', return_pct: '-0.02' }))

  expect(screen.getByText('-348.6 USDT')).toHaveClass('pnl-negative')
  expect(formatSigned('-0.0001')).toBe('0')
  expect(formatSigned('5')).toBe('+5')
  expect(pnlClass('5')).toBe('pnl-positive')
  expect(pnlClass('-5')).toBe('pnl-negative')
  expect(pnlClass('0')).toBe('')
})

it('still shows the bot and its controls before the overview has loaded', () => {
  const onCommand = vi.fn()
  render(
    <ul>
      <BotCard
        bot={bot}
        overview={undefined}
        latestRun={undefined}
        onCommand={onCommand}
        onConfirmStop={() => undefined}
      />
    </ul>,
  )

  expect(screen.getByText('btcusdt-4h-donchian')).toBeInTheDocument()
  expect(screen.queryByText('純資産')).not.toBeInTheDocument()
  fireEvent.click(screen.getByRole('button', { name: '一時停止' }))
  expect(onCommand).toHaveBeenCalledWith(bot, 'pause')
})

it('formats money the way a person reads it', () => {
  expect(formatAmount('166667.000000000000000000')).toBe('166,667')
  expect(formatAmount('0.1234567', 4)).toBe('0.1235')
  expect(formatAmount(null)).toBe('—')
  expect(formatPercent('1.5')).toBe('+1.5%')
  expect(formatPercent(null)).toBe('—')
})

it('tells two accounts of the same currency apart by what they are for', () => {
  expect(accountLabel({ id: 'ac11ea0b-1111', base_currency: 'USDT', bot_names: ['btc', 'eth'] })).toBe(
    'USDT口座 ac11ea0b(btc・eth)',
  )
  expect(accountLabel({ id: 'ebe93244-2222', base_currency: 'USDT', bot_names: [] })).toBe(
    'USDT口座 ebe93244(Botなし)',
  )
  expect(accountLabel({ id: 'x1234567-0', base_currency: 'JPY' })).toBe('JPY口座 x1234567(Botなし)')
})

it('totals each currency on its own line and counts running bots and positions', () => {
  const accounts: AccountOverview[] = [
    { id: 'a1', base_currency: 'USDT', mode: 'paper', status: 'active', bot_names: [bot.name], balances: { USDT: '1000' } },
  ]
  render(
    <TradingPanel
      visible
      tradingAccounts={[
        { id: 'a1', workspace_id: 'w1', connection_id: null, mode: 'paper', base_currency: 'USDT', status: 'active', created_at: '' },
      ]}
      bots={[bot]}
      latestRuns={{}}
      halts={[]}
      overviews={[
        overview({
          deposits: '1000',
          equity: '1020',
          position: {
            side: 'long',
            quantity: '1',
            average_entry_price: '100',
            mark_price: '120',
            unrealized_pnl: '20',
            stop_price: null,
            opened_at: null,
          },
        }),
        overview({ bot_id: 'b2', name: 'jpy-bot', quote_asset: 'JPY', deposits: '100', equity: '90' }),
      ]}
      accountOverviews={accounts}
      onCommand={() => undefined}
      onEmergencyStop={() => undefined}
      onReleaseHalt={() => undefined}
    />,
  )

  const totals = screen.getByLabelText('全体の状況')
  expect(totals).toHaveTextContent('稼働中 2 / 2台、建玉あり 1台')
  expect(totals).toHaveTextContent('USDT: 純資産 1,020(入金比 +20 / +2%)')
  expect(totals).toHaveTextContent('JPY: 純資産 90(入金比 -10 / -10%)')
  expect(screen.getByText('USDT口座 a1(btcusdt-4h-donchian)')).toBeInTheDocument()
  expect(screen.getByText('1,000 USDT')).toBeInTheDocument()
})
