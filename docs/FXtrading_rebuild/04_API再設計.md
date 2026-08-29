# API再設計

## 1. 共通規約

- ベースパス: `/api/v1`
- 形式: JSON、UTF-8、日時はUTCのRFC 3339
- 認証: WebはOIDCセッション、外部クライアントは短命OAuth2アクセストークン
- 認可: workspaceとroleを各リクエストで検証
- ID: UUID文字列
- 金額・価格・数量: JSON文字列で返し、浮動小数点への暗黙変換を避ける
- ページング: cursor方式、`limit` は既定50・最大500
- 相関ID: `X-Request-Id`。未指定時はサーバー発行
- 変更競合: `ETag` / `If-Match` またはリソースの `version`
- 作成系の冪等性: `Idempotency-Key` を必須化
- APIバージョンはURLで管理し、フィールド追加は後方互換とする

## 2. エラー形式

```json
{
  "type": "https://example.invalid/problems/validation-error",
  "title": "Request validation failed",
  "status": 422,
  "code": "VALIDATION_ERROR",
  "detail": "One or more fields are invalid.",
  "instance": "/api/v1/bots/...",
  "request_id": "01J...",
  "errors": [
    {"field": "quantity", "code": "MIN_NOTIONAL", "message": "Order value is below the minimum."}
  ]
}
```

内部例外、SQL、SDKレスポンス、APIキーは返さない。代表コードは `UNAUTHORIZED`、`FORBIDDEN`、`NOT_FOUND`、`CONFLICT`、`VALIDATION_ERROR`、`RATE_LIMITED`、`EXCHANGE_UNAVAILABLE`、`TRADING_HALTED` とする。

## 3. エンドポイント一覧

### セッション・workspace

| Method | Path | 用途 |
|---|---|---|
| GET | `/me` | ログイン利用者と権限 |
| GET | `/workspaces/{workspace_id}` | workspace情報 |
| GET | `/workspaces/{workspace_id}/audit-logs` | 監査ログ（Owner） |

### 取引所接続

| Method | Path | 用途 |
|---|---|---|
| GET | `/exchange-connections` | 接続一覧。秘密値は返さない |
| POST | `/exchange-connections` | 接続作成。冪等性キー必須 |
| PATCH | `/exchange-connections/{id}` | ラベル・状態等の変更 |
| PUT | `/exchange-connections/{id}/credentials` | 秘密情報の登録・ローテーション |
| POST | `/exchange-connections/{id}/verify` | 権限・疎通検証。非同期化可 |
| DELETE | `/exchange-connections/{id}` | 未使用時のみ無効化。秘密を破棄 |
| POST | `/exchange-connections/{id}/accounts/sync` | OANDA等の利用可能口座を再同期 |
| GET | `/exchange-connections/{id}/accounts` | 口座能力一覧。外部IDはマスク |
| GET | `/external-accounts/{id}` | hedging、margin、GSLO等の詳細 |
| POST | `/account-selection-policies` | 自動選択条件の新規版 |
| POST | `/account-selection-policies/{id}/simulate` | 候補口座と選択・除外理由を表示 |

接続作成は画面のウィザードから行い、認証情報登録と接続リソース作成を分離する。ブラウザーは秘密を永続保存せず、TLS経由で一度だけバックエンドへ送る。バックエンドはSecret Managerへ保存し、DBには参照だけを記録する。

`POST /exchange-connections/{id}/verify` の結果例:

```json
{
  "status": "verified",
  "capabilities": {
    "market_data_read": true,
    "account_read": true,
    "trade": true,
    "withdrawal": false
  },
  "markets": ["foreign_fx"],
  "accounts": [{"external_ref_masked": "***4821", "type": "margin"}],
  "verified_at": "2026-08-15T03:00:00Z"
}
```

### 市場・ローソク足

| Method | Path | 用途 |
|---|---|---|
| GET | `/exchanges` | 対応取引所 |
| GET | `/instruments?exchange=binance&status=active` | 銘柄一覧 |
| GET | `/instruments/{id}/candles?interval=1m&from=...&to=...&limit=...` | OHLCV取得 |
| GET | `/instruments/{id}/indicators?interval=1m&from=...&indicators=...` | 指標計算結果 |
| GET | `/market-data/quality?instrument_id=...&interval=1m` | 遅延・欠損状態 |
| POST | `/market-data/backfills` | 履歴補完ジョブ作成（Operator以上） |
| GET | `/market-data/backfills/{job_id}` | 補完進捗 |
| POST | `/market-data/gaps/{gap_id}/retry` | 欠損補完の手動再試行 |
| GET | `/market-data/gaps?status=open` | 欠損一覧と影響範囲 |

指標パラメータは大量のフラットなqueryではなく、プリセットIDまたはBase64URLエンコードしない短いJSON相当の構造をPOST計算APIへ送る。共有・再現が必要な設定は `indicator-preset` として保存する。

### 戦略・リスク設定

