import type { BackfillJob } from './types'

/** A market-data error code in words (a code not listed is shown as it is). */
export const marketErrorLabel = (code: string) => ({
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

export const backfillStatusLabel = (status: BackfillJob['status']) =>
  ({ queued: '待機中', running: '取得中', succeeded: '完了', failed: '失敗' })[status]
