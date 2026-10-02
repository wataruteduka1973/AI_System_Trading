// @vitest-environment jsdom
import '@testing-library/jest-dom/vitest'
import { act, cleanup, render, renderHook, screen } from '@testing-library/react'
import { afterEach, expect, it, vi } from 'vitest'
import { TradingForms } from './TradingPanel'
import { useTrading } from './useTrading'

afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
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
