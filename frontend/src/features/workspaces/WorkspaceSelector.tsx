import type { WorkspaceSummary } from './types'

export default function WorkspaceSelector({
  ownerToken,
  onOwnerTokenChange,
  onLoadWorkspaces,
  workspaces,
  selectedWorkspaceId,
  onSelectWorkspace,
  workspaceMessage,
}: {
  ownerToken: string
  onOwnerTokenChange: (value: string) => void
  onLoadWorkspaces: () => void
  workspaces: WorkspaceSummary[]
  selectedWorkspaceId: string
  onSelectWorkspace: (workspaceId: string) => void
  workspaceMessage: string
}) {
  return (
    <>
      <div>
        <p className="eyebrow">DEVELOPMENT OWNER</p>
        <h2>Workspace選択</h2>
        <p className="panel-description">
          Tokenはこの画面のメモリ上だけで使用し、ブラウザへ保存しません。
        </p>
      </div>
      <div className="workspace-controls">
        <label>
          Owner token
          <input
            type="password"
            value={ownerToken}
            onChange={(event) => onOwnerTokenChange(event.target.value)}
            autoComplete="off"
          />
        </label>
        <button type="button" onClick={onLoadWorkspaces} disabled={!ownerToken}>
          読み込む
        </button>
        <label>
          Workspace
          <select
            value={selectedWorkspaceId}
            onChange={(event) => onSelectWorkspace(event.target.value)}
            disabled={workspaces.length === 0}
          >
            <option value="">選択してください</option>
            {workspaces.map((workspace) => (
              <option key={workspace.id} value={workspace.id}>
                {workspace.name} ({workspace.status})
              </option>
            ))}
          </select>
        </label>
      </div>
      <p className="workspace-message">{workspaceMessage}</p>
    </>
  )
}
