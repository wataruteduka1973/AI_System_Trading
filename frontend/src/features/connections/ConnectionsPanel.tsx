import type { BinanceVerification, ConnectionSummary, OandaVerification, WorkspaceAccount } from './types'

const verificationLabel = (outcome: ConnectionSummary['verification_outcome']) =>
  ({
    not_verified: '未検証',
    success: '認証成功',
    authentication_failed: '認証失敗',
    communication_failed: '通信失敗',
  })[outcome]

const connectionStatusLabel = (status: string) =>
  ({
    verified: '認証成功',
    verifying: '検証中（再検証が必要）',
    invalid: '認証または通信に失敗',
    disabled: '無効',
    pending_credentials: '資格情報待ち',
  })[status] ?? status

/** Rendered inside the shared workspace-panel section, directly after WorkspaceSelector,
 * so it stays visually grouped with it (no own wrapping section). */
export default function ConnectionsPanel({
  visible,
  connections,
  workspaceAccounts,
  onVerify,
  onDisable,
  onDelete,
  onSelectAccount,
}: {
  visible: boolean
  connections: ConnectionSummary[]
  workspaceAccounts: WorkspaceAccount[]
  onVerify: (connection: ConnectionSummary) => void
  onDisable: (connection: ConnectionSummary) => void
  onDelete: (connection: ConnectionSummary) => void
  onSelectAccount: (account: WorkspaceAccount) => void
}) {
  return (
    <>
      {visible && connections.length > 0 && (
        <ul className="connection-list">
          {connections.map((connection) => (
            <li key={connection.id}>
              <strong>{connection.label}</strong>
              <span>{connection.environment}</span>
              <span className={`connection-badge credential-${connection.credentials_status}`}>
                {connection.credentials_status === 'saved' ? '資格情報保存済み' : '資格情報未保存'}
              </span>
              <span className={`connection-badge verification-${connection.verification_outcome}`}>
                {verificationLabel(connection.verification_outcome)}
              </span>
              <button type="button" onClick={() => onVerify(connection)}>
                検証
              </button>
              <button
                type="button"
                className="secondary-button"
                disabled={connection.status === 'disabled'}
                onClick={() => onDisable(connection)}
              >
                無効化
              </button>
              <button
                type="button"
                className="danger-button"
                disabled={!['disabled', 'invalid', 'revoked', 'pending_credentials'].includes(connection.status)}
                title={
                  ['disabled', 'invalid', 'revoked', 'pending_credentials'].includes(connection.status)
                    ? '保存済み資格情報を含む接続を削除します'
                    : '先に接続を無効化してください'
                }
                onClick={() => onDelete(connection)}
              >
                削除
              </button>
            </li>
          ))}
        </ul>
      )}
      {visible && workspaceAccounts.length > 0 && (
        <div className="account-selection">
          <h3>Workspaceで利用する口座</h3>
          <ul className="account-list">
            {workspaceAccounts.map((account) => (
              <li key={account.id}>
                <strong>{account.alias || account.account_ref_masked}</strong>
                <span>{account.exchange_code.toUpperCase()} / {account.connection_label}</span>
                <span>接続状態: {connectionStatusLabel(account.connection_status)}</span>
                <span>{account.currency}</span>
                <button
                  type="button"
                  disabled={
                    account.selected ||
                    account.status !== 'active' ||
                    account.connection_status !== 'verified'
                  }
                  onClick={() => onSelectAccount(account)}
                >
                  {account.selected
                    ? '選択中'
                    : account.connection_status !== 'verified'
                      ? '接続を再検証してください'
                      : 'この口座を利用'}
                </button>
              </li>
            ))}
          </ul>
        </div>
      )}
    </>
  )
}

