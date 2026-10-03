// @vitest-environment jsdom
import '@testing-library/jest-dom/vitest'
import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, expect, it, vi } from 'vitest'
import ConnectionsPanel, { ConnectionRegistrationForms } from './ConnectionsPanel'
import type { ConnectionSummary, WorkspaceAccount } from './types'

afterEach(cleanup)

const noop = () => undefined

const connection = (overrides: Partial<ConnectionSummary> = {}): ConnectionSummary => ({
  id: 'c1',
  exchange_id: 'e1',
  label: 'practice-1',
  environment: 'practice',
  status: 'verified',
  credentials_status: 'saved',
  credentials_updated_at: null,
  verification_outcome: 'success',
  ...overrides,
})

const account = (overrides: Partial<WorkspaceAccount> = {}): WorkspaceAccount => ({
  id: 'a1',
  exchange_code: 'oanda',
  connection_label: 'practice-1',
  connection_status: 'verified',
  account_ref_masked: '***789',
  alias: null,
  currency: 'JPY',
  status: 'active',
  selected: false,
  ...overrides,
})

const renderPanel = (
  connections: ConnectionSummary[],
  accounts: WorkspaceAccount[] = [],
  handlers: Partial<{
    onDisable: (c: ConnectionSummary) => void
    onDelete: (c: ConnectionSummary) => void
    onSelectAccount: (a: WorkspaceAccount) => void
  }> = {},
) =>
  render(
    <ConnectionsPanel
      visible
      connections={connections}
      workspaceAccounts={accounts}
      onVerify={noop}
      onDisable={handlers.onDisable ?? noop}
      onDelete={handlers.onDelete ?? noop}
      onSelectAccount={handlers.onSelectAccount ?? noop}
    />,
  )

it('does not allow deleting a connection that is still active', () => {
  renderPanel([connection({ status: 'verified' })])

  const deleteButton = screen.getByRole('button', { name: '削除' })
  expect(deleteButton).toBeDisabled()
  expect(deleteButton).toHaveAttribute('title', '先に接続を無効化してください')
})

it.each(['disabled', 'invalid', 'revoked', 'pending_credentials'])(
  'allows deleting a %s connection',
  (status) => {
    const onDelete = vi.fn()
    renderPanel([connection({ status })], [], { onDelete })

    fireEvent.click(screen.getByRole('button', { name: '削除' }))
    expect(onDelete).toHaveBeenCalledWith(expect.objectContaining({ status }))
  },
)

it('does not offer disabling a connection that is already disabled', () => {
  const onDisable = vi.fn()
  renderPanel([connection({ status: 'disabled' })], [], { onDisable })

  expect(screen.getByRole('button', { name: '無効化' })).toBeDisabled()
})

it('only lets an active account on a verified connection be selected', () => {
  const onSelectAccount = vi.fn()
  renderPanel(
    [connection()],
    [
      account({ id: 'unverified', connection_status: 'verifying', account_ref_masked: '***111' }),
      account({ id: 'chosen', selected: true, account_ref_masked: '***222' }),
      account({ id: 'usable', account_ref_masked: '***333' }),
    ],
    { onSelectAccount },
  )

  expect(screen.getByRole('button', { name: '接続を再検証してください' })).toBeDisabled()
  expect(screen.getByRole('button', { name: '選択中' })).toBeDisabled()
  fireEvent.click(screen.getByRole('button', { name: 'この口座を利用' }))
  expect(onSelectAccount).toHaveBeenCalledWith(expect.objectContaining({ id: 'usable' }))
})

it('masks credentials and requires them before registering', () => {
  render(
    <ConnectionRegistrationForms
      visible
      connections={[]}
      connectionLabel="practice-1"
      onConnectionLabelChange={noop}
      oandaToken=""
      onOandaTokenChange={noop}
      registrationMessage=""
      verifiedAccounts={[]}
      selectedOandaConnectionId=""
      onSelectedOandaConnectionIdChange={noop}
      onRegisterOanda={noop}
      binanceLabel="testnet-1"
      onBinanceLabelChange={noop}
      binanceApiKey="key"
      onBinanceApiKeyChange={noop}
      binanceSecretKey=""
      onBinanceSecretKeyChange={noop}
      binanceMessage=""
      binanceAccounts={[]}
      selectedBinanceConnectionId=""
      onSelectedBinanceConnectionIdChange={noop}
      onRegisterBinance={noop}
    />,
  )

  for (const label of ['OANDA personal access token', 'Binance API Key', 'Binance Secret Key']) {
    const input = screen.getByLabelText(label)
    expect(input).toHaveAttribute('type', 'password')
    expect(input).toHaveAttribute('autocomplete', 'off')
  }
  // Token missing / secret key missing: neither form can be submitted yet.
  for (const button of screen.getAllByRole('button', { name: '暗号化保存して検証' })) {
    expect(button).toBeDisabled()
  }
})