| Method | Path | 用途 |
|---|---|---|
| GET/POST | `/strategies` | 戦略一覧・作成 |
| GET | `/strategies/{id}` | 戦略詳細 |
| POST | `/strategies/{id}/versions` | 新しい不変バージョン作成 |
| POST | `/strategy-versions/{id}/validate` | 構文・パラメータ検証 |
| GET/POST | `/risk-profiles` | リスク設定一覧・作成 |
| POST | `/risk-profiles/{id}/versions` | 新しい不変バージョン作成 |
| POST | `/strategy-versions/{id}/evaluations` | backtest/walk-forward/paper評価 |
| POST | `/strategy-versions/{id}/approvals` | 環境別承認 |
| POST | `/strategy-versions/{id}/deployments` | 停止中Botへ配備 |
| POST | `/algorithm-deployments/{id}/rollback` | 直前承認版へ切戻し |
| GET | `/risk-templates` | conservative-v1等の初期テンプレート |
| POST | `/risk-profile-versions/{id}/impact-analysis` | 注文量・想定損失・stress loss計算 |

### Bot操作

| Method | Path | 用途 |
|---|---|---|
| GET/POST | `/bots` | Bot一覧・作成 |
| GET | `/bots/{id}` | 設定とdesired/actual state |
| PATCH | `/bots/{id}` | 停止中Botの設定変更 |
| POST | `/bots/{id}/commands` | start/stop/pause/resume。冪等性キー必須 |
| POST | `/bots/{id}/emergency-stop` | 新規注文停止。Owner/Operator |
| GET | `/bots/{id}/runs` | 実行履歴 |
| GET | `/bot-runs/{run_id}/signals` | シグナルと根拠 |
| GET | `/bot-runs/{run_id}/risk-decisions` | リスク判断 |
| GET | `/bots/{id}/halts?status=active` | Botに適用中の停止 |
| PUT | `/bots/{id}/account-selection` | 手動口座または選択ポリシーを指定。停止中のみ |

操作APIは非同期受付とする。

```json
POST /api/v1/bots/8e.../commands
Idempotency-Key: 4e...

{"command": "start", "expected_version": 7}
```

```json
{
  "command_id": "13...",
  "status": "accepted",
  "bot_id": "8e...",
  "requested_at": "2026-08-15T03:00:00Z"
}
```

### 口座・注文・ポジション

| Method | Path | 用途 |
|---|---|---|
| GET | `/accounts` | paper/live口座一覧 |
| GET | `/accounts/{id}/summary` | equity、利用可能残高、当日損益 |
| GET | `/accounts/{id}/balances` | 資産別残高 |
| GET | `/accounts/{id}/positions?status=open` | ポジション |
| GET | `/orders?account_id=...&status=...` | 注文一覧 |
| GET | `/orders/{id}` | 注文・状態履歴・約定 |
| POST | `/orders/{id}/cancel` | 注文取消。冪等性キー必須 |
| GET | `/fills?account_id=...&from=...` | 約定一覧 |
| GET | `/ledger-entries?account_id=...` | 台帳履歴 |

Bot以外からの手動注文はMVPでは提供しない。提供する場合は `/manual-order-intents` として、シグナル起点の注文と区別する。

### バックテスト

| Method | Path | 用途 |
|---|---|---|
| POST | `/backtests` | 非同期実行を作成 |
| GET | `/backtests/{id}` | 状態・条件・要約 |
| GET | `/backtests/{id}/trades` | 取引一覧 |
| GET | `/backtests/{id}/equity-curve` | 資産曲線 |
| POST | `/backtests/{id}/cancel` | 実行取消 |

### AI学習・外部モデルカタログ

| Method | Path | 用途 |
|---|---|---|
| POST | `/dataset-snapshots` | 学習用データを固定し品質検査 |
| GET | `/dataset-snapshots/{id}` | データ範囲、特徴量、品質、checksum |
| POST | `/training-runs` | アプリ内学習ジョブ作成 |
| GET | `/training-runs/{id}` | 進捗、資源利用、指標、生成モデル |
| POST | `/training-runs/{id}/cancel` | 学習取消 |
| POST | `/model-catalog/searches` | 許可カタログのメタデータ検索ジョブ |
| GET | `/model-catalog/searches/{id}` | 候補一覧と除外理由 |
| POST | `/model-candidates/{id}/imports` | revision固定で隔離取得 |
| GET | `/model-candidates/{id}/security-review` | 形式・ライセンス・scan結果 |
| POST | `/model-artifacts/{id}/evaluations` | 共通データで再評価 |
| POST | `/model-artifacts/{id}/approvals` | Ownerによる承認 |
| POST | `/model-artifacts/{id}/deployments` | 停止中Botへ版を配備 |
| GET | `/model-artifacts` | 内製・外部モデル一覧 |

探索リクエストは `task=time-series-forecasting`、対象市場、特徴量、許可ライセンス、safetensors必須等のポリシーを受け取る。検索結果は候補メタデータであり、検索直後に重みをロードしない。

