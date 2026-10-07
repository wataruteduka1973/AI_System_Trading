import {
  AreaSeries,
  ColorType,
  createChart,
  LineStyle,
  type Time,
  type UTCTimestamp,
} from 'lightweight-charts'
import { useEffect, useRef } from 'react'
import type { EquityCurve } from './types'

const toTimestamp = (time: string) => Math.floor(new Date(time).getTime() / 1000) as UTCTimestamp

/** Equity over the run, with the starting equity as a dashed line. The stored curve is
 * already downsampled, so every point is drawn. */
export default function EquityCurveChart({ curve }: { curve: EquityCurve }) {
  const containerRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    if (!containerRef.current || curve.points.length === 0) return
    const dateTime = new Intl.DateTimeFormat('ja-JP', {
      timeZone: 'Asia/Tokyo',
      year: '2-digit',
      month: '2-digit',
      day: '2-digit',
    })
    const chart = createChart(containerRef.current, {
      autoSize: true,
      height: 280,
      layout: { background: { type: ColorType.Solid, color: '#081426' }, textColor: '#b9cbe0' },
      grid: {
        vertLines: { color: 'rgba(75, 104, 139, 0.2)' },
        horzLines: { color: 'rgba(75, 104, 139, 0.2)' },
      },
      rightPriceScale: { borderColor: '#38506f' },
      timeScale: {
        borderColor: '#38506f',
        tickMarkFormatter: (time: Time) => dateTime.format(new Date(Number(time) * 1000)),
      },
      localization: {
        timeFormatter: (time: Time) =>
          new Date(Number(time) * 1000).toLocaleString('ja-JP', { timeZone: 'Asia/Tokyo' }),
      },
    })
    const series = chart.addSeries(AreaSeries, {
      lineColor: '#4da3ff',
      topColor: 'rgba(77, 163, 255, 0.35)',
      bottomColor: 'rgba(77, 163, 255, 0.02)',
      title: '資産',
    })
    // The chart needs strictly increasing times; two bars cannot share a second.
    const seen = new Set<number>()
    series.setData(
      curve.points.flatMap((point) => {
        const time = toTimestamp(point.time)
        if (seen.has(time)) return []
        seen.add(time)
        return [{ time, value: Number(point.equity) }]
      }),
    )
    if (curve.initial_equity !== null) {
      series.createPriceLine({
        price: Number(curve.initial_equity),
        color: '#8aa0b8',
        lineStyle: LineStyle.Dashed,
        lineWidth: 1,
        title: '開始時',
      })
    }
    chart.timeScale().fitContent()
    return () => chart.remove()
  }, [curve])

  if (curve.points.length === 0) {
    return (
      <p className="panel-description">
        この実行には資産曲線が保存されていません(曲線を保存する前に実行したものです)。
      </p>
    )
  }
  return <div ref={containerRef} className="equity-curve-chart" aria-label="資産曲線" />
}
