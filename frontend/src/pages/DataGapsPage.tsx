import type { ReactNode } from 'react'

export default function DataGapsPage({ children }: { children: ReactNode }) {
  return (
    <>
      <header className="page-header">
        <p className="eyebrow">DATA GAPS</p>
        <h1>欠損データ</h1>
        <p className="subtitle">ローソク足の欠けている範囲と、その影響・補完の状況を確認します。</p>
      </header>
      {children}
    </>
  )
}
