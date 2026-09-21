import type { WorkspaceOption } from './types'

const ROLE_LABEL: Record<WorkspaceOption['role'], string> = {
  viewer: '閲覧者',
  operator: '操作者',
  owner: 'オーナー',
}

export default function WorkspaceSelector({
  workspaces,
  selectedWorkspaceId,
  onSelectWorkspace,
  workspaceMessage,
}: {
  workspaces: WorkspaceOption[]
  selectedWorkspaceId: string
  onSelectWorkspace: (workspaceId: string) => void
  workspaceMessage: string
}) {
  return (
    <>
      <div>
        <h2>Workspace選択</h2>
        <p className="panel-description">
          ログイン中のユーザーが所属するWorkspaceのみ表示されます。
        </p>
      </div>
      <div className="workspace-controls">
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
                {workspace.name} ({ROLE_LABEL[workspace.role]})
              </option>
            ))}
          </select>
        </label>
      </div>
      <p className="workspace-message">{workspaceMessage}</p>
    </>
  )
}
