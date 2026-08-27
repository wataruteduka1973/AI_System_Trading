import type { ReactNode } from 'react'

export default function ConnectionManagementPage({ children }: { children: ReactNode }) {
  return (
    <>
      <header className="page-header">
        <p className="eyebrow">CONNECTION MANAGEMENT</p>
        <h1>口座・接続管理</h1>
        <p className="subtitle">資格情報、認証状態、利用口座、銘柄ルールをWorkspaceごとに管理します。</p>
      </header>
      {children}
    </>
  )
}
