import { useState } from 'react'
import { apiBaseUrl, apiErrorMessage } from '../../lib/api'
import type { WorkspaceInstrument } from './types'

export function useInstruments(ownerToken: string) {
  const [workspaceInstruments, setWorkspaceInstruments] = useState<WorkspaceInstrument[]>([])
  const [instrumentMessage, setInstrumentMessage] = useState(
    '利用口座を選択すると、取引所から最新の銘柄ルールを同期できます。',
  )
  const [selectedInstrumentId, setSelectedInstrumentId] = useState('')

  const load = async (workspaceId: string) => {
    setWorkspaceInstruments([])
    setSelectedInstrumentId('')
    if (!workspaceId) return
    try {
      const response = await fetch(`${apiBaseUrl}/api/v1/workspaces/${workspaceId}/instruments`, {
        headers: { 'X-Owner-Token': ownerToken },
      })
      if (response.ok) {
        const instruments = (await response.json()) as WorkspaceInstrument[]
        setWorkspaceInstruments(instruments)
        setSelectedInstrumentId(instruments[0]?.id ?? '')
      }
    } catch {
      // A network failure here leaves the instrument list empty; the workspace-level
      // message (set by the connections load that runs alongside this) still reports status.
    }
  }

  const syncInstruments = async (workspaceId: string) => {
    if (!workspaceId) return
    setInstrumentMessage('選択済み口座から銘柄ルールを同期しています。')
    try {
      const response = await fetch(
        `${apiBaseUrl}/api/v1/workspaces/${workspaceId}/instruments/sync`,
        { method: 'POST', headers: { 'X-Owner-Token': ownerToken } },
      )
      if (!response.ok) {
        setInstrumentMessage(await apiErrorMessage(response, '銘柄ルールの同期に失敗しました'))
        return
      }
      const payload = (await response.json()) as { instruments: WorkspaceInstrument[] }
      setWorkspaceInstruments(payload.instruments)
      setSelectedInstrumentId((current) => current || payload.instruments[0]?.id || '')
      setInstrumentMessage(`${payload.instruments.length}件の銘柄ルールを同期しました。`)
    } catch {
      setInstrumentMessage('取引所または銘柄同期APIへ接続できません。')
    }
  }

  return {
    workspaceInstruments,
    instrumentMessage,
    selectedInstrumentId,
    setSelectedInstrumentId,
    load,
    syncInstruments,
  }
}
