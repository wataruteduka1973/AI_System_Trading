import type { ReactNode } from 'react'
import type { ExchangeCode } from '../app/routes'

const dataEnvironment: Record<ExchangeCode, string> = {
  oanda: 'OANDA Practice',
  binance: 'Binance Spot Testnet',
  binance_public: 'Binance本番の公開データ(認証なし・読み取り専用)',
}

const exchangePresentation = {
  oanda: {
    eyebrow: 'OANDA PRACTICE MARKET',
    title: 'OANDAマーケット',
    description: 'Practice環境の価格データ、保存範囲、確定済みローソク足を表示します。',
  },
  binance: {
    eyebrow: 'BINANCE SPOT TESTNET MARKET',
    title: 'Binanceマーケット',
    description: 'Testnetの価格データを表示します。定期リセットにより履歴範囲が制限されます。',
  },
  binance_public: {
    eyebrow: 'BINANCE PUBLIC HISTORY (RESEARCH)',
    title: '公開履歴(検証用)',
    description: 'Binance本番の公開APIから取得した履歴を表示します。閲覧とバックテスト専用です。',
  },
} satisfies Record<ExchangeCode, { eyebrow: string; title: string; description: string }>

export default function ExchangeMarketPage({
  exchange,
  children,
}: {
  exchange: ExchangeCode
  children: ReactNode
}) {
  const presentation = exchangePresentation[exchange]
  return (
    <>
      <header className="page-header">
        <p className="eyebrow">{presentation.eyebrow}</p>
        <h1>{presentation.title}</h1>
        <p className="subtitle">{presentation.description}</p>
      </header>
      <div className={`source-notice source-${exchange}`}>
        データ環境: {dataEnvironment[exchange]} / 注文送信なし
      </div>
      {children}
    </>
  )
}
