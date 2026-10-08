import type { ReactNode } from 'react'

export default function EventLogPage({ children }: { children: ReactNode }) {
  return (
    <>
      <header className="page-header">
        <p className="eyebrow">EVENT LOG</p>
        <h1>イベントログ</h1>
        <p className="subtitle">通知や取引停止の経緯を、あとから検索して追えます。</p>
      </header>
      {children}
    </>
  )
}
