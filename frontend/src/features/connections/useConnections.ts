import { useState } from 'react'
import { apiBaseUrl, apiErrorMessage } from '../../lib/api'
import type { BinanceVerification, ConnectionSummary, OandaVerification, WorkspaceAccount } from './types'

/** `ownerToken`/`selectedWorkspaceId` are shared across features (see useWorkspaces); this hook
 * receives them rather than owning them. `setWorkspaceMessage` is the same shared status line
 * used by workspace loading, so connection actions report into it too. */
export function useConnections(
  ownerToken: string,
  selectedWorkspaceId: string,
  setWorkspaceMessage: (message: string) => void,
) {
  const ownerHeaders = { 'X-Owner-Token': ownerToken }

  const [connections, setConnections] = useState<ConnectionSummary[]>([])
  const [workspaceAccounts, setWorkspaceAccounts] = useState<WorkspaceAccount[]>([])
  const [connectionLabel, setConnectionLabel] = useState('OANDA practice')
  const [oandaToken, setOandaToken] = useState('')
  const [registrationMessage, setRegistrationMessage] = useState(
    'OANDA practice Tokenは登録後すぐに暗号化され、検証後に入力欄から消去されます。',
  )
  const [verifiedAccounts, setVerifiedAccounts] = useState<OandaVerification['accounts']>([])
  const [selectedOandaConnectionId, setSelectedOandaConnectionId] = useState('')
  const [binanceLabel, setBinanceLabel] = useState('Binance Spot Testnet')
  const [binanceApiKey, setBinanceApiKey] = useState('')
  const [binanceSecretKey, setBinanceSecretKey] = useState('')
  const [binanceMessage, setBinanceMessage] = useState(
    'API KeyとSecret Keyは暗号化保存され、検証後に入力欄から消去されます。',
  )
  const [binanceAccounts, setBinanceAccounts] = useState<BinanceVerification['accounts']>([])
  const [selectedBinanceConnectionId, setSelectedBinanceConnectionId] = useState('')

  const load = async (workspaceId: string): Promise<string | undefined> => {
    setConnections([])
    setWorkspaceAccounts([])
    setSelectedOandaConnectionId('')
    setSelectedBinanceConnectionId('')
    if (!workspaceId) return undefined
    try {
      const response = await fetch(
        `${apiBaseUrl}/api/v1/workspaces/${workspaceId}/connections`,
        { headers: ownerHeaders },
      )
      if (!response.ok) {
        return `接続一覧の取得に失敗しました（HTTP ${response.status}）。`
      }
      const loaded = (await response.json()) as ConnectionSummary[]
      setConnections(loaded)
      const accountsResponse = await fetch(
        `${apiBaseUrl}/api/v1/workspaces/${workspaceId}/accounts`,
        { headers: ownerHeaders },
      )
      if (accountsResponse.ok) {
        setWorkspaceAccounts((await accountsResponse.json()) as WorkspaceAccount[])
      }
      return `選択中のWorkspaceには${loaded.length}件の取引所接続があります。`
    } catch {
      return '接続一覧APIへ接続できません。'
    }
  }

  const manageConnection = async (connection: ConnectionSummary, action: 'disable' | 'delete') => {
    if (!selectedWorkspaceId) return
    try {
      const response = await fetch(
        `${apiBaseUrl}/api/v1/workspaces/${selectedWorkspaceId}/connections/${connection.id}${
          action === 'disable' ? '/disable' : ''
        }`,
        { method: action === 'disable' ? 'POST' : 'DELETE', headers: ownerHeaders },
      )
      setWorkspaceMessage(
        response.ok
          ? action === 'disable'
            ? '接続を無効化しました。選択中だった口座も解除しました。'
            : '接続と暗号化済み資格情報を削除しました。'
          : await apiErrorMessage(response, action === 'disable' ? '無効化に失敗しました' : '削除に失敗しました'),
      )
      await load(selectedWorkspaceId)
    } catch {
      setWorkspaceMessage(
        action === 'disable'
          ? '接続の無効化APIへ接続できません。バックエンドのログを確認してください。'
          : '接続の削除APIへ接続できません。バックエンドのログを確認してください。',
      )
    }
  }

  const selectAccount = async (account: WorkspaceAccount) => {
    if (!selectedWorkspaceId) return
    try {
      const response = await fetch(
        `${apiBaseUrl}/api/v1/workspaces/${selectedWorkspaceId}/account-selections/${account.exchange_code}`,
        {
          method: 'PUT',
          headers: { ...ownerHeaders, 'Content-Type': 'application/json' },
          body: JSON.stringify({ external_account_id: account.id }),
        },
      )
      setWorkspaceMessage(
        response.ok
          ? `${account.exchange_code.toUpperCase()}で利用する口座を選択しました。`
          : await apiErrorMessage(response, '口座選択に失敗しました'),
      )
      await load(selectedWorkspaceId)
    } catch {
      setWorkspaceMessage('口座選択APIへ接続できません。')
    }
  }

  const verifyOandaConnection = async (connectionId: string) => {
    if (!selectedWorkspaceId) return
    setRegistrationMessage('TokenをOANDA practiceで検証しています。')
    setVerifiedAccounts([])
    try {
      const response = await fetch(
        `${apiBaseUrl}/api/v1/workspaces/${selectedWorkspaceId}/connections/${connectionId}/verify`,
        { method: 'POST', headers: ownerHeaders },
      )
      if (!response.ok) {
        setRegistrationMessage(await apiErrorMessage(response, 'OANDA検証に失敗しました'))
        await load(selectedWorkspaceId)
        return
      }
      const verification = (await response.json()) as OandaVerification
      setVerifiedAccounts(verification.accounts)
      setRegistrationMessage(
        `OANDA接続を検証し、${verification.accounts.length}件の口座を同期しました。`,
      )
      await load(selectedWorkspaceId)
    } catch {
      setRegistrationMessage('OANDA APIへの通信に失敗しました。')
    }
  }

  const registerAndVerifyOanda = async () => {
    const isUpdate = Boolean(selectedOandaConnectionId)
    if (!selectedWorkspaceId || !oandaToken || (!isUpdate && !connectionLabel.trim())) return
    setRegistrationMessage(
      isUpdate ? '保存済みTokenを暗号化更新して再検証しています。' : '接続情報を暗号化して登録しています。',
    )
    setVerifiedAccounts([])
    try {
      const saveResponse = await fetch(
        isUpdate
          ? `${apiBaseUrl}/api/v1/workspaces/${selectedWorkspaceId}/connections/${selectedOandaConnectionId}/credentials`
          : `${apiBaseUrl}/api/v1/workspaces/${selectedWorkspaceId}/connections`,
        {
          method: isUpdate ? 'PUT' : 'POST',
          headers: { ...ownerHeaders, 'Content-Type': 'application/json' },
          body: JSON.stringify(
            isUpdate
              ? { credentials: { token: oandaToken } }
              : {
                  exchange_code: 'oanda',
                  label: connectionLabel.trim(),
                  environment: 'practice',
                  api_base_url: 'https://api-fxpractice.oanda.com',
                  credentials: { token: oandaToken },
                },
          ),
        },
      )
      setOandaToken('')
      if (!saveResponse.ok) {
        setRegistrationMessage(
          await apiErrorMessage(saveResponse, isUpdate ? 'Token更新・再検証に失敗しました' : '接続登録に失敗しました'),
        )
        await load(selectedWorkspaceId)
        return
      }
      if (isUpdate) {
        const verification = (await saveResponse.json()) as OandaVerification
        setVerifiedAccounts(verification.accounts)
        setRegistrationMessage('新しいTokenを暗号化保存し、OANDAでの再検証に成功しました。')
        await load(selectedWorkspaceId)
      } else {
        const connection = (await saveResponse.json()) as ConnectionSummary
        await verifyOandaConnection(connection.id)
      }
    } catch {
      setOandaToken('')
      setRegistrationMessage('接続登録またはOANDA APIへの通信に失敗しました。')
    }
  }

  const verifyBinanceConnection = async (connectionId: string) => {
    if (!selectedWorkspaceId) return
    setBinanceMessage('Binance Spot TestnetでAPI資格情報を検証しています。')
    setBinanceAccounts([])
    try {
      const response = await fetch(
        `${apiBaseUrl}/api/v1/workspaces/${selectedWorkspaceId}/connections/${connectionId}/verify`,
        { method: 'POST', headers: ownerHeaders },
      )
      if (!response.ok) {
        setBinanceMessage(await apiErrorMessage(response, 'Binance検証に失敗しました'))
        await load(selectedWorkspaceId)
        return
      }
      const verification = (await response.json()) as BinanceVerification
      setBinanceAccounts(verification.accounts)
      setBinanceMessage('Binance Spot Testnet接続を検証し、口座情報を同期しました。')
      await load(selectedWorkspaceId)
    } catch {
      setBinanceMessage('Binance Spot Testnet APIへの通信に失敗しました。')
    }
  }

  const registerAndVerifyBinance = async () => {
    const isUpdate = Boolean(selectedBinanceConnectionId)
    if (
      !selectedWorkspaceId ||
      !binanceApiKey ||
      !binanceSecretKey ||
      (!isUpdate && !binanceLabel.trim())
    ) return
    setBinanceMessage(
      isUpdate ? '保存済みAPI資格情報を暗号化更新して再検証しています。' : '接続情報を暗号化して登録しています。',
    )
    setBinanceAccounts([])
    try {
      const saveResponse = await fetch(
        isUpdate
          ? `${apiBaseUrl}/api/v1/workspaces/${selectedWorkspaceId}/connections/${selectedBinanceConnectionId}/credentials`
          : `${apiBaseUrl}/api/v1/workspaces/${selectedWorkspaceId}/connections`,
        {
          method: isUpdate ? 'PUT' : 'POST',
          headers: { ...ownerHeaders, 'Content-Type': 'application/json' },
          body: JSON.stringify(
            isUpdate
              ? { credentials: { api_key: binanceApiKey, secret_key: binanceSecretKey } }
              : {
                  exchange_code: 'binance',
                  label: binanceLabel.trim(),
                  environment: 'testnet',
                  api_base_url: 'https://testnet.binance.vision',
                  credentials: { api_key: binanceApiKey, secret_key: binanceSecretKey },
                },
          ),
        },
      )
      setBinanceApiKey('')
      setBinanceSecretKey('')
      if (!saveResponse.ok) {
        setBinanceMessage(
          await apiErrorMessage(saveResponse, isUpdate ? 'API資格情報の更新・再検証に失敗しました' : '接続登録に失敗しました'),
        )
        await load(selectedWorkspaceId)
        return
      }
      if (isUpdate) {
        const verification = (await saveResponse.json()) as BinanceVerification
        setBinanceAccounts(verification.accounts)
        setBinanceMessage('新しいAPI資格情報を暗号化保存し、Binanceでの再検証に成功しました。')
        await load(selectedWorkspaceId)
      } else {
        const connection = (await saveResponse.json()) as ConnectionSummary
        await verifyBinanceConnection(connection.id)
      }
    } catch {
      setBinanceApiKey('')
      setBinanceSecretKey('')
      setBinanceMessage('接続登録またはBinance APIへの通信に失敗しました。')
    }
  }

  return {
    connections,
    workspaceAccounts,
    connectionLabel,
    setConnectionLabel,
    oandaToken,
    setOandaToken,
    registrationMessage,
    verifiedAccounts,
    selectedOandaConnectionId,
    setSelectedOandaConnectionId,
    binanceLabel,
    setBinanceLabel,
    binanceApiKey,
    setBinanceApiKey,
    binanceSecretKey,
    setBinanceSecretKey,
    binanceMessage,
    binanceAccounts,
    selectedBinanceConnectionId,
    setSelectedBinanceConnectionId,
    load,
    manageConnection,
    selectAccount,
    verifyOandaConnection,
    registerAndVerifyOanda,
    verifyBinanceConnection,
    registerAndVerifyBinance,
  }
}
