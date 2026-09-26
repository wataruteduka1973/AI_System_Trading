import type { ReactNode } from 'react'

export default function BacktestPage({ children }: { children: ReactNode }) {
  return (
    <>
      <header className="page-header">
        <p className="eyebrow">BACKTESTS</p>
        <h1>バックテスト</h1>
        <p className="subtitle">過去データでBotと同じロジックを再生し、成績を確認します。</p>
      </header>
      {children}
    </>
  )
}
