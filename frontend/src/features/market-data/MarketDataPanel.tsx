import CandleChart, { type DisplayedRange } from '../../components/CandleChart'
import type { ChartCandle } from '../../components/marketData'
import type { WorkspaceInstrument } from '../instruments/types'
import { statusLabel, type StreamConnectionStatus } from './marketStream'
import type { BackfillJob, CandleCoverage, MarketDataSubscription, Timeframe } from './types'

const marketErrorLabel = (code: string) => ({
  credentials_unreadable: '保存済み資格情報を復号できません。接続管理でAPI資格情報を更新し再検証してください。',
  credentials_missing: '資格情報が不足しています。接続管理でAPI資格情報を登録してください。',
  configuration_error: '接続設定を確認してください。暗号化キー変更後はAPI資格情報の再登録が必要です。',
  worker_interrupted: '前回の取得処理が中断されました。再取得できます。',
  authentication_failed: '取引所の認証に失敗しました。接続管理でAPI資格情報を確認してください。',
  access_unavailable: '有効な接続または選択口座が見つかりません。接続管理を確認してください。',
  invalid_candles: '取引所から不正なローソク足データを受信しました。',
  access_changed: '取得中に接続または口座の設定が変更されました。',
  invalid_checkpoint: '内部の再開位置が不正です。サポートに連絡してください。',
  communication_failed: '取引所との通信に失敗しました。自動的に再試行します。',
  rate_limited: '取引所のレート制限に達しました。自動的に再試行します。',
  internal_error: '内部エラーが発生しました。',
})[code] ?? code

const backfillStatusLabel = (status: BackfillJob['status']) =>
  ({ queued: '待機中', running: '取得中', succeeded: '完了', failed: '失敗' })[status]

