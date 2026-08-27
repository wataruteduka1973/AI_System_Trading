import { Link } from 'react-router'

export default function NotFoundPage() {
  return (
    <section className="workspace-panel not-found-page">
      <p className="eyebrow">404</p>
      <h1>ページが見つかりません</h1>
      <p>URLを確認するか、開発状態ページへ戻ってください。</p>
      <Link to="/">開発状態へ戻る</Link>
    </section>
  )
}
