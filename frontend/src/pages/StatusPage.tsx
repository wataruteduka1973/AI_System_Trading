import type { ReactNode } from 'react'

export default function StatusPage({ children }: { children: ReactNode }) {
  return (
    <>
      <header className="page-header">
        <p className="eyebrow">SYSTEM STATUS</p>
        <h1>システム状態</h1>
        <p className="subtitle">Worker・通知・取引停止・接続の、いまの状態を確認します。</p>
      </header>
      {children}
    </>
  )
}