export function ConnectionRegistrationForms({
  visible,
  connections,
  connectionLabel,
  onConnectionLabelChange,
  oandaToken,
  onOandaTokenChange,
  registrationMessage,
  verifiedAccounts,
  selectedOandaConnectionId,
  onSelectedOandaConnectionIdChange,
  onRegisterOanda,
  binanceLabel,
  onBinanceLabelChange,
  binanceApiKey,
  onBinanceApiKeyChange,
  binanceSecretKey,
  onBinanceSecretKeyChange,
  binanceMessage,
  binanceAccounts,
  selectedBinanceConnectionId,
  onSelectedBinanceConnectionIdChange,
  onRegisterBinance,
}: {
  visible: boolean
  connections: ConnectionSummary[]
  connectionLabel: string
  onConnectionLabelChange: (value: string) => void
  oandaToken: string
  onOandaTokenChange: (value: string) => void
  registrationMessage: string
  verifiedAccounts: OandaVerification['accounts']
  selectedOandaConnectionId: string
  onSelectedOandaConnectionIdChange: (value: string) => void
  onRegisterOanda: () => void
  binanceLabel: string
  onBinanceLabelChange: (value: string) => void
  binanceApiKey: string
  onBinanceApiKeyChange: (value: string) => void
  binanceSecretKey: string
  onBinanceSecretKeyChange: (value: string) => void
  binanceMessage: string
  binanceAccounts: BinanceVerification['accounts']
  selectedBinanceConnectionId: string
  onSelectedBinanceConnectionIdChange: (value: string) => void
  onRegisterBinance: () => void
}) {
  if (!visible) return null
  return (
    <>
      <section className="workspace-panel connection-registration">
        <div>
          <p className="eyebrow">OANDA PRACTICE</p>
          <h2>取引所接続を登録</h2>
          <p className="panel-description">
            読取専用の口座確認を行います。外部注文は送信しません。
          </p>
        </div>
        <div className="registration-grid">
          <label>
            操作
            <select
              value={selectedOandaConnectionId}
              onChange={(event) => onSelectedOandaConnectionIdChange(event.target.value)}
            >
              <option value="">新しい接続を登録</option>
              {connections
                .filter((connection) => connection.environment === 'practice')
                .map((connection) => (
                  <option key={connection.id} value={connection.id}>
                    {connection.label}を更新 ({connection.status})
                  </option>
                ))}
            </select>
          </label>
          <label>
            接続名
            <input
              value={connectionLabel}
              onChange={(event) => onConnectionLabelChange(event.target.value)}
              disabled={Boolean(selectedOandaConnectionId)}
            />
          </label>
          <label>
            OANDA personal access token
            <input
              type="password"
              value={oandaToken}
              onChange={(event) => onOandaTokenChange(event.target.value)}
              autoComplete="off"
            />
          </label>
          <button
            type="button"
            onClick={onRegisterOanda}
            disabled={!oandaToken || (!selectedOandaConnectionId && !connectionLabel.trim())}
          >
            {selectedOandaConnectionId ? 'Tokenを更新して再検証' : '暗号化保存して検証'}
          </button>
        </div>
        <p className="workspace-message">{registrationMessage}</p>
        {verifiedAccounts.length > 0 && (
          <ul className="account-list">
            {verifiedAccounts.map((account) => (
              <li key={account.account_ref_masked}>
                <strong>{account.alias || account.account_ref_masked}</strong>
                <span>{account.currency}</span>
                <span>USD/JPY: {account.usd_jpy_tradeable ? '利用可能' : '利用不可'}</span>
              </li>
            ))}
          </ul>
        )}
      </section>

      <section className="workspace-panel connection-registration">
        <div>
          <p className="eyebrow">BINANCE SPOT TESTNET</p>
          <h2>Binance接続を登録</h2>
          <p className="panel-description">
            署名付きの口座照会だけを行います。外部注文は送信しません。
          </p>
        </div>
        <div className="registration-grid">
          <label>
            操作
            <select
              value={selectedBinanceConnectionId}
              onChange={(event) => onSelectedBinanceConnectionIdChange(event.target.value)}
            >
              <option value="">新しい接続を登録</option>
              {connections
                .filter((connection) => connection.environment === 'testnet')
                .map((connection) => (
                  <option key={connection.id} value={connection.id}>
                    {connection.label}を更新 ({connection.status})
                  </option>
                ))}
            </select>
          </label>
          <label>
            接続名
            <input
              value={binanceLabel}
              onChange={(event) => onBinanceLabelChange(event.target.value)}
              disabled={Boolean(selectedBinanceConnectionId)}
            />
          </label>
          <label>
            Binance API Key
            <input
              type="password"
              value={binanceApiKey}
              onChange={(event) => onBinanceApiKeyChange(event.target.value)}
              autoComplete="off"
            />
          </label>
          <label>
            Binance Secret Key
            <input
              type="password"
              value={binanceSecretKey}
              onChange={(event) => onBinanceSecretKeyChange(event.target.value)}
              autoComplete="off"
            />
          </label>
          <button
            type="button"
            onClick={onRegisterBinance}
            disabled={
              !binanceApiKey ||
              !binanceSecretKey ||
              (!selectedBinanceConnectionId && !binanceLabel.trim())
            }
          >
            {selectedBinanceConnectionId
              ? 'API資格情報を更新して再検証'
              : '暗号化保存して検証'}
          </button>
        </div>
        <p className="workspace-message">{binanceMessage}</p>
        {binanceAccounts.length > 0 && (
          <ul className="account-list">
            {binanceAccounts.map((account) => (
              <li key={account.account_ref_masked}>
                <strong>{account.account_type} ({account.account_ref_masked})</strong>
                <span>残高あり資産: {account.nonzero_asset_count}</span>
                <span>BTC/JPY: {account.btc_jpy_tradeable ? '利用可能' : '利用不可'}</span>
                <span>取引権限: {account.can_trade ? '有効' : '無効'}</span>
              </li>
            ))}
          </ul>
        )}
      </section>
    </>
  )
}
