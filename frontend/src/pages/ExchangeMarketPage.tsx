import type { ReactNode } from 'react'
import type { ExchangeCode } from '../app/routes'

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
        データ環境: {exchange === 'oanda' ? 'OANDA Practice' : 'Binance Spot Testnet'} / 注文送信なし
      </div>
      {children}
    </>
  )
}
