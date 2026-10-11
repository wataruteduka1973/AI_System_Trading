# API リファレンス(自動生成)

> このファイルは `python scripts/generate_reference_docs.py` が、コードから生成します。直接編集しないでください。ルート・スキーマを変えたら再生成します(`tests/test_reference_docs.py` が、古くなると失敗します)。

ベースパス: `/api/v1`。対話式の仕様(OpenAPI)は、API サーバーの `/docs` でも見られます。

## 認証と権限

- ログインは OIDC(`/auth/login` → IdP → `/auth/callback`)。成功すると、`HttpOnly`・`SameSite=Strict` の `session` Cookie が付く。画面からの呼び出しは `credentials: include` で、この Cookie を送る。
- ワークスペースの資源は、メンバーの役割で制限する: **Viewer**(読み取り)< **Operator**(Bot の操作・補完・実行)< **Owner**(取引停止の解除など)。下の「権限」列は、必要な最低の役割。
- エラーは `{"detail": "…"}`。404 は、存在しない・見えない(他のワークスペースの)資源、409 は状態の衝突、422 は入力の誤り。
- 金額・数量は、精度を保つため、**文字列**の小数で返す(例: `"166667.000000000000000000"`)。時刻は ISO 8601。

## 認証・セッション

| メソッド | パス | 権限 | クエリ | 本文 | 応答 | 説明 |
|---|---|---|---|---|---|---|
| GET | `/api/v1/auth/login` | ログイン不要 | - | - | `200: any` | - |
| GET | `/api/v1/auth/callback` | ログイン不要 | `code`, `state` | - | `200: any` | - |
| POST | `/api/v1/auth/logout` | ログイン不要 | - | - | `200: any` | - |
| GET | `/api/v1/auth/me` | ログイン済み | - | - | `200: CurrentUserRead` | - |
| POST | `/api/v1/auth/sessions/revoke` | ログイン済み | - | - | `200: any` | Self-service "log out everywhere": the user who thinks their own session may have leaked does not need to wai… |
| POST | `/api/v1/auth/users/{user_id}/revoke-sessions` | Owner(いずれかのワークスペース) | - | - | `200: any` | Owner-initiated forced revocation of another user's sessions (e.g. |

## ワークスペース

