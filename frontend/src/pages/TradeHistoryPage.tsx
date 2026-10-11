import type { ReactNode } from 'react'

export default function TradeHistoryPage({ children }: { children: ReactNode }) {
  return (
    <>
      <header className="page-header">
        <p className="eyebrow">TRADE HISTORY</p>
        <h1>取引履歴</h1>
        <p className="subtitle">Botが行った約定と建玉を、あとから確認します。</p>
      </header>
      {children}
    </>
  )
}
