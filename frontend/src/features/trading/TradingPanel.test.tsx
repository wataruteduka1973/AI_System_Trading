// @vitest-environment jsdom
import '@testing-library/jest-dom/vitest'
import { act, cleanup, fireEvent, render, renderHook, screen } from '@testing-library/react'
import { afterEach, expect, it, vi } from 'vitest'
import TradingPanel, { TradingForms } from './TradingPanel'
import type { BotRunSummary, TradingBot, TradingHalt } from './types'
import { useTrading } from './useTrading'

afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
  vi.restoreAllMocks()
  vi.useRealTimers()
})

const noop = () => undefined

it('shows the approved strategy and timeframe instead of letting the user pick a timeframe', () => {
  render(
    <TradingForms
      visible
      connections={[]}
      workspaceInstruments={[]}
      tradingAccounts={[]}
      tradingMessage=""
      accountConnectionId=""
      onAccountConnectionIdChange={noop}
      accountBaseCurrency="JPY"
      onAccountBaseCurrencyChange={noop}
      onCreateTradingAccount={noop}
      depositAccountId=""
      onDepositAccountIdChange={noop}
      depositAmount=""
      onDepositAmountChange={noop}
      depositAsset="JPY"
      onDepositAssetChange={noop}
      onCreateDeposit={noop}
      botName=""
      onBotNameChange={noop}
      botAccountId=""
      onBotAccountIdChange={noop}
      botInstrumentId=""
      onBotInstrumentIdChange={noop}
      onCreateBot={noop}
    />,
  )

  const strategy = screen.getByLabelText('戦略・時間足')
  expect(strategy).toHaveValue('4h・Donchian 55/20')
  expect(strategy).toHaveAttribute('readonly')
  expect(screen.queryByLabelText('時間足')).not.toBeInTheDocument()
})

it('creates a bot without sending a timeframe, leaving the choice to the server', async () => {
  const fetchMock = vi.fn<typeof fetch>(async () => new Response('[]', { status: 200 }))
  vi.stubGlobal('fetch', fetchMock)
  const { result } = renderHook(() => useTrading('ws-1'))

  act(() => {
    result.current.setBotName('btc-4h-bot')
    result.current.setBotAccountId('account-1')
    result.current.setBotInstrumentId('instrument-1')
  })
  await act(async () => {
    await result.current.createBot()
  })

  const createCall = fetchMock.mock.calls.find(
    ([url, init]) => String(url).endsWith('/workspaces/ws-1/bots') && init?.method === 'POST',
  )
  expect(createCall).toBeDefined()
  expect(JSON.parse(String(createCall?.[1]?.body))).toEqual({
    name: 'btc-4h-bot',
    account_id: 'account-1',
    instrument_id: 'instrument-1',
  })
})

const runningBot: TradingBot = {
  id: 'b1',
  workspace_id: 'ws',
  name: 'btcusdt-4h-donchian',
  execution_mode: 'paper',
  strategy_mode: 'technical',
  account_id: 'a1',
  instrument_id: 'i1',
  timeframe: '4h',
  desired_state: 'running',
  actual_state: 'running',
  live_trading_enabled: false,
  version: 1,
  created_at: '2026-10-01T00:00:00Z',
}

const latestRun = (lastSignalAt: string): BotRunSummary => ({
  id: 'r1',
  bot_id: 'b1',
  status: 'running',
  code_version: 'dummy-pipeline-0.1',
  started_at: '2026-10-02T13:13:00Z',
  stopped_at: null,
  stop_reason: null,
  heartbeat_at: null,
  latest_signal: { id: 's1', action: 'hold', created_at: lastSignalAt },
})

const renderBots = (lastSignalAt: string) => {
  vi.useFakeTimers({ toFake: ['Date'] })
  // 21 minutes after the 04:00 UTC 4h close, past the 10-minute grace.
  vi.setSystemTime(new Date('2026-10-03T04:21:00Z'))
  render(
    <TradingPanel
      visible
      tradingAccounts={[]}
      bots={[runningBot]}
      latestRuns={{ b1: latestRun(lastSignalAt) }}
      halts={[]}
      onCommand={() => undefined}
      onEmergencyStop={() => undefined}
      onReleaseHalt={() => undefined}
    />,
  )
}

it('warns when a running bot has not evaluated the newest closed bar', () => {
  renderBots('2026-10-02T13:30:08Z')

  const alert = screen.getByRole('alert')
  expect(alert).toHaveTextContent('トレーディングWorkerが止まっている可能性があります')
  expect(alert).toHaveTextContent('btcusdt-4h-donchian')
})

it('shows no warning while the bots keep up', () => {
  renderBots('2026-10-03T04:00:40Z')

  expect(screen.queryByRole('alert')).not.toBeInTheDocument()
})


