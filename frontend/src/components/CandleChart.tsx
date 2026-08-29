import {
  CandlestickSeries,
  ColorType,
  CrosshairMode,
  createChart,
  type IChartApi,
  type ISeriesApi,
  type LogicalRange,
  type Time,
  type UTCTimestamp,
} from 'lightweight-charts'
import { useEffect, useRef, useState } from 'react'
import { formatPrice, shouldRequestOlder, type ChartCandle } from './marketData'

type ChartInstrument = {
  symbol: string
  quote_asset: string
  price_scale: number
  tick_size: string
}

export type DisplayedRange = { from: string; to: string } | null

type Props = {
  candles: ChartCandle[]
  instrument: ChartInstrument
  loadingInitial: boolean
  loadingOlder: boolean
  error: string | null
  hasOlder: boolean
  onLoadOlder: () => void
  onDisplayedRangeChange: (range: DisplayedRange) => void
}

const candleTimestamp = (candle: ChartCandle) =>
  Math.floor(new Date(candle.open_time).getTime() / 1000) as UTCTimestamp

export default function CandleChart({
  candles,
  instrument,
  loadingInitial,
  loadingOlder,
  error,
  hasOlder,
  onLoadOlder,
  onDisplayedRangeChange,
}: Props) {
  const containerRef = useRef<HTMLDivElement>(null)
  const [hovered, setHovered] = useState<ChartCandle | null>(null)
  const chartRef = useRef<IChartApi | null>(null)
  const seriesRef = useRef<ISeriesApi<'Candlestick'> | null>(null)
  const candlesRef = useRef(candles)
  const indexedRef = useRef(new Map<number, ChartCandle>())
  const loadOlderRef = useRef(onLoadOlder)
  const rangeChangeRef = useRef(onDisplayedRangeChange)
  const loadingOlderRef = useRef(loadingOlder)
  const hasOlderRef = useRef(hasOlder)
  const previousVisibleFromRef = useRef<number | null>(null)
  const rangeLoadingReadyRef = useRef(false)
  const hasUserInteractedRef = useRef(false)
  const hasCandles = candles.length > 0

  useEffect(() => {
    loadOlderRef.current = onLoadOlder
    rangeChangeRef.current = onDisplayedRangeChange
    loadingOlderRef.current = loadingOlder
    hasOlderRef.current = hasOlder
  }, [hasOlder, loadingOlder, onDisplayedRangeChange, onLoadOlder])

  useEffect(() => {
    if (!containerRef.current || !hasCandles) return
    previousVisibleFromRef.current = null
    rangeLoadingReadyRef.current = false
    hasUserInteractedRef.current = false
    const dateTime = new Intl.DateTimeFormat('ja-JP', {
      timeZone: 'Asia/Tokyo',
      month: '2-digit',
      day: '2-digit',
      hour: '2-digit',
      minute: '2-digit',
    })
    const chart = createChart(containerRef.current, {
      autoSize: true,
      height: 360,
      layout: { background: { type: ColorType.Solid, color: '#081426' }, textColor: '#b9cbe0' },
      grid: {
        vertLines: { color: 'rgba(75, 104, 139, 0.2)' },
        horzLines: { color: 'rgba(75, 104, 139, 0.2)' },
      },
      crosshair: { mode: CrosshairMode.Normal },
      rightPriceScale: { borderColor: '#38506f' },
      timeScale: {
        borderColor: '#38506f',
        timeVisible: true,
        secondsVisible: false,
        tickMarkFormatter: (time: Time) => dateTime.format(new Date(Number(time) * 1000)),
      },
      localization: {
        timeFormatter: (time: Time) =>
          new Date(Number(time) * 1000).toLocaleString('ja-JP', { timeZone: 'Asia/Tokyo' }),
      },
    })
    const series = chart.addSeries(CandlestickSeries, {
      upColor: '#26a69a',
      downColor: '#ef5350',
      borderVisible: false,
      wickUpColor: '#26a69a',
      wickDownColor: '#ef5350',
      priceFormat: {
        type: 'price',
        precision: instrument.price_scale,
        minMove: Number(instrument.tick_size),
      },
      title: `${instrument.symbol} / ${instrument.quote_asset}`,
    })
    chartRef.current = chart
    seriesRef.current = series
    chart.subscribeCrosshairMove((parameter) => {
      const time = typeof parameter.time === 'number' ? parameter.time : null
      setHovered(time === null ? null : indexedRef.current.get(time) ?? null)
    })
    const markUserInteraction = () => {
      hasUserInteractedRef.current = true
    }
    const container = containerRef.current
    container.addEventListener('pointerdown', markUserInteraction)
    container.addEventListener('wheel', markUserInteraction, { passive: true })
    container.addEventListener('touchstart', markUserInteraction, { passive: true })
    const handleRange = (range: LogicalRange | null) => {
      if (!range) return
      const shouldLoadOlder = shouldRequestOlder({
        hasUserInteracted: hasUserInteractedRef.current,
        previousVisibleFrom: previousVisibleFromRef.current,
        currentVisibleFrom: range.from,
        hasOlder: hasOlderRef.current,
        loadingOlder: loadingOlderRef.current,
      })
      previousVisibleFromRef.current = range.from
      const currentCandles = candlesRef.current
      const first = Math.max(0, Math.floor(range.from))
      const last = Math.min(currentCandles.length - 1, Math.ceil(range.to))
      if (currentCandles[first] && currentCandles[last]) {
        rangeChangeRef.current({
          from: currentCandles[first].open_time,
          to: currentCandles[last].open_time,
        })
      }
      if (
        rangeLoadingReadyRef.current &&
        shouldLoadOlder
      ) {
        loadOlderRef.current()
      }
    }
    chart.timeScale().subscribeVisibleLogicalRangeChange(handleRange)

    return () => {
      chartRef.current = null
      seriesRef.current = null
      container.removeEventListener('pointerdown', markUserInteraction)
      container.removeEventListener('wheel', markUserInteraction)
      container.removeEventListener('touchstart', markUserInteraction)
      chart.remove()
    }
  }, [hasCandles, instrument])

  useEffect(() => {
    candlesRef.current = candles
    const chart = chartRef.current
    const series = seriesRef.current
    if (!chart || !series || candles.length === 0) return
    const previousRange = chart.timeScale().getVisibleLogicalRange()
    const previousFirst = indexedRef.current.values().next().value as ChartCandle | undefined
    indexedRef.current.clear()
    series.setData(
      candles.map((candle) => {
        const time = candleTimestamp(candle)
        indexedRef.current.set(time, candle)
        return {
          time,
          open: Number(candle.open),
          high: Number(candle.high),
          low: Number(candle.low),
          close: Number(candle.close),
        }
      }),
    )
    const addedBefore = previousFirst
      ? candles.findIndex((candle) => candle.open_time === previousFirst.open_time)
      : 0
    if (previousRange && addedBefore > 0) {
      chart.timeScale().setVisibleLogicalRange({
        from: previousRange.from + addedBefore,
        to: previousRange.to + addedBefore,
      })
    } else if (!previousRange) {
      chart.timeScale().fitContent()
      previousVisibleFromRef.current = chart.timeScale().getVisibleLogicalRange()?.from ?? null
      rangeLoadingReadyRef.current = true
    }
  }, [candles, instrument])

  if (loadingInitial) return <p className="chart-state">ローソク足を読み込んでいます。</p>
  if (error && candles.length === 0) return <p className="chart-state chart-error">{error}</p>
  if (candles.length === 0) return <p className="chart-empty">表示できる確定足がまだありません。</p>
  const detail = hovered ?? candles[candles.length - 1]
  return (
    <div className="candle-chart" role="img" aria-label={`${instrument.symbol}のローソク足`}>
      <div className="chart-tooltip" aria-live="polite">
        <strong>{new Date(detail.open_time).toLocaleString('ja-JP', { timeZone: 'Asia/Tokyo' })}</strong>
        <span>始値 {formatPrice(detail.open, instrument.price_scale)}</span>
        <span>高値 {formatPrice(detail.high, instrument.price_scale)}</span>
        <span>安値 {formatPrice(detail.low, instrument.price_scale)}</span>
        <span>終値 {formatPrice(detail.close, instrument.price_scale)}</span>
        <span>出来高 {detail.volume ?? '未提供'}</span>
        <span>取得元 {detail.source}</span><span>品質 {detail.quality_status}</span>
      </div>
      {loadingOlder && <p className="chart-loading-older">古いローソク足を読み込んでいます。</p>}
      {error && <p className="chart-inline-error">{error}</p>}
      <div ref={containerRef} className="candle-chart-canvas" />
    </div>
  )
}
