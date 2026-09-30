import type { ReactNode } from 'react'

export default function TradingPage({ children }: { children: ReactNode }) {
  return (
    <>
      <header className="page-header">
        <p className="eyebrow">BOT MANAGEMENT</p>
        <h1>Bot管理</h1>
        <p className="subtitle">取引口座の作成・入金と、Botの作成・start/pause/resume/stopを行います。</p>
      </header>
      {children}
    </>
  )
}