const emergencyHalt = (overrides: Partial<TradingHalt> = {}): TradingHalt => ({
  id: 'h1',
  scope_type: 'bot',
  scope_id: 'b1',
  level: 'emergency_stopped',
  reason_code: 'user_emergency_stop',
  status: 'active',
  halted_at: '2026-10-06T01:00:00Z',
  released_at: null,
  ...overrides,
})

const renderControls = (halts: TradingHalt[] = []) => {
  const onEmergencyStop = vi.fn()
  const onReleaseHalt = vi.fn()
  render(
    <TradingPanel
      visible
      tradingAccounts={[]}
      bots={[runningBot]}
      latestRuns={{}}
      halts={halts}
      onCommand={() => undefined}
      onEmergencyStop={onEmergencyStop}
      onReleaseHalt={onReleaseHalt}
    />,
  )
  return { onEmergencyStop, onReleaseHalt }
}

it('emergency-stops one bot only after confirmation, leaving positions open by default', () => {
  const confirm = vi.spyOn(window, 'confirm')
  const { onEmergencyStop } = renderControls()

  confirm.mockReturnValueOnce(false)
  fireEvent.click(screen.getByRole('button', { name: '緊急停止' }))
  expect(onEmergencyStop).not.toHaveBeenCalled()

  confirm.mockReturnValueOnce(true)
  fireEvent.click(screen.getByRole('button', { name: '緊急停止' }))
  expect(onEmergencyStop).toHaveBeenCalledWith(runningBot, false)
  expect(confirm.mock.calls[1][0]).toContain('建玉はそのまま残します')
})

it('stops the whole workspace and closes positions when that policy is chosen', () => {
  const confirm = vi.spyOn(window, 'confirm').mockReturnValue(true)
  const { onEmergencyStop } = renderControls()

  fireEvent.click(screen.getByLabelText('建玉を成行で決済する'))
  fireEvent.click(screen.getByRole('button', { name: '全Botを緊急停止' }))

  expect(onEmergencyStop).toHaveBeenCalledWith(null, true)
  expect(confirm.mock.calls[0][0]).toContain('建玉は成行で決済します')
})

it('shows an active emergency stop and offers its release', () => {
  const { onReleaseHalt } = renderControls([
    emergencyHalt(),
    emergencyHalt({ id: 'h2', level: 'entry_halted', reason_code: 'data_delay' }),
  ])

  const alert = screen.getByRole('alert')
  expect(alert).toHaveTextContent('緊急停止中です')
  expect(alert).toHaveTextContent('btcusdt-4h-donchian')
  fireEvent.click(screen.getByRole('button', { name: '緊急停止を解除' }))
  expect(onReleaseHalt).toHaveBeenCalledWith(emergencyHalt())
  expect(screen.getAllByRole('button', { name: '緊急停止を解除' })).toHaveLength(1) // only emergency level
})

it('labels a workspace-wide emergency stop', () => {
  renderControls([emergencyHalt({ scope_type: 'workspace', scope_id: null })])

  expect(screen.getByRole('alert')).toHaveTextContent('ワークスペース全体')
})

it('posts the emergency stop with the close policy and reports the outcome', async () => {
  const stopResult = {
    halt_id: 'h1',
    scope_type: 'bot',
    scope_id: 'b1',
    level: 'emergency_stopped',
    already_active: false,
    stopped_bot_ids: ['b1'],
    bot_stop_failures: [],
    closing_order_ids: ['o1'],
    close_failures: [{ position_id: 'p2', code: 'market_price_unavailable' }],
  }
  const fetchMock = vi.fn<typeof fetch>(async (input, init) =>
    String(input).endsWith('/emergency-stop') && init?.method === 'POST'
      ? new Response(JSON.stringify(stopResult), { status: 200 })
      : new Response('[]', { status: 200 }),
  )
  vi.stubGlobal('fetch', fetchMock)
  const { result } = renderHook(() => useTrading('ws-1'))

  await act(async () => {
    await result.current.emergencyStop(runningBot, true)
  })

  const call = fetchMock.mock.calls.find(([url]) => String(url).endsWith('/bots/b1/emergency-stop'))
  expect(call).toBeDefined()
  expect(JSON.parse(String(call?.[1]?.body))).toEqual({ close_positions: true })
  expect(result.current.tradingMessage).toContain('緊急停止しました')
  expect(result.current.tradingMessage).toContain('決済注文 1件')
  expect(result.current.tradingMessage).toContain('一部を処理できませんでした(1件)')
})

it('stops the whole workspace through the workspace endpoint', async () => {
  const fetchMock = vi.fn<typeof fetch>(async () => new Response('[]', { status: 200 }))
  vi.stubGlobal('fetch', fetchMock)
  const { result } = renderHook(() => useTrading('ws-1'))

  await act(async () => {
    await result.current.emergencyStop(null, false)
  })

  expect(
    fetchMock.mock.calls.some(
      ([url, init]) => String(url).endsWith('/workspaces/ws-1/emergency-stop') && init?.method === 'POST',
    ),
  ).toBe(true)
})
