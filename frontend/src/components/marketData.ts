export type ChartCandle = {
  open_time: string
  close_time: string
  open: string
  high: string
  low: string
  close: string
  volume: string | null
  source: string
  quality_status: string
}

export function mergeCandlePages(
  current: ChartCandle[],
  incoming: ChartCandle[],
): ChartCandle[] {
  const indexed = new Map(current.map((candle) => [candle.open_time, candle]))
  incoming.forEach((candle) => indexed.set(candle.open_time, candle))
  return [...indexed.values()].sort(
    (left, right) => new Date(left.open_time).getTime() - new Date(right.open_time).getTime(),
  )
}

export function formatPrice(value: string, priceScale: number): string {
  const [integer, fraction = ''] = value.split('.', 2)
  if (priceScale === 0) return integer
  return `${integer}.${fraction.padEnd(priceScale, '0').slice(0, priceScale)}`
}

export function movedTowardOlder(
  previousVisibleFrom: number | null,
  currentVisibleFrom: number,
): boolean {
  return previousVisibleFrom !== null && currentVisibleFrom < previousVisibleFrom - 0.5
}

export function shouldRequestOlder(params: {
  hasUserInteracted: boolean
  previousVisibleFrom: number | null
  currentVisibleFrom: number
  hasOlder: boolean
  loadingOlder: boolean
}): boolean {
  return (
    params.hasUserInteracted &&
    movedTowardOlder(params.previousVisibleFrom, params.currentVisibleFrom) &&
    params.currentVisibleFrom <= 20 &&
    params.hasOlder &&
    !params.loadingOlder
  )
}
