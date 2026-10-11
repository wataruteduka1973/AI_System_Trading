import type { ReactNode } from 'react'

export default function DecisionsPage({ children }: { children: ReactNode }) {
  return (
    <>
      <header className="page-header">
        <p className="eyebrow">DECISIONS</p>
        <h1>判断の履歴</h1>
        <p className="subtitle">Botが、なぜ取引したか、なぜ取引しなかったかを、足ごとに確認します。</p>
      </header>
      {children}
    </>
  )
}