| メソッド | パス | 権限 | クエリ | 本文 | 応答 | 説明 |
|---|---|---|---|---|---|---|
| GET | `/api/v1/workspaces` | ログイン済み | - | - | `200: WorkspaceRead[]` | Returns only workspaces `current_user` is a member of (Horizon5 Group A intentional behavior change, docs/pla… |
| POST | `/api/v1/workspaces` | ログイン済み | - | `WorkspaceCreate` | `201: WorkspaceRead` | - |
| GET | `/api/v1/workspaces/{workspace_id}` | Viewer 以上 | - | - | `200: WorkspaceRead` | - |

## 取引所の接続・口座

| メソッド | パス | 権限 | クエリ | 本文 | 応答 | 説明 |
|---|---|---|---|---|---|---|
| GET | `/api/v1/workspaces/{workspace_id}/connections` | Viewer 以上 | - | - | `200: ExchangeConnectionRead[]` | - |
| POST | `/api/v1/workspaces/{workspace_id}/connections` | Operator 以上 | - | `ExchangeConnectionCreate` | `201: ExchangeConnectionRead` | - |
| POST | `/api/v1/workspaces/{workspace_id}/connections/{connection_id}/disable` | Operator 以上 | - | - | `200: ExchangeConnectionRead` | - |
| DELETE | `/api/v1/workspaces/{workspace_id}/connections/{connection_id}` | Owner | - | - | `204` | - |
| PUT | `/api/v1/workspaces/{workspace_id}/connections/{connection_id}/credentials` | Operator 以上 | - | `ExchangeCredentialsUpdate` | `200: OandaVerificationRead | BinanceVerificationRead` | - |
| POST | `/api/v1/workspaces/{workspace_id}/connections/{connection_id}/verify` | Operator 以上 | - | - | `200: OandaVerificationRead | BinanceVerificationRead` | - |
| GET | `/api/v1/workspaces/{workspace_id}/accounts` | Viewer 以上 | - | - | `200: WorkspaceAccountRead[]` | - |
| PUT | `/api/v1/workspaces/{workspace_id}/account-selections/{exchange_code}` | Operator 以上 | - | `WorkspaceAccountSelectionUpdate` | `200: WorkspaceAccountSelectionRead` | - |
| GET | `/api/v1/exchanges` | ログイン済み | - | - | `200: ExchangeRead[]` | - |
| GET | `/api/v1/markets` | ログイン済み | - | - | `200: MarketRead[]` | - |

## 銘柄

| メソッド | パス | 権限 | クエリ | 本文 | 応答 | 説明 |
|---|---|---|---|---|---|---|
| GET | `/api/v1/workspaces/{workspace_id}/instruments` | Viewer 以上 | - | - | `200: WorkspaceInstrumentRead[]` | - |
| POST | `/api/v1/workspaces/{workspace_id}/instruments/sync` | Operator 以上 | - | - | `200: WorkspaceInstrumentSyncRead` | - |

## 市場データ(ローソク足・自動取得・過去取得)

| メソッド | パス | 権限 | クエリ | 本文 | 応答 | 説明 |
|---|---|---|---|---|---|---|
| POST | `/api/v1/workspaces/{workspace_id}/candle-backfills` | Operator 以上 | - | `CandleBackfillCreate` | `202: BackfillJobRead` | - |
| GET | `/api/v1/workspaces/{workspace_id}/candle-backfills` | Viewer 以上 | `instrument_id`, `timeframe`, `limit` | - | `200: BackfillJobRead[]` | - |
| GET | `/api/v1/workspaces/{workspace_id}/instruments/{instrument_id}/candles` | Viewer 以上 | `timeframe`, `limit`, `before` | - | `200: CandleRead[]` | - |
| GET | `/api/v1/workspaces/{workspace_id}/instruments/{instrument_id}/candle-coverage` | Viewer 以上 | `timeframe`, `requested_from`, `requested_to` | - | `200: CandleCoverageRead` | - |
| PUT | `/api/v1/workspaces/{workspace_id}/market-data-subscription` | Operator 以上 | - | `MarketDataSubscriptionUpdate` | `200: MarketDataSubscriptionRead` | - |
| PUT | `/api/v1/workspaces/{workspace_id}/market-data-subscriptions` | Operator 以上 | - | `MarketDataCollectionUpdate` | `200: MarketDataSubscriptionRead[]` | - |
| GET | `/api/v1/workspaces/{workspace_id}/market-data-subscriptions` | Viewer 以上 | - | - | `200: MarketDataSubscriptionRead[]` | - |
| POST | `/api/v1/workspaces/{workspace_id}/market-stream-tickets` | Viewer 以上 | - | `MarketStreamTicketCreate` | `201: MarketStreamTicketRead` | Issue a short-lived, one-time ticket authorizing a single WebSocket market-stream connection. |

## 欠損データ

| メソッド | パス | 権限 | クエリ | 本文 | 応答 | 説明 |
|---|---|---|---|---|---|---|
| GET | `/api/v1/workspaces/{workspace_id}/market-data-gaps` | Viewer 以上 | `status`, `instrument_id`, `timeframe`, `limit`, `offset` | - | `200: MarketDataGapPageRead` | - |
| GET | `/api/v1/workspaces/{workspace_id}/market-data-gaps/summary` | Viewer 以上 | - | - | `200: MarketDataGapSummaryRead[]` | - |
| POST | `/api/v1/workspaces/{workspace_id}/market-data-gaps/{gap_id}/backfill` | Operator 以上 | - | - | `202: BackfillJobRead` | - |

## 取引口座・Bot・緊急停止

| メソッド | パス | 権限 | クエリ | 本文 | 応答 | 説明 |
|---|---|---|---|---|---|---|
| POST | `/api/v1/workspaces/{workspace_id}/trading-accounts` | Operator 以上 | - | `TradingAccountCreate` | `201: TradingAccountRead` | - |
| GET | `/api/v1/workspaces/{workspace_id}/trading-accounts` | Viewer 以上 | - | - | `200: TradingAccountRead[]` | - |
| GET | `/api/v1/workspaces/{workspace_id}/trading-accounts/{account_id}` | Viewer 以上 | - | - | `200: TradingAccountRead` | - |
| POST | `/api/v1/workspaces/{workspace_id}/trading-accounts/{account_id}/deposits` | Operator 以上 | - | `TradingAccountDepositCreate` | `201: TradingAccountDepositRead` | - |
| POST | `/api/v1/workspaces/{workspace_id}/bots` | Operator 以上 | - | `TradingBotCreate` | `201: TradingBotRead` | - |
| GET | `/api/v1/workspaces/{workspace_id}/bots` | Viewer 以上 | - | - | `200: TradingBotRead[]` | - |
| GET | `/api/v1/workspaces/{workspace_id}/bot-overview` | Viewer 以上 | - | - | `200: BotOverviewResponse` | Each bot with its instrument, its account's balance and equity, the open position and what it has made so far… |
| GET | `/api/v1/workspaces/{workspace_id}/bots/{bot_id}` | Viewer 以上 | - | - | `200: TradingBotRead` | - |
| GET | `/api/v1/workspaces/{workspace_id}/bots/{bot_id}/latest-run` | Viewer 以上 | - | - | `200: BotRunSummaryRead` | - |
| POST | `/api/v1/workspaces/{workspace_id}/bots/{bot_id}/start` | Operator 以上 | - | - | `200: BotRunRead` | - |
| POST | `/api/v1/workspaces/{workspace_id}/bots/{bot_id}/pause` | Operator 以上 | - | - | `200: BotRunRead` | - |
| POST | `/api/v1/workspaces/{workspace_id}/bots/{bot_id}/resume` | Operator 以上 | - | - | `200: BotRunRead` | - |
| POST | `/api/v1/workspaces/{workspace_id}/bots/{bot_id}/stop` | Operator 以上 | - | - | `200: BotRunRead` | - |
| POST | `/api/v1/workspaces/{workspace_id}/bots/{bot_id}/emergency-stop` | Operator 以上 | - | `EmergencyStopRequest` | `200: EmergencyStopRead` | Stops this bot and blocks new orders until an Owner releases the halt (`/trading-halts/{id}/emergency-release… |
| POST | `/api/v1/workspaces/{workspace_id}/emergency-stop` | Operator 以上 | - | `EmergencyStopRequest` | `200: EmergencyStopRead` | The same for every bot of the workspace, with one workspace-wide halt that also keeps new bots from starting. |

## 取引履歴(約定・建玉)

| メソッド | パス | 権限 | クエリ | 本文 | 応答 | 説明 |
|---|---|---|---|---|---|---|
| GET | `/api/v1/workspaces/{workspace_id}/fills` | Viewer 以上 | `account_id`, `bot_id`, `instrument_id`, `side`, `from_time`, `to_time`, `limit`, `before`, `before_id` | - | `200: FillPageRead` | - |
| GET | `/api/v1/workspaces/{workspace_id}/position-history` | Viewer 以上 | `status`, `bot_id`, `instrument_id`, `limit`, `offset` | - | `200: PositionPageRead` | - |

## 判断の履歴

| メソッド | パス | 権限 | クエリ | 本文 | 応答 | 説明 |
|---|---|---|---|---|---|---|
| GET | `/api/v1/workspaces/{workspace_id}/bots/{bot_id}/decisions` | Viewer 以上 | `outcome`, `from_time`, `to_time`, `limit`, `before`, `before_id` | - | `200: DecisionHistoryRead` | - |

## 取引停止(halt)

| メソッド | パス | 権限 | クエリ | 本文 | 応答 | 説明 |
|---|---|---|---|---|---|---|
| GET | `/api/v1/workspaces/{workspace_id}/trading-halts` | Viewer 以上 | - | - | `200: TradingHaltRead[]` | - |
| POST | `/api/v1/workspaces/{workspace_id}/trading-halts/{halt_id}/release` | Owner | - | - | `200: TradingHaltRead` | - |
| POST | `/api/v1/workspaces/{workspace_id}/trading-halts/{halt_id}/emergency-release` | Owner | - | - | `200: TradingHaltRead` | - |

## バックテスト

| メソッド | パス | 権限 | クエリ | 本文 | 応答 | 説明 |
|---|---|---|---|---|---|---|
| GET | `/api/v1/workspaces/{workspace_id}/research-instruments` | Viewer 以上 | - | - | `200: WorkspaceInstrumentRead[]` | Research-only instruments (2026-09-26 -- the `binance_public` exchange and its `Instrument`s, see `app/market… |
| POST | `/api/v1/workspaces/{workspace_id}/backtests` | Operator 以上 | - | `BacktestCreate` | `201: BacktestCreateResponse` | - |
| POST | `/api/v1/workspaces/{workspace_id}/backtests/batch` | Operator 以上 | - | `BacktestBatchCreate` | `201: BacktestBatchResponse` | Runs the backtest for several instruments in one request, one after the other. |
| GET | `/api/v1/workspaces/{workspace_id}/backtests` | Viewer 以上 | - | - | `200: BacktestRunRead[]` | - |
| GET | `/api/v1/workspaces/{workspace_id}/backtests/{backtest_run_id}` | Viewer 以上 | - | - | `200: BacktestRunRead` | - |
| GET | `/api/v1/workspaces/{workspace_id}/backtests/{backtest_run_id}/equity-curve` | Viewer 以上 | - | - | `200: BacktestEquityCurveRead` | - |
| GET | `/api/v1/workspaces/{workspace_id}/backtests/{backtest_run_id}/trades` | Viewer 以上 | - | - | `200: BacktestTradeRead[]` | - |

## 通知

| メソッド | パス | 権限 | クエリ | 本文 | 応答 | 説明 |
|---|---|---|---|---|---|---|
| GET | `/api/v1/workspaces/{workspace_id}/notifications` | Viewer 以上 | `unacknowledged_only`, `limit` | - | `200: NotificationListRead` | - |
| POST | `/api/v1/workspaces/{workspace_id}/notifications/acknowledge-all` | Viewer 以上 | - | - | `200: AcknowledgeAllRead` | - |
| POST | `/api/v1/workspaces/{workspace_id}/notifications/{notification_id}/acknowledge` | Viewer 以上 | - | - | `200: NotificationRead` | Idempotent: acknowledging again changes nothing (the first time and person stay). |

## イベントログ

| メソッド | パス | 権限 | クエリ | 本文 | 応答 | 説明 |
|---|---|---|---|---|---|---|
| GET | `/api/v1/workspaces/{workspace_id}/events` | Viewer 以上 | `severity`, `category`, `event_type`, `reason_code`, `bot_id`, `correlation_id`, `from_time`, `to_time`, `q`, `limit`, `before`, `before_id` | - | `200: EventPageRead` | - |
| GET | `/api/v1/workspaces/{workspace_id}/events/facets` | Viewer 以上 | - | - | `200: EventFacetsRead` | - |

## システム状態

| メソッド | パス | 権限 | クエリ | 本文 | 応答 | 説明 |
|---|---|---|---|---|---|---|
| GET | `/api/v1/workspaces/{workspace_id}/system-status` | Viewer 以上 | - | - | `200: SystemStatusRead` | - |

## 画面のエラー報告

| メソッド | パス | 権限 | クエリ | 本文 | 応答 | 説明 |
|---|---|---|---|---|---|---|
| POST | `/api/v1/client-logs` | ログイン済み | - | `ClientLogEntry` | `202: object` | - |

## スキーマ

リクエストとレスポンスの形(フィールド名・型・必須)。

### AccountOverviewRead

| フィールド | 型 | 必須 | 説明 |
|---|---|---|---|
| `id` | string(uuid) | ○ | - |
| `base_currency` | string | ○ | - |
| `mode` | string | ○ | - |
| `status` | string | ○ | - |
| `bot_names` | string[] | ○ | - |
| `balances` | object | ○ | - |

### AcknowledgeAllRead

| フィールド | 型 | 必須 | 説明 |
|---|---|---|---|
| `acknowledged` | integer | ○ | - |

### AuthenticatedUserRead

| フィールド | 型 | 必須 | 説明 |
|---|---|---|---|
| `id` | string(uuid) | ○ | - |
| `email` | string | ○ | - |
| `display_name` | string | ○ | - |
| `status` | string | ○ | - |

### BackfillJobRead

| フィールド | 型 | 必須 | 説明 |
|---|---|---|---|
| `id` | string(uuid) | ○ | - |
| `workspace_id` | string(uuid) | ○ | - |
| `instrument_id` | string(uuid) | ○ | - |
| `timeframe` | string | ○ | - |
| `from_time` | string(date-time) | ○ | - |
| `to_time` | string(date-time) | ○ | - |
| `trigger_type` | string | ○ | - |
| `status` | string | ○ | - |
| `attempts` | integer | ○ | - |
| `rows_written` | integer | ○ | - |
| `validation_result` | object | ○ | - |
| `error_code` | string | null | ○ | - |
| `started_at` | string(date-time) | null | ○ | - |
| `finished_at` | string(date-time) | null | ○ | - |
| `created_at` | string(date-time) | ○ | - |
| `next_run_at` | string(date-time) | ○ | - |
| `consecutive_failures` | integer | ○ | - |

### BacktestBatchCreate

| フィールド | 型 | 必須 | 説明 |
|---|---|---|---|
| `instrument_ids` | string(uuid)[] | ○ | - |
| `timeframe` | string |  | - |
| `from_time` | string(date-time) | ○ | - |
| `to_time` | string(date-time) | ○ | - |
| `initial_equity` | number | string | ○ | - |
| `spread` | number | string |  | - |
| `mode` | `single` / `walk_forward` |  | - |
| `train_ratio` | number | string |  | - |

### BacktestBatchItemRead

| フィールド | 型 | 必須 | 説明 |
|---|---|---|---|
| `instrument_id` | string(uuid) | ○ | - |
| `symbol` | string | ○ | - |
| `bars` | integer | ○ | - |
| `runs` | BacktestRunRead[] | ○ | - |
| `error_code` | string | null | ○ | - |
| `error` | string | null | ○ | - |

### BacktestBatchResponse

| フィールド | 型 | 必須 | 説明 |
|---|---|---|---|
| `total_bars` | integer | ○ | - |
| `items` | BacktestBatchItemRead[] | ○ | - |

### BacktestCreate

| フィールド | 型 | 必須 | 説明 |
|---|---|---|---|
| `instrument_id` | string(uuid) | ○ | - |
| `timeframe` | string |  | - |
| `from_time` | string(date-time) | ○ | - |
| `to_time` | string(date-time) | ○ | - |
| `initial_equity` | number | string | ○ | - |
| `spread` | number | string |  | - |
| `mode` | `single` / `walk_forward` |  | - |
| `train_ratio` | number | string |  | - |

### BacktestCreateResponse

| フィールド | 型 | 必須 | 説明 |
|---|---|---|---|
| `runs` | BacktestRunRead[] | ○ | - |

### BacktestEquityCurveRead

| フィールド | 型 | 必須 | 説明 |
|---|---|---|---|
| `initial_equity` | string | null | ○ | - |
| `points` | EquityPoint[] | ○ | - |

### BacktestRunRead

| フィールド | 型 | 必須 | 説明 |
|---|---|---|---|
| `id` | string(uuid) | ○ | - |
| `workspace_id` | string(uuid) | ○ | - |
| `strategy_version_id` | string(uuid) | ○ | - |
| `risk_profile_version_id` | string(uuid) | ○ | - |
| `dataset_snapshot_id` | string(uuid) | ○ | - |
| `parameters` | object | ○ | - |
| `code_version` | string | ○ | - |
| `status` | string | ○ | - |
| `summary_metrics` | object | ○ | - |
| `started_at` | string(date-time) | null | ○ | - |
| `finished_at` | string(date-time) | null | ○ | - |
| `created_at` | string(date-time) | ○ | - |

### BacktestTradeRead

| フィールド | 型 | 必須 | 説明 |
|---|---|---|---|
| `id` | string(uuid) | ○ | - |
| `backtest_run_id` | string(uuid) | ○ | - |
| `sequence_no` | integer | ○ | - |
| `instrument_id` | string(uuid) | ○ | - |
| `side` | string | ○ | - |
| `entry_time` | string(date-time) | ○ | - |
| `exit_time` | string(date-time) | null | ○ | - |
| `entry_price` | string | ○ | - |
| `exit_price` | string | null | ○ | - |
| `quantity` | string | ○ | - |
| `fees` | string | ○ | - |
| `realized_pnl` | string | null | ○ | - |

### BinanceAccountRead

| フィールド | 型 | 必須 | 説明 |
|---|---|---|---|
| `account_ref_masked` | string | ○ | - |
| `account_type` | string | ○ | - |
| `permissions` | string[] | ○ | - |
| `can_trade` | boolean | ○ | - |
| `can_deposit` | boolean | ○ | - |
| `can_withdraw` | boolean | ○ | - |
| `nonzero_asset_count` | integer | ○ | - |
| `btc_jpy_tradeable` | boolean | ○ | - |

### BinanceVerificationRead

| フィールド | 型 | 必須 | 説明 |
|---|---|---|---|
| `connection_id` | string(uuid) | ○ | - |
| `status` | string | ○ | - |
| `accounts` | BinanceAccountRead[] | ○ | - |

### BotOverviewRead

| フィールド | 型 | 必須 | 説明 |
|---|---|---|---|
| `bot_id` | string(uuid) | ○ | - |
| `name` | string | ○ | - |
| `symbol` | string | ○ | - |
| `exchange_code` | string | ○ | - |
| `timeframe` | string | ○ | - |
| `desired_state` | string | ○ | - |
| `actual_state` | string | ○ | - |
| `account_id` | string(uuid) | ○ | - |
| `quote_asset` | string | ○ | - |
| `deposits` | string | ○ | - |
| `cash` | string | ○ | - |
| `equity` | string | ○ | - |
| `return_pct` | string | null | ○ | - |
| `realized_pnl` | string | ○ | - |
| `fees_paid` | string | ○ | - |
| `closed_trades` | integer | ○ | - |
| `position` | PositionOverviewRead | null | ○ | - |

### BotOverviewResponse

| フィールド | 型 | 必須 | 説明 |
|---|---|---|---|
| `bots` | BotOverviewRead[] | ○ | - |
| `accounts` | AccountOverviewRead[] | ○ | - |

### BotRunRead

| フィールド | 型 | 必須 | 説明 |
|---|---|---|---|
| `id` | string(uuid) | ○ | - |
| `bot_id` | string(uuid) | ○ | - |
| `status` | string | ○ | - |
| `code_version` | string | ○ | - |
| `started_at` | string(date-time) | ○ | - |
| `stopped_at` | string(date-time) | null | ○ | - |
| `stop_reason` | string | null | ○ | - |
| `heartbeat_at` | string(date-time) | null | ○ | - |

### BotRunSummaryRead

| フィールド | 型 | 必須 | 説明 |
|---|---|---|---|
| `id` | string(uuid) | ○ | - |
| `bot_id` | string(uuid) | ○ | - |
| `status` | string | ○ | - |
| `code_version` | string | ○ | - |
| `started_at` | string(date-time) | ○ | - |
| `stopped_at` | string(date-time) | null | ○ | - |
| `stop_reason` | string | null | ○ | - |
| `heartbeat_at` | string(date-time) | null | ○ | - |
| `latest_signal` | LatestSignalRead | null | ○ | - |

### CandleBackfillCreate

| フィールド | 型 | 必須 | 説明 |
|---|---|---|---|
| `instrument_id` | string(uuid) | ○ | - |
| `timeframe` | `1m` / `5m` / `15m` / `30m` / `1h` / `4h` / `1d` |  | - |
| `days` | integer |  | - |

### CandleCoverageRead

| フィールド | 型 | 必須 | 説明 |
|---|---|---|---|
| `timeframe` | `1m` / `5m` / `15m` / `30m` / `1h` / `4h` / `1d` | ○ | - |
| `requested_from` | string(date-time) | null | ○ | - |
| `requested_to` | string(date-time) | null | ○ | - |
| `actual_from` | string(date-time) | null | ○ | - |
| `actual_to` | string(date-time) | null | ○ | - |
| `stored_count` | integer | ○ | - |
| `expected_count` | integer | null | ○ | - |
| `missing_count` | integer | null | ○ | - |
| `coverage_status` | `complete` / `partial_source_limit` / `partial_gaps` / `empty` / `checking` | ○ | - |
| `source_limitation` | string | null | ○ | - |

### CandleRead

| フィールド | 型 | 必須 | 説明 |
|---|---|---|---|
| `open_time` | string(date-time) | ○ | - |
| `close_time` | string(date-time) | ○ | - |
| `open` | string | ○ | - |
| `high` | string | ○ | - |
| `low` | string | ○ | - |
| `close` | string | ○ | - |
| `volume` | string | null | ○ | - |
| `trade_count` | integer | null | ○ | - |
| `source` | string | ○ | - |
| `quality_status` | string | ○ | - |
| `is_final` | boolean | ○ | - |

### ClientLogEntry

| フィールド | 型 | 必須 | 説明 |
|---|---|---|---|
| `message` | string | ○ | - |
| `stack` | string | null |  | - |
| `source` | string | ○ | e.g. |
| `path` | string | ○ | Page path where the error occurred |
| `user_agent` | string | null |  | - |

### ConnectionStatusRead

| フィールド | 型 | 必須 | 説明 |
|---|---|---|---|
| `id` | string(uuid) | ○ | - |
| `label` | string | ○ | - |
| `environment` | string | ○ | - |
| `status` | string | ○ | - |
| `verification_outcome` | string | ○ | - |
| `last_verified_at` | string(date-time) | null | ○ | - |

### CurrentUserRead

| フィールド | 型 | 必須 | 説明 |
|---|---|---|---|
| `user` | AuthenticatedUserRead | ○ | - |
| `memberships` | WorkspaceMembershipRead[] | ○ | - |

### DecisionHaltRead

| フィールド | 型 | 必須 | 説明 |
|---|---|---|---|
| `id` | string(uuid) | ○ | - |
| `scope_type` | string | ○ | - |
| `level` | string | ○ | - |
| `reason_code` | string | ○ | - |
| `status` | string | ○ | - |
| `halted_at` | string(date-time) | ○ | - |
| `released_at` | string(date-time) | null | ○ | - |

### DecisionHistoryRead

| フィールド | 型 | 必須 | 説明 |
|---|---|---|---|
| `items` | DecisionItemRead[] | ○ | - |
| `summary` | DecisionSummaryRead | ○ | - |
| `halts` | DecisionHaltRead[] | ○ | - |
| `next_before` | string(date-time) | null | ○ | - |
| `next_before_id` | string(uuid) | null | ○ | - |

### DecisionItemRead

| フィールド | 型 | 必須 | 説明 |
|---|---|---|---|
| `signal_id` | string(uuid) | ○ | - |
| `evaluated_at` | string(date-time) | ○ | - |
| `bar_open_time` | string(date-time) | null | ○ | - |
| `bar_close_time` | string(date-time) | null | ○ | - |
| `action` | string | ○ | - |
| `outcome` | string | ○ | - |
| `rationale` | object | ○ | - |
| `risk_outcome` | string | null | ○ | - |
| `risk_reason_code` | string | null | ○ | - |
| `failed_checks` | FailedCheckRead[] | ○ | - |
| `approved_quantity` | string | null | ○ | - |
| `order_id` | string(uuid) | null | ○ | - |
| `order_side` | string | null | ○ | - |
| `fill_price` | string | null | ○ | - |
| `fill_quantity` | string | null | ○ | - |

### DecisionSummaryRead

| フィールド | 型 | 必須 | 説明 |
|---|---|---|---|
| `evaluations` | integer | ○ | - |
| `counts` | object | ○ | - |

### EmergencyStopRead

| フィールド | 型 | 必須 | 説明 |
|---|---|---|---|
| `halt_id` | string(uuid) | ○ | - |
| `scope_type` | string | ○ | - |
| `scope_id` | string(uuid) | null | ○ | - |
| `level` | string | ○ | - |
| `already_active` | boolean | ○ | - |
| `stopped_bot_ids` | string(uuid)[] | ○ | - |
| `bot_stop_failures` | object[] | ○ | - |
| `closing_order_ids` | string(uuid)[] | ○ | - |
| `close_failures` | object[] | ○ | - |

### EmergencyStopRequest

| フィールド | 型 | 必須 | 説明 |
|---|---|---|---|
| `close_positions` | boolean |  | - |
| `reason` | string | null |  | - |

### EquityPoint

| フィールド | 型 | 必須 | 説明 |
|---|---|---|---|
| `time` | string(date-time) | ○ | - |
| `equity` | string | ○ | - |

### EventFacetsRead

| フィールド | 型 | 必須 | 説明 |
|---|---|---|---|
| `severities` | string[] | ○ | - |
| `categories` | string[] | ○ | - |
| `event_types` | string[] | ○ | - |
| `reason_codes` | string[] | ○ | - |

### EventPageRead

| フィールド | 型 | 必須 | 説明 |
|---|---|---|---|
| `items` | SystemEventRead[] | ○ | - |
| `next_before` | string(date-time) | null | ○ | - |
| `next_before_id` | string(uuid) | null | ○ | - |

### ExchangeConnectionCreate

| フィールド | 型 | 必須 | 説明 |
|---|---|---|---|
| `exchange_code` | string | ○ | - |
| `label` | string | ○ | - |
| `environment` | `practice` / `testnet` / `paper_data` | ○ | - |
| `api_base_url` | string(uri) | ○ | - |
| `credentials` | object | ○ | - |

### ExchangeConnectionRead

| フィールド | 型 | 必須 | 説明 |
|---|---|---|---|
| `id` | string(uuid) | ○ | - |
| `workspace_id` | string(uuid) | ○ | - |
| `exchange_id` | string(uuid) | ○ | - |
| `label` | string | ○ | - |
| `environment` | string | ○ | - |
| `api_base_url` | string | ○ | - |
| `status` | string | ○ | - |
| `capabilities` | object | ○ | - |
| `last_verified_at` | string(date-time) | null | ○ | - |
| `credentials_status` | `saved` / `missing` | ○ | - |
| `credentials_updated_at` | string(date-time) | null | ○ | - |
| `verification_outcome` | `not_verified` / `success` / `authentication_failed` / `communication_failed` | ○ | - |

### ExchangeCredentialsUpdate

| フィールド | 型 | 必須 | 説明 |
|---|---|---|---|
| `credentials` | object | ○ | - |

### ExchangeRead

| フィールド | 型 | 必須 | 説明 |
|---|---|---|---|
| `id` | string(uuid) | ○ | - |
| `code` | string | ○ | - |
| `name` | string | ○ | - |
| `status` | string | ○ | - |

### FailedCheckRead

| フィールド | 型 | 必須 | 説明 |
|---|---|---|---|
| `name` | string | ○ | - |
| `detail` | object | ○ | - |

### FillPageRead

| フィールド | 型 | 必須 | 説明 |
|---|---|---|---|
| `items` | FillRowRead[] | ○ | - |
| `totals` | FillTotalsRead[] | ○ | - |
| `next_before` | string(date-time) | null | ○ | - |
| `next_before_id` | string(uuid) | null | ○ | - |

### FillRowRead

| フィールド | 型 | 必須 | 説明 |
|---|---|---|---|
| `fill_id` | string(uuid) | ○ | - |
| `order_id` | string(uuid) | ○ | - |
| `executed_at` | string(date-time) | ○ | - |
| `symbol` | string | ○ | - |
| `quote_asset` | string | ○ | - |
| `side` | string | ○ | - |
| `order_type` | string | ○ | - |
| `order_status` | string | ○ | - |
| `client_order_id` | string | ○ | - |
| `quantity` | string | ○ | - |
| `price` | string | ○ | - |
| `notional` | string | ○ | - |
| `fee_amount` | string | ○ | - |
| `realized_pnl` | string | ○ | - |
| `reason` | string | ○ | - |
| `bot_id` | string(uuid) | null | ○ | - |
| `bot_name` | string | null | ○ | - |

### FillTotalsRead

| フィールド | 型 | 必須 | 説明 |
|---|---|---|---|
| `quote_asset` | string | ○ | - |
| `fills` | integer | ○ | - |
| `fees` | string | ○ | - |
| `realized_pnl` | string | ○ | - |

### HTTPValidationError

| フィールド | 型 | 必須 | 説明 |
|---|---|---|---|
| `detail` | ValidationError[] |  | - |

### LatestSignalRead

| フィールド | 型 | 必須 | 説明 |
|---|---|---|---|
| `id` | string(uuid) | ○ | - |
| `action` | string | ○ | - |
| `created_at` | string(date-time) | ○ | - |

### MarketDataCollectionUpdate

| フィールド | 型 | 必須 | 説明 |
|---|---|---|---|
| `instrument_id` | string(uuid) | ○ | - |
| `enabled` | boolean | ○ | - |

### MarketDataGapPageRead

| フィールド | 型 | 必須 | 説明 |
|---|---|---|---|
| `total` | integer | ○ | - |
| `items` | MarketDataGapRead[] | ○ | - |

### MarketDataGapRead

| フィールド | 型 | 必須 | 説明 |
|---|---|---|---|
| `id` | string(uuid) | ○ | - |
| `instrument_id` | string(uuid) | ○ | - |
| `symbol` | string | ○ | - |
| `exchange_code` | string | ○ | - |
| `timeframe` | string | ○ | - |
| `from_time` | string(date-time) | ○ | - |
| `to_time` | string(date-time) | ○ | - |
| `expected_count` | integer | null | ○ | - |
| `missing_count` | integer | null | ○ | - |
| `reason_code` | string | ○ | - |
| `status` | string | ○ | - |
| `detected_at` | string(date-time) | ○ | - |
| `resolved_at` | string(date-time) | null | ○ | - |
| `can_backfill` | boolean | ○ | - |
| `latest_job` | BackfillJobRead | null | ○ | - |
| `affected_bots` | string[] | ○ | - |

### MarketDataGapSummaryRead

| フィールド | 型 | 必須 | 説明 |
|---|---|---|---|
| `instrument_id` | string(uuid) | ○ | - |
| `symbol` | string | ○ | - |
| `exchange_code` | string | ○ | - |
| `timeframe` | string | ○ | - |
| `open_count` | integer | ○ | - |
| `open_missing` | integer | ○ | - |
| `ignored_count` | integer | ○ | - |
| `resolved_count` | integer | ○ | - |

### MarketDataSubscriptionRead

| フィールド | 型 | 必須 | 説明 |
|---|---|---|---|
| `id` | string(uuid) | ○ | - |
| `workspace_id` | string(uuid) | ○ | - |
| `instrument_id` | string(uuid) | ○ | - |
| `timeframe` | string | ○ | - |
| `enabled` | boolean | ○ | - |
| `poll_interval_seconds` | integer | ○ | - |
| `last_polled_at` | string(date-time) | null | ○ | - |
| `last_success_at` | string(date-time) | null | ○ | - |
| `last_error_code` | string | null | ○ | - |
| `next_run_at` | string(date-time) | ○ | - |
| `consecutive_failures` | integer | ○ | - |
| `blocked_reason` | string | null | ○ | - |

### MarketDataSubscriptionUpdate

| フィールド | 型 | 必須 | 説明 |
|---|---|---|---|
| `instrument_id` | string(uuid) | ○ | - |
| `timeframe` | `1m` / `5m` / `15m` / `30m` / `1h` / `4h` / `1d` |  | - |
| `enabled` | boolean | ○ | - |

### MarketDataWorkerStatusRead

| フィールド | 型 | 必須 | 説明 |
|---|---|---|---|
| `status` | string | ○ | - |
| `enabled_subscriptions` | integer | ○ | - |
| `blocked_subscriptions` | integer | ○ | - |
| `overdue_subscriptions` | integer | ○ | - |
| `most_overdue_seconds` | integer | null | ○ | - |
| `overdue_after_seconds` | integer | ○ | - |

### MarketRead

| フィールド | 型 | 必須 | 説明 |
|---|---|---|---|
| `id` | string(uuid) | ○ | - |
| `code` | string | ○ | - |
| `asset_class` | string | ○ | - |
| `product_type` | string | ○ | - |
| `settlement_type` | string | ○ | - |

### MarketStreamTicketCreate

| フィールド | 型 | 必須 | 説明 |
|---|---|---|---|
| `instrument_id` | string(uuid) | ○ | - |
| `timeframe` | `1m` / `5m` / `15m` / `30m` / `1h` / `4h` / `1d` |  | - |

### MarketStreamTicketRead

| フィールド | 型 | 必須 | 説明 |
|---|---|---|---|
| `ticket` | string | ○ | - |
| `exchange` | string | ○ | - |
| `symbol` | string | ○ | - |
| `timeframe` | string | ○ | - |
| `expires_at` | string(date-time) | ○ | - |

### NotificationListRead

| フィールド | 型 | 必須 | 説明 |
|---|---|---|---|
| `unacknowledged_count` | integer | ○ | - |
| `items` | NotificationRead[] | ○ | - |

### NotificationRead

| フィールド | 型 | 必須 | 説明 |
|---|---|---|---|
| `id` | string(uuid) | ○ | - |
| `event_id` | string(uuid) | ○ | - |
| `severity` | string | ○ | - |
| `category` | string | ○ | - |
| `event_type` | string | ○ | - |
| `message` | string | ○ | - |
| `payload` | object | ○ | - |
| `occurred_at` | string(date-time) | ○ | - |
| `status` | string | ○ | - |
| `acknowledged_at` | string(date-time) | null | ○ | - |

### NotificationStatusRead

| フィールド | 型 | 必須 | 説明 |
|---|---|---|---|
| `pending_events` | integer | ○ | - |
| `oldest_pending_age_seconds` | integer | null | ○ | - |
| `failed_events_last_7_days` | integer | ○ | - |

### OandaAccountRead

| フィールド | 型 | 必須 | 説明 |
|---|---|---|---|
| `account_ref_masked` | string | ○ | - |
| `alias` | string | null | ○ | - |
| `currency` | string | ○ | - |
| `hedging_enabled` | boolean | null | ○ | - |
| `margin_rate` | string | null | ○ | - |
| `gslo_mode` | string | null | ○ | - |
| `usd_jpy_tradeable` | boolean | ○ | - |

### OandaVerificationRead

| フィールド | 型 | 必須 | 説明 |
|---|---|---|---|
| `connection_id` | string(uuid) | ○ | - |
| `status` | string | ○ | - |
| `accounts` | OandaAccountRead[] | ○ | - |

### PositionHistoryRead

| フィールド | 型 | 必須 | 説明 |
|---|---|---|---|
| `id` | string(uuid) | ○ | - |
| `symbol` | string | ○ | - |
| `quote_asset` | string | ○ | - |
| `bot_id` | string(uuid) | null | ○ | - |
| `bot_name` | string | null | ○ | - |
| `side` | string | ○ | - |
| `status` | string | ○ | - |
| `quantity` | string | ○ | - |
| `average_entry_price` | string | null | ○ | - |
| `realized_pnl` | string | ○ | - |
| `stop_price` | string | null | ○ | - |
| `opened_at` | string(date-time) | null | ○ | - |
| `closed_at` | string(date-time) | null | ○ | - |

### PositionOverviewRead

| フィールド | 型 | 必須 | 説明 |
|---|---|---|---|
| `side` | string | ○ | - |
| `quantity` | string | ○ | - |
| `average_entry_price` | string | null | ○ | - |
| `mark_price` | string | null | ○ | - |
| `unrealized_pnl` | string | ○ | - |
| `stop_price` | string | null | ○ | - |
| `opened_at` | string(date-time) | null | ○ | - |

### PositionPageRead

| フィールド | 型 | 必須 | 説明 |
|---|---|---|---|
| `total` | integer | ○ | - |
| `items` | PositionHistoryRead[] | ○ | - |

### SystemEventRead

| フィールド | 型 | 必須 | 説明 |
|---|---|---|---|
| `id` | string(uuid) | ○ | - |
| `occurred_at` | string(date-time) | ○ | - |
| `severity` | string | ○ | - |
| `category` | string | ○ | - |
| `event_type` | string | ○ | - |
| `reason_code` | string | null | ○ | - |
| `source_type` | string | ○ | - |
| `source_id` | string(uuid) | null | ○ | - |
| `target_type` | string | null | ○ | - |
| `target_id` | string(uuid) | null | ○ | - |
| `correlation_id` | string(uuid) | ○ | - |
| `message` | string | ○ | - |
| `payload` | object | ○ | - |

### SystemStatusRead

| フィールド | 型 | 必須 | 説明 |
|---|---|---|---|
| `checked_at` | string(date-time) | ○ | - |
| `overall` | string | ○ | - |
| `problems` | string[] | ○ | - |
| `trading_worker` | TradingWorkerStatusRead | ○ | - |
| `market_data_worker` | MarketDataWorkerStatusRead | ○ | - |
| `notifications` | NotificationStatusRead | ○ | - |
| `halts` | object | ○ | - |
| `bots` | object | ○ | - |
| `connections` | ConnectionStatusRead[] | ○ | - |

### TradingAccountCreate

| フィールド | 型 | 必須 | 説明 |
|---|---|---|---|
| `connection_id` | string(uuid) | ○ | - |
| `base_currency` | string | ○ | - |

### TradingAccountDepositCreate

| フィールド | 型 | 必須 | 説明 |
|---|---|---|---|
| `amount` | number | string | ○ | - |
| `asset` | string | ○ | - |
| `note` | string | null |  | - |

### TradingAccountDepositRead

| フィールド | 型 | 必須 | 説明 |
|---|---|---|---|
| `id` | string(uuid) | ○ | - |
| `account_id` | string(uuid) | ○ | - |
| `reference_type` | string | ○ | - |
| `description` | string | ○ | - |
| `occurred_at` | string(date-time) | ○ | - |

### TradingAccountRead

| フィールド | 型 | 必須 | 説明 |
|---|---|---|---|
| `id` | string(uuid) | ○ | - |
| `workspace_id` | string(uuid) | ○ | - |
| `connection_id` | string(uuid) | null | ○ | - |
| `mode` | string | ○ | - |
| `base_currency` | string | ○ | - |
| `status` | string | ○ | - |
| `created_at` | string(date-time) | ○ | - |

### TradingBotCreate

| フィールド | 型 | 必須 | 説明 |
|---|---|---|---|
| `name` | string | ○ | - |
| `account_id` | string(uuid) | ○ | - |
| `instrument_id` | string(uuid) | ○ | - |
| `timeframe` | string |  | - |

### TradingBotRead

| フィールド | 型 | 必須 | 説明 |
|---|---|---|---|
| `id` | string(uuid) | ○ | - |
| `workspace_id` | string(uuid) | ○ | - |
| `name` | string | ○ | - |
| `execution_mode` | string | ○ | - |
| `strategy_mode` | string | ○ | - |
| `account_id` | string(uuid) | ○ | - |
| `instrument_id` | string(uuid) | ○ | - |
| `timeframe` | string | ○ | - |
| `desired_state` | string | ○ | - |
| `actual_state` | string | ○ | - |
| `live_trading_enabled` | boolean | ○ | - |
| `version` | integer | ○ | - |
| `created_at` | string(date-time) | ○ | - |

### TradingHaltRead

| フィールド | 型 | 必須 | 説明 |
|---|---|---|---|
| `id` | string(uuid) | ○ | - |
| `scope_type` | string | ○ | - |
| `scope_id` | string(uuid) | null | ○ | - |
| `level` | string | ○ | - |
| `reason_code` | string | ○ | - |
| `status` | string | ○ | - |
| `halted_at` | string(date-time) | ○ | - |
| `released_at` | string(date-time) | null | ○ | - |

### TradingWorkerStatusRead

| フィールド | 型 | 必須 | 説明 |
|---|---|---|---|
| `status` | string | ○ | - |
| `active_bots` | integer | ○ | - |
| `last_heartbeat_age_seconds` | integer | null | ○ | - |
| `stalled_bots` | string[] | ○ | - |
| `stale_after_seconds` | integer | ○ | - |

### ValidationError

| フィールド | 型 | 必須 | 説明 |
|---|---|---|---|
| `loc` | string | integer[] | ○ | - |
| `msg` | string | ○ | - |
| `type` | string | ○ | - |
| `input` | any |  | - |
| `ctx` | object |  | - |

### WorkspaceAccountRead

| フィールド | 型 | 必須 | 説明 |
|---|---|---|---|
| `id` | string(uuid) | ○ | - |
| `connection_id` | string(uuid) | ○ | - |
| `exchange_id` | string(uuid) | ○ | - |
| `exchange_code` | string | ○ | - |
| `connection_label` | string | ○ | - |
| `connection_status` | string | ○ | - |
| `account_ref_masked` | string | ○ | - |
| `alias` | string | null | ○ | - |
| `environment` | string | ○ | - |
| `currency` | string | ○ | - |
| `status` | string | ○ | - |
| `selected` | boolean | ○ | - |

### WorkspaceAccountSelectionRead

| フィールド | 型 | 必須 | 説明 |
|---|---|---|---|
| `workspace_id` | string(uuid) | ○ | - |
| `exchange_id` | string(uuid) | ○ | - |
| `external_account_id` | string(uuid) | ○ | - |
| `selected_at` | string(date-time) | ○ | - |

### WorkspaceAccountSelectionUpdate

| フィールド | 型 | 必須 | 説明 |
|---|---|---|---|
| `external_account_id` | string(uuid) | ○ | - |

### WorkspaceCreate

| フィールド | 型 | 必須 | 説明 |
|---|---|---|---|
| `name` | string | ○ | - |

### WorkspaceInstrumentRead

| フィールド | 型 | 必須 | 説明 |
|---|---|---|---|
| `id` | string(uuid) | ○ | - |
| `exchange_code` | string | ○ | - |
| `market_code` | string | ○ | - |
| `symbol` | string | ○ | - |
| `base_asset` | string | ○ | - |
| `quote_asset` | string | ○ | - |
| `price_scale` | integer | ○ | - |
| `quantity_scale` | integer | ○ | - |
| `tick_size` | string | ○ | - |
| `step_size` | string | ○ | - |
| `min_quantity` | string | null | ○ | - |
| `max_quantity` | string | null | ○ | - |
| `min_notional` | string | null | ○ | - |
| `allowed_order_types` | string[] | ○ | - |
| `capabilities` | object | ○ | - |
| `status` | string | ○ | - |
| `rules_synced_at` | string(date-time) | null | ○ | - |

### WorkspaceInstrumentSyncRead

| フィールド | 型 | 必須 | 説明 |
|---|---|---|---|
| `instruments` | WorkspaceInstrumentRead[] | ○ | - |

### WorkspaceMembershipRead

| フィールド | 型 | 必須 | 説明 |
|---|---|---|---|
| `workspace_id` | string(uuid) | ○ | - |
| `workspace_name` | string | ○ | - |
| `role` | string | ○ | - |

### WorkspaceRead

| フィールド | 型 | 必須 | 説明 |
|---|---|---|---|
| `id` | string(uuid) | ○ | - |
| `name` | string | ○ | - |
| `status` | string | ○ | - |
| `created_at` | string(date-time) | ○ | - |
| `updated_at` | string(date-time) | ○ | - |