### 運用

| Method | Path | 用途 |
|---|---|---|
| GET | `/health/live` | プロセス生存。認証不要、詳細なし |
| GET | `/health/ready` | DB・キュー等の準備状態。公開範囲を制限 |
| GET | `/system/status` | 利用者向けの構成要素状態 |
| GET | `/notifications` | アプリ内通知 |
| GET | `/events?severity=...&category=...&bot_id=...&correlation_id=...` | 運用イベント検索 |
| GET | `/events/{id}` | イベント詳細と関連リソース |
| POST | `/notifications/{id}/acknowledge` | 通知確認 |
| GET | `/trading-halts?status=active&scope_type=...` | 有効な停止一覧 |
| POST | `/trading-halts` | 手動停止を作成 |
| POST | `/trading-halts/{id}/release` | 条件確認後に停止解除 |

## 4.1 フロントエンド用集約API

画面が多数の細粒度APIを直接組み合わせ続けないよう、読み取り専用の集約APIを用意する。

| Method | Path | 用途 |
|---|---|---|
| GET | `/dashboard/overview` | 全市場のBot、口座、損益、停止、重大イベント |
| GET | `/dashboard/markets/{market_type}` | 外貨FXまたは仮想通貨FXの集約状態 |
| GET | `/bots/{id}/dashboard` | Bot状態、チャート要約、最新判断、注文、停止 |
| GET | `/operations/overview` | 接続、データ品質、補完ジョブ、ワーカー状態 |

## 5. 代表レスポンス

### ローソク足

```json
{
  "instrument": {"id": "...", "symbol": "BTCUSDT"},
  "interval": "1m",
  "items": [
    {
      "open_time": "2026-08-15T03:00:00Z",
      "close_time": "2026-08-15T03:01:00Z",
      "open": "118500.10",
      "high": "118530.00",
      "low": "118480.50",
      "close": "118510.20",
      "volume": "12.3456",
      "quality": "complete"
    }
  ],
  "next_cursor": null
}
```

### Bot状態

```json
{
  "id": "...",
  "name": "BTC paper 1m",
  "mode": "paper",
  "desired_state": "running",
  "actual_state": "running",
  "version": 8,
  "market_data": {"status": "healthy", "lag_seconds": 1.4},
  "last_signal_at": "2026-08-15T03:02:00Z",
  "last_heartbeat_at": "2026-08-15T03:02:03Z",
  "halt": null
}
```

## 6. リアルタイムAPI

画面の3秒ポーリングを廃止し、認証済みWebSocketを使用する。

- 接続: `/api/v1/stream`
- subscribe対象:
  - `candles:{instrument_id}:{interval}`
  - `bot:{bot_id}:status`
  - `account:{account_id}:summary`
  - `orders:{account_id}`
  - `notifications`
  - `events:{workspace_id}`
  - `market-data-gaps:{workspace_id}`
  - `trading-halts:{workspace_id}`
- 各イベントは `event_id`、`type`、`occurred_at`、`sequence`、`data` を持つ。
- 再接続時は最後の `event_id` を送り、保持範囲内なら差分を再送する。
- 注文やBot操作はWebSocketで受けず、監査・冪等性を備えたREST APIで行う。

```json
{
  "event_id": "01J...",
  "type": "order.status_changed",
  "occurred_at": "2026-08-15T03:04:05Z",
  "sequence": 1042,
  "data": {"order_id": "...", "from": "submitted", "to": "filled"}
}
```

## 7. 外部取引所アダプター契約

内部APIと取引所SDKを直接結合しない。アダプターは少なくとも次の操作を実装する。

- `verify_credentials`
- `list_instruments`
- `stream_market_data`
- `fetch_candles`
- `get_account_snapshot`
- `submit_order`
- `cancel_order`
- `get_order`
- `list_open_orders`
- `list_recent_fills`
- `get_margin_state`
- `get_positions`
- `set_leverage`（対応市場のみ、Owner制限）

共通DTOへ正規化し、取引所固有の生データは秘密除去後の参照として限定保存する。タイムアウト、レート制限、再試行可能性、取引所エラーコードを共通エラーへ写像する。全アダプターに同一の契約テストを適用する。

外貨FXと仮想通貨FXでは、注文単位、建玉方式、証拠金、取引時間、スワップまたは資金調達料が異なる。共通契約で差異を隠し切らず、`capabilities` と市場別拡張DTOで明示する。

初期アダプターの制約:

- OANDA: `USD_JPY`、MARKET、LIMIT。接続先が提供するstop-loss、take-profit、trailing-stop能力も検出する。
- Binance: `BTCJPY`、SPOT、MARKET、LIMIT。`exchangeInfo` のstatus、orderTypes、filters、permissionsを毎起動時と定期的に同期する。
- BinanceでMARGIN、LEVERAGED、perpetual/futures相当の能力が検出されても、初期版ではAPI層と実行層の両方で拒否する。