export default function MarketDataPanel({
  visible,
  visibleInstruments,
  activeInstrumentId,
  onSelectInstrument,
  selectedWorkspaceId,
  timeframe,
  onTimeframeChange,
  submittingMarketAction,
  onStartAutomaticCollection,
  onStopAutomaticCollection,
  marketDataMessage,
  subscriptions,
  backfillJobs,
  coverage,
  candles,
  marketDataLoading,
  olderCandlesLoading,
  candleError,
  hasOlderCandles,
  onLoadOlder,
  displayedRange,
  onDisplayedRangeChange,
  connectionStatus,
  lastDataAt,
  gapCount,
  lastGapReason,
  liveCandle,
}: {
  visible: boolean
  visibleInstruments: WorkspaceInstrument[]
  activeInstrumentId: string
  onSelectInstrument: (instrumentId: string) => void
  selectedWorkspaceId: string
  timeframe: Timeframe
  onTimeframeChange: (timeframe: Timeframe) => void
  submittingMarketAction: boolean
  onStartAutomaticCollection: () => void
  onStopAutomaticCollection: () => void
  marketDataMessage: string
  subscriptions: MarketDataSubscription[]
  backfillJobs: BackfillJob[]
  coverage: CandleCoverage | null
  candles: ChartCandle[]
  marketDataLoading: boolean
  olderCandlesLoading: boolean
  candleError: string | null
  hasOlderCandles: boolean
  onLoadOlder: () => void
  displayedRange: DisplayedRange
  onDisplayedRangeChange: (range: DisplayedRange) => void
  connectionStatus: StreamConnectionStatus
  lastDataAt: Date | null
  gapCount: number
  lastGapReason: string | null
  liveCandle: ChartCandle | null
}) {
  if (!visible) return null

  if (visibleInstruments.length === 0) {
    return (
      <section className="workspace-panel market-empty-state">
        <h2>表示できる銘柄がありません</h2>
        <p>接続管理ページで利用口座を選択し、銘柄ルールを同期してください。</p>
      </section>
    )
  }

  return (
    <section className="workspace-panel market-data-panel">
      <div>
        <p className="eyebrow">CANDLE DATA</p>
        <h2>ローソク足取得・保存</h2>
        <p className="panel-description">
          確定済みデータだけを保存します。接続管理で銘柄ルールを同期すると、過去1年分の取得と
          自動取得が自動的に始まります(手動での取得開始は不要です)。
        </p>
      </div>
      <div className="market-data-controls">
        <label>
          銘柄
          <select value={activeInstrumentId} onChange={(event) => onSelectInstrument(event.target.value)}>
            {visibleInstruments.map((instrument) => (
              <option key={instrument.id} value={instrument.id}>
                {instrument.exchange_code.toUpperCase()} {instrument.symbol}
              </option>
            ))}
          </select>
        </label>
        <label>
          時間足
          <select value={timeframe} onChange={(event) => onTimeframeChange(event.target.value as Timeframe)}>
            {(['1m', '5m', '15m', '30m', '1h', '4h', '1d'] as Timeframe[]).map((frame) => (
              <option key={frame} value={frame}>{frame}</option>
            ))}
          </select>
        </label>
        <button type="button" disabled={submittingMarketAction} onClick={onStartAutomaticCollection}>この銘柄の全時間足を開始</button>
        <button type="button" disabled={submittingMarketAction} onClick={onStopAutomaticCollection}>この銘柄の全時間足を停止</button>
      </div>
      <p className="workspace-message">{marketDataMessage}</p>
      <p>時間足の選択は表示と手動の過去取得に使用します。自動取得の開始・停止は全7時間足に適用します。</p>
      <p>自動取得中の時間足: {subscriptions.filter((item) => item.instrument_id === activeInstrumentId && item.enabled).map((item) => item.timeframe).join(', ') || 'なし'}。画面の5秒ごとの更新は保存済みデータの読込であり、取引所からの自動取得とは別です。</p>
      {subscriptions
        .filter((item) => item.instrument_id === activeInstrumentId && item.timeframe === timeframe)
        .map((item) => (
          <div
            className={`collection-status ${item.blocked_reason ? 'blocked' : item.enabled ? 'enabled' : 'disabled'}`}
            key={item.id}
          >
            <strong>
              {item.blocked_reason
                ? '自動取得停止中（要確認）'
                : item.enabled ? '自動取得中' : '自動取得停止中'}
            </strong>
            <span>最終成功: {item.last_success_at ? new Date(item.last_success_at).toLocaleString('ja-JP') : 'まだありません'}</span>
            {item.blocked_reason && (
              <span>停止理由: {marketErrorLabel(item.blocked_reason)}（連続失敗{item.consecutive_failures}回）</span>
            )}
            {!item.blocked_reason && item.last_error_code && (
              <span>直近エラー: {marketErrorLabel(item.last_error_code)}</span>
            )}
          </div>
        ))}
      {backfillJobs.length > 0 && (
        <div className={`backfill-status ${backfillJobs[0].status}`}>
          <strong>過去取得 ({timeframe}): {backfillStatusLabel(backfillJobs[0].status)}</strong>
          <span>完了後は5秒以内に再読込します。古い保存済みデータはチャートを左へ移動して表示できます。</span>
          <span>保存件数: {backfillJobs[0].rows_written.toLocaleString()}</span>
          {backfillJobs[0].status === 'queued' && backfillJobs[0].consecutive_failures > 0 && (
            <span>
              次回再試行予定: {new Date(backfillJobs[0].next_run_at).toLocaleString('ja-JP')}
              （連続失敗{backfillJobs[0].consecutive_failures}回）
            </span>
          )}
          {backfillJobs[0].error_code && <span>エラー: {marketErrorLabel(backfillJobs[0].error_code)}</span>}
        </div>
      )}
      {coverage && (
        <div className={`coverage-summary coverage-${coverage.coverage_status}`}>
          <strong>取得範囲: {coverage.coverage_status}</strong>
          <span>
            要求: {coverage.requested_from ? new Date(coverage.requested_from).toLocaleDateString('ja-JP') : '指定なし'}
            {' 〜 '}
            {coverage.requested_to ? new Date(coverage.requested_to).toLocaleDateString('ja-JP') : '指定なし'}
          </span>
          <span>
            保存済み: {coverage.actual_from ? new Date(coverage.actual_from).toLocaleString('ja-JP') : 'データなし'}
            {' 〜 '}
            {coverage.actual_to ? new Date(coverage.actual_to).toLocaleString('ja-JP') : 'データなし'}
          </span>
          <span>保存件数: {coverage.stored_count.toLocaleString()}</span>
          {coverage.source_limitation === 'binance_testnet_periodic_reset' && (
            <span>Binance Testnetの定期リセットにより、要求した1年より短い範囲です。</span>
          )}
        </div>
      )}
      {visibleInstruments.find((item) => item.id === activeInstrumentId) && (
        <CandleChart
          key={`${selectedWorkspaceId}:${activeInstrumentId}:${timeframe}`}
          candles={candles}
          instrument={visibleInstruments.find((item) => item.id === activeInstrumentId)!}
          loadingInitial={marketDataLoading}
          loadingOlder={olderCandlesLoading}
          error={candleError}
          hasOlder={hasOlderCandles}
          onLoadOlder={onLoadOlder}
          onDisplayedRangeChange={onDisplayedRangeChange}
          liveCandle={liveCandle}
        />
      )}
      {connectionStatus !== 'idle' && (
        <div className={`stream-status stream-${connectionStatus}`}>
          <strong>リアルタイム配信: {statusLabel(connectionStatus)}</strong>
          <span>直近データ受信: {lastDataAt ? lastDataAt.toLocaleString('ja-JP') : 'まだありません'}</span>
          <span>gap件数: {gapCount}</span>
          {lastGapReason && <span>直近の遅延理由: {lastGapReason}</span>}
        </div>
      )}
      {displayedRange && (
        <div className="chart-range">
          <strong>チャート表示中</strong>
          <span>{new Date(displayedRange.from).toLocaleString('ja-JP')} 〜 {new Date(displayedRange.to).toLocaleString('ja-JP')}</span>
          <span>{candles.length.toLocaleString()}件をブラウザに読込済み</span>
        </div>
      )}
      {candles.length > 0 && (
        <div className="latest-candle">
          <strong>最新の確定足</strong>
          <span>{new Date(candles[candles.length - 1].open_time).toLocaleString('ja-JP')}</span>
          <span>始値 {candles[candles.length - 1].open}</span>
          <span>高値 {candles[candles.length - 1].high}</span>
          <span>安値 {candles[candles.length - 1].low}</span>
          <span>終値 {candles[candles.length - 1].close}</span>
        </div>
      )}
    </section>
  )
}
