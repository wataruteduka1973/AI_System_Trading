# データベース スキーマ リファレンス(自動生成)

> このファイルは `python scripts/generate_reference_docs.py` が、ORM のモデルから生成します。直接編集しないでください。モデルを変えたら再生成します(`tests/test_reference_docs.py` が、古くなると失敗します)。

- PostgreSQL。スキーマは `fx`(設定 `DATABASE_SCHEMA`)。変更は Alembic(`alembic/versions/`)で管理し、**CHECK 制約や一部の索引は、マイグレーション側にだけある**(この文書はモデルから読めるものだけ)。
- 主キーは UUID(`gen_random_uuid()`)。金額・数量は `NUMERIC(38,18)`。時刻は `timestamptz`(**UTC ではなく、サーバーのローカルタイムで返ることがある**。`docs/knowledge/database-times-are-not-utc.md`)。

## ワークスペース・ユーザー

### `app_user`

| 列 | 型 | NULL | 既定値 | 参照・備考 |
|---|---|---|---|---|
| `id` | UUID | 必須 | `gen_random_uuid()` | 主キー |
| `email` | TEXT | 必須 |  |  |
| `display_name` | TEXT | 必須 |  |  |
| `status` | TEXT | 必須 | `active` |  |
| `oidc_subject` | TEXT |  |  |  |
| `created_at` | TIMESTAMP WITH TIME ZONE | 必須 | `now()` |  |
| `updated_at` | TIMESTAMP WITH TIME ZONE | 必須 | `now()` |  |

- 一意: `email`
- 一意: `oidc_subject`

### `workspace`

| 列 | 型 | NULL | 既定値 | 参照・備考 |
|---|---|---|---|---|
| `id` | UUID | 必須 | `gen_random_uuid()` | 主キー |
| `name` | TEXT | 必須 |  |  |
| `status` | TEXT | 必須 | `active` |  |
| `created_at` | TIMESTAMP WITH TIME ZONE | 必須 | `now()` |  |
| `updated_at` | TIMESTAMP WITH TIME ZONE | 必須 | `now()` |  |

### `session_revocation`

Horizon5 Group A / Unit 3.9 (docs/plans/horizon5-implementation-plan.md, added in response to a critical review of the plan: without this, a suspected leaked session has no faster remedy than waiting…

| 列 | 型 | NULL | 既定値 | 参照・備考 |
|---|---|---|---|---|
| `user_id` | UUID | 必須 |  | 主キー; → `app_user.id` (ON DELETE CASCADE) |
| `revoked_sessions_before` | TIMESTAMP WITH TIME ZONE | 必須 |  |  |
| `revoked_by` | UUID |  |  | → `app_user.id` (ON DELETE SET NULL) |
| `revoked_at` | TIMESTAMP WITH TIME ZONE | 必須 | `now()` |  |

### `user_membership`

Horizon5 Group A / Unit 1 (docs/plans/horizon5-implementation-plan.md).

| 列 | 型 | NULL | 既定値 | 参照・備考 |
|---|---|---|---|---|
| `workspace_id` | UUID | 必須 |  | 主キー; → `workspace.id` (ON DELETE CASCADE) |
| `user_id` | UUID | 必須 |  | 主キー; → `app_user.id` (ON DELETE CASCADE) |
| `role` | TEXT | 必須 |  |  |
| `created_at` | TIMESTAMP WITH TIME ZONE | 必須 | `now()` |  |

## 取引所の接続・口座

### `exchange`

| 列 | 型 | NULL | 既定値 | 参照・備考 |
|---|---|---|---|---|
| `id` | UUID | 必須 | `gen_random_uuid()` | 主キー |
| `code` | TEXT | 必須 |  |  |
| `name` | TEXT | 必須 |  |  |
| `status` | TEXT | 必須 | `active` |  |
| `created_at` | TIMESTAMP WITH TIME ZONE | 必須 | `now()` |  |

- 一意: `code`

### `market`

| 列 | 型 | NULL | 既定値 | 参照・備考 |
|---|---|---|---|---|
| `id` | UUID | 必須 | `gen_random_uuid()` | 主キー |
| `code` | TEXT | 必須 |  |  |
| `asset_class` | TEXT | 必須 |  |  |
| `product_type` | TEXT | 必須 |  |  |
| `settlement_type` | TEXT | 必須 |  |  |
| `created_at` | TIMESTAMP WITH TIME ZONE | 必須 | `now()` |  |

- 一意: `code`

### `account_selection_policy`

Added for Paper Trading's `TradingAccount.selection_policy_id` foreign key to resolve (see `app/models/strategy.py` module docstring for why this is needed at all).

| 列 | 型 | NULL | 既定値 | 参照・備考 |
|---|---|---|---|---|
| `id` | UUID | 必須 | `gen_random_uuid()` | 主キー |
| `workspace_id` | UUID | 必須 |  | → `workspace.id` (ON DELETE CASCADE) |
| `name` | TEXT | 必須 |  |  |
| `version` | INTEGER | 必須 |  |  |
| `criteria` | JSONB | 必須 |  |  |
| `checksum` | VARCHAR(64) | 必須 |  |  |
| `status` | TEXT | 必須 | `draft` |  |
| `created_by` | UUID |  |  | → `app_user.id` (ON DELETE SET NULL) |
| `created_at` | TIMESTAMP WITH TIME ZONE | 必須 | `now()` |  |

- 一意: `workspace_id`, `checksum`(`uq_account_selection_policy_checksum`)
- 一意: `workspace_id`, `name`, `version`(`uq_account_selection_policy_version`)

### `exchange_connection`

| 列 | 型 | NULL | 既定値 | 参照・備考 |
|---|---|---|---|---|
| `id` | UUID | 必須 | `gen_random_uuid()` | 主キー |
| `workspace_id` | UUID | 必須 |  | → `workspace.id` (ON DELETE CASCADE) |
| `exchange_id` | UUID | 必須 |  | → `exchange.id` (ON DELETE RESTRICT) |
| `label` | TEXT | 必須 |  |  |
| `environment` | TEXT | 必須 |  |  |
| `api_base_url` | TEXT | 必須 |  |  |
| `secret_ref` | TEXT |  |  |  |
| `status` | TEXT | 必須 | `pending_credentials` |  |
| `capabilities` | JSONB | 必須 | `{}` |  |
| `last_verified_at` | TIMESTAMP WITH TIME ZONE |  |  |  |
| `credentials_updated_at` | TIMESTAMP WITH TIME ZONE |  |  |  |
| `verification_outcome` | TEXT | 必須 | `not_verified` |  |
| `created_at` | TIMESTAMP WITH TIME ZONE | 必須 | `now()` |  |
| `updated_at` | TIMESTAMP WITH TIME ZONE | 必須 | `now()` |  |

### `external_account`

| 列 | 型 | NULL | 既定値 | 参照・備考 |
|---|---|---|---|---|
| `id` | UUID | 必須 | `gen_random_uuid()` | 主キー |
| `connection_id` | UUID | 必須 |  | → `exchange_connection.id` (ON DELETE CASCADE) |
| `external_account_ref_encrypted` | TEXT | 必須 |  |  |
| `external_account_ref_hash` | TEXT | 必須 |  |  |
| `external_account_ref_masked` | TEXT | 必須 |  |  |
| `alias` | TEXT |  |  |  |
| `environment` | TEXT | 必須 |  |  |
| `currency` | TEXT | 必須 |  |  |
| `hedging_enabled` | BOOLEAN |  |  |  |
| `margin_rate` | NUMERIC(20, 10) |  |  |  |
| `mt4_account_ref_masked` | TEXT |  |  |  |
| `gslo_mode` | TEXT |  |  |  |
| `capabilities` | JSONB | 必須 | `{}` |  |
| `status` | TEXT | 必須 | `active` |  |
| `synced_at` | TIMESTAMP WITH TIME ZONE | 必須 | `now()` |  |
| `created_at` | TIMESTAMP WITH TIME ZONE | 必須 | `now()` |  |
| `updated_at` | TIMESTAMP WITH TIME ZONE | 必須 | `now()` |  |

### `workspace_account_selection`

| 列 | 型 | NULL | 既定値 | 参照・備考 |
|---|---|---|---|---|
| `id` | UUID | 必須 | `gen_random_uuid()` | 主キー |
| `workspace_id` | UUID | 必須 |  | → `workspace.id` (ON DELETE CASCADE) |
| `exchange_id` | UUID | 必須 |  | → `exchange.id` (ON DELETE RESTRICT) |
| `external_account_id` | UUID | 必須 |  | → `external_account.id` (ON DELETE CASCADE) |
| `selected_at` | TIMESTAMP WITH TIME ZONE | 必須 | `now()` |  |
| `updated_at` | TIMESTAMP WITH TIME ZONE | 必須 | `now()` |  |

- 一意: `workspace_id`, `exchange_id`(`uq_workspace_exchange_selection`)

## 銘柄

### `instrument`

| 列 | 型 | NULL | 既定値 | 参照・備考 |
|---|---|---|---|---|
| `id` | UUID | 必須 | `gen_random_uuid()` | 主キー |
| `exchange_id` | UUID | 必須 |  | → `exchange.id` (ON DELETE RESTRICT) |
| `market_id` | UUID | 必須 |  | → `market.id` (ON DELETE RESTRICT) |
| `symbol` | TEXT | 必須 |  |  |
| `base_asset` | TEXT | 必須 |  |  |
| `quote_asset` | TEXT | 必須 |  |  |
| `contract_size` | NUMERIC(38, 18) |  |  |  |
| `price_scale` | SMALLINT | 必須 |  |  |
| `quantity_scale` | SMALLINT | 必須 |  |  |
| `tick_size` | NUMERIC(38, 18) | 必須 |  |  |
| `step_size` | NUMERIC(38, 18) | 必須 |  |  |
| `min_quantity` | NUMERIC(38, 18) |  |  |  |
| `max_quantity` | NUMERIC(38, 18) |  |  |  |
| `min_notional` | NUMERIC(38, 18) |  |  |  |
| `margin_asset` | TEXT |  |  |  |
| `allowed_order_types` | TEXT[] | 必須 | `{}` |  |
| `capabilities` | JSONB | 必須 | `{}` |  |
| `status` | TEXT | 必須 | `active` |  |
| `rules_synced_at` | TIMESTAMP WITH TIME ZONE |  |  |  |
| `created_at` | TIMESTAMP WITH TIME ZONE | 必須 | `now()` |  |
| `updated_at` | TIMESTAMP WITH TIME ZONE | 必須 | `now()` |  |

- 一意: `exchange_id`, `market_id`, `symbol`(`uq_instrument_symbol`)

## 市場データ(ローソク足・自動取得・欠損・spread)

### `candle`

| 列 | 型 | NULL | 既定値 | 参照・備考 |
|---|---|---|---|---|
| `id` | UUID | 必須 | `gen_random_uuid()` | 主キー |
| `instrument_id` | UUID | 必須 |  | → `instrument.id` (ON DELETE RESTRICT) |
| `timeframe` | TEXT | 必須 |  |  |
| `open_time` | TIMESTAMP WITH TIME ZONE | 必須 |  |  |
| `close_time` | TIMESTAMP WITH TIME ZONE | 必須 |  |  |
| `open` | NUMERIC(38, 18) | 必須 |  |  |
| `high` | NUMERIC(38, 18) | 必須 |  |  |
| `low` | NUMERIC(38, 18) | 必須 |  |  |
| `close` | NUMERIC(38, 18) | 必須 |  |  |
| `volume` | NUMERIC(38, 18) |  |  |  |
| `trade_count` | BIGINT |  |  |  |
| `source` | TEXT | 必須 |  |  |
| `quality_status` | TEXT | 必須 | `complete` |  |
| `is_final` | BOOLEAN | 必須 | `true` |  |
| `received_at` | TIMESTAMP WITH TIME ZONE | 必須 | `now()` |  |
| `corrected_at` | TIMESTAMP WITH TIME ZONE |  |  |  |

- 一意: `instrument_id`, `timeframe`, `open_time`(`uq_candle_business_key`)

### `instrument_spread`

Latest observed bid/ask per instrument -- a continuously UPSERTed "current state" row (see alembic/versions/20260920_0007_instrument_spread.py), not an append-only tick history.

| 列 | 型 | NULL | 既定値 | 参照・備考 |
|---|---|---|---|---|
| `instrument_id` | UUID | 必須 |  | 主キー; → `instrument.id` (ON DELETE CASCADE) |
| `bid` | NUMERIC(38, 18) | 必須 |  |  |
| `ask` | NUMERIC(38, 18) | 必須 |  |  |
| `observed_at` | TIMESTAMP WITH TIME ZONE | 必須 |  |  |
| `source` | TEXT | 必須 |  |  |
| `updated_at` | TIMESTAMP WITH TIME ZONE | 必須 | `now()` |  |

### `instrument_spread_history`

Sampled history of `InstrumentSpread`'s observations (alembic 20261007_0011): the only way to know a past spread, since no public source serves past quotes.

| 列 | 型 | NULL | 既定値 | 参照・備考 |
|---|---|---|---|---|
| `instrument_id` | UUID | 必須 |  | 主キー; → `instrument.id` (ON DELETE CASCADE) |
| `observed_at` | TIMESTAMP WITH TIME ZONE | 必須 |  | 主キー |
| `bid` | NUMERIC(38, 18) | 必須 |  |  |
| `ask` | NUMERIC(38, 18) | 必須 |  |  |
| `source` | TEXT | 必須 |  |  |

### `market_data_gap`

| 列 | 型 | NULL | 既定値 | 参照・備考 |
|---|---|---|---|---|
| `id` | UUID | 必須 | `gen_random_uuid()` | 主キー |
| `instrument_id` | UUID | 必須 |  | → `instrument.id` (ON DELETE RESTRICT) |
| `timeframe` | TEXT | 必須 |  |  |
| `from_time` | TIMESTAMP WITH TIME ZONE | 必須 |  |  |
| `to_time` | TIMESTAMP WITH TIME ZONE | 必須 |  |  |
| `expected_count` | INTEGER |  |  |  |
| `missing_count` | INTEGER |  |  |  |
| `reason_code` | TEXT | 必須 |  |  |
| `status` | TEXT | 必須 | `open` |  |
| `detected_at` | TIMESTAMP WITH TIME ZONE | 必須 | `now()` |  |
| `resolved_at` | TIMESTAMP WITH TIME ZONE |  |  |  |

### `market_data_subscription`

| 列 | 型 | NULL | 既定値 | 参照・備考 |
|---|---|---|---|---|
| `id` | UUID | 必須 | `gen_random_uuid()` | 主キー |
| `workspace_id` | UUID | 必須 |  | → `workspace.id` (ON DELETE CASCADE) |
| `instrument_id` | UUID | 必須 |  | → `instrument.id` (ON DELETE CASCADE) |
| `timeframe` | TEXT | 必須 |  |  |
| `enabled` | BOOLEAN | 必須 | `false` |  |
| `poll_interval_seconds` | INTEGER | 必須 | `60` |  |
| `last_polled_at` | TIMESTAMP WITH TIME ZONE |  |  |  |
| `last_success_at` | TIMESTAMP WITH TIME ZONE |  |  |  |
| `last_error_code` | TEXT |  |  |  |
| `created_at` | TIMESTAMP WITH TIME ZONE | 必須 | `now()` |  |
| `updated_at` | TIMESTAMP WITH TIME ZONE | 必須 | `now()` |  |
| `next_run_at` | TIMESTAMP WITH TIME ZONE | 必須 | `now()` |  |
| `consecutive_failures` | INTEGER | 必須 | `0` |  |
| `blocked_reason` | TEXT |  |  |  |

- 一意: `workspace_id`, `instrument_id`, `timeframe`(`uq_market_data_subscription`)

### `backfill_job`

| 列 | 型 | NULL | 既定値 | 参照・備考 |
|---|---|---|---|---|
| `id` | UUID | 必須 | `gen_random_uuid()` | 主キー |
| `gap_id` | UUID |  |  | → `market_data_gap.id` (ON DELETE SET NULL) |
| `workspace_id` | UUID | 必須 |  | → `workspace.id` (ON DELETE CASCADE) |
| `instrument_id` | UUID | 必須 |  | → `instrument.id` (ON DELETE RESTRICT) |
| `timeframe` | TEXT | 必須 |  |  |
| `from_time` | TIMESTAMP WITH TIME ZONE | 必須 |  |  |
| `to_time` | TIMESTAMP WITH TIME ZONE | 必須 |  |  |
| `requested_by` | UUID |  |  | → `app_user.id` (ON DELETE SET NULL) |
| `trigger_type` | TEXT | 必須 |  |  |
| `status` | TEXT | 必須 | `queued` |  |
| `attempts` | INTEGER | 必須 | `0` |  |
| `rows_written` | INTEGER | 必須 | `0` |  |
| `validation_result` | JSONB | 必須 | `{}` |  |
| `error_code` | TEXT |  |  |  |
| `started_at` | TIMESTAMP WITH TIME ZONE |  |  |  |
| `finished_at` | TIMESTAMP WITH TIME ZONE |  |  |  |
| `created_at` | TIMESTAMP WITH TIME ZONE | 必須 | `now()` |  |
| `next_run_at` | TIMESTAMP WITH TIME ZONE | 必須 | `now()` |  |
| `consecutive_failures` | INTEGER | 必須 | `0` |  |

## 戦略・Bot・リスク判定・取引停止

### `risk_profile`

| 列 | 型 | NULL | 既定値 | 参照・備考 |
|---|---|---|---|---|
| `id` | UUID | 必須 | `gen_random_uuid()` | 主キー |
| `workspace_id` | UUID | 必須 |  | → `workspace.id` (ON DELETE CASCADE) |
| `name` | TEXT | 必須 |  |  |
| `status` | TEXT | 必須 | `active` |  |
| `created_at` | TIMESTAMP WITH TIME ZONE | 必須 | `now()` |  |
| `updated_at` | TIMESTAMP WITH TIME ZONE | 必須 | `now()` |  |

- 一意: `workspace_id`, `name`(`uq_risk_profile_name`)

### `strategy`

| 列 | 型 | NULL | 既定値 | 参照・備考 |
|---|---|---|---|---|
| `id` | UUID | 必須 | `gen_random_uuid()` | 主キー |
| `workspace_id` | UUID | 必須 |  | → `workspace.id` (ON DELETE CASCADE) |
| `name` | TEXT | 必須 |  |  |
| `mode` | TEXT | 必須 |  |  |
| `status` | TEXT | 必須 | `active` |  |
| `created_at` | TIMESTAMP WITH TIME ZONE | 必須 | `now()` |  |
| `updated_at` | TIMESTAMP WITH TIME ZONE | 必須 | `now()` |  |

- 一意: `workspace_id`, `name`(`uq_strategy_name`)

### `risk_profile_version`

| 列 | 型 | NULL | 既定値 | 参照・備考 |
|---|---|---|---|---|
| `id` | UUID | 必須 | `gen_random_uuid()` | 主キー |
| `risk_profile_id` | UUID | 必須 |  | → `risk_profile.id` (ON DELETE CASCADE) |
| `version` | INTEGER | 必須 |  |  |
| `rules` | JSONB | 必須 |  |  |
| `checksum` | VARCHAR(64) | 必須 |  |  |
| `template_code` | TEXT |  |  |  |
| `status` | TEXT | 必須 | `draft` |  |
| `created_by` | UUID |  |  | → `app_user.id` (ON DELETE SET NULL) |
| `created_at` | TIMESTAMP WITH TIME ZONE | 必須 | `now()` |  |

- 一意: `risk_profile_id`, `version`(`uq_risk_profile_version`)
- 一意: `risk_profile_id`, `checksum`(`uq_risk_profile_version_checksum`)

### `strategy_version`

| 列 | 型 | NULL | 既定値 | 参照・備考 |
|---|---|---|---|---|
| `id` | UUID | 必須 | `gen_random_uuid()` | 主キー |
| `strategy_id` | UUID | 必須 |  | → `strategy.id` (ON DELETE CASCADE) |
| `version` | INTEGER | 必須 |  |  |
| `supported_market_types` | TEXT[] | 必須 |  |  |
| `definition` | JSONB | 必須 |  |  |
| `feature_schema` | JSONB | 必須 | `{}` |  |
| `checksum` | VARCHAR(64) | 必須 |  |  |
| `lifecycle_status` | TEXT | 必須 | `draft` |  |
| `created_by` | UUID |  |  | → `app_user.id` (ON DELETE SET NULL) |
| `created_at` | TIMESTAMP WITH TIME ZONE | 必須 | `now()` |  |

- 一意: `strategy_id`, `version`(`uq_strategy_version`)
- 一意: `strategy_id`, `checksum`(`uq_strategy_version_checksum`)

### `trading_halt`

`scope_id` is polymorphic (its meaning depends on `scope_type`: NULL for `system`/`workspace`, otherwise a connection/account/bot/instrument id) and therefore intentionally has no foreign key, matchi…

| 列 | 型 | NULL | 既定値 | 参照・備考 |
|---|---|---|---|---|
| `id` | UUID | 必須 | `gen_random_uuid()` | 主キー |
| `workspace_id` | UUID | 必須 |  | → `workspace.id` (ON DELETE CASCADE) |
| `scope_type` | TEXT | 必須 |  |  |
| `scope_id` | UUID |  |  |  |
| `level` | TEXT | 必須 |  |  |
| `reason_code` | TEXT | 必須 |  |  |
| `trigger_event_id` | UUID |  |  | → `system_event.id` (ON DELETE SET NULL) |
| `auto_releasable` | BOOLEAN | 必須 | `false` |  |
| `release_condition` | JSONB | 必須 | `{}` |  |
| `status` | TEXT | 必須 | `active` |  |
| `halted_at` | TIMESTAMP WITH TIME ZONE | 必須 | `now()` |  |
| `released_at` | TIMESTAMP WITH TIME ZONE |  |  |  |
| `released_by` | UUID |  |  | → `app_user.id` (ON DELETE SET NULL) |

### `trading_bot`

| 列 | 型 | NULL | 既定値 | 参照・備考 |
|---|---|---|---|---|
| `id` | UUID | 必須 | `gen_random_uuid()` | 主キー |
| `workspace_id` | UUID | 必須 |  | → `workspace.id` (ON DELETE CASCADE) |
| `name` | TEXT | 必須 |  |  |
| `execution_mode` | TEXT | 必須 |  |  |
| `strategy_mode` | TEXT | 必須 |  |  |
| `connection_id` | UUID | 必須 |  | → `exchange_connection.id` (ON DELETE RESTRICT) |
| `account_id` | UUID | 必須 |  | → `trading_account.id` (ON DELETE RESTRICT) |
| `instrument_id` | UUID | 必須 |  | → `instrument.id` (ON DELETE RESTRICT) |
| `timeframe` | TEXT | 必須 |  |  |
| `strategy_version_id` | UUID | 必須 |  | → `strategy_version.id` (ON DELETE RESTRICT) |
| `risk_profile_version_id` | UUID | 必須 |  | → `risk_profile_version.id` (ON DELETE RESTRICT) |
| `desired_state` | TEXT | 必須 | `stopped` |  |
| `actual_state` | TEXT | 必須 | `stopped` |  |
| `live_trading_enabled` | BOOLEAN | 必須 | `false` |  |
| `version` | BIGINT | 必須 | `1` |  |
| `created_at` | TIMESTAMP WITH TIME ZONE | 必須 | `now()` |  |
| `updated_at` | TIMESTAMP WITH TIME ZONE | 必須 | `now()` |  |

- 一意: `workspace_id`, `name`(`uq_trading_bot_name`)

### `bot_run`

| 列 | 型 | NULL | 既定値 | 参照・備考 |
|---|---|---|---|---|
| `id` | UUID | 必須 | `gen_random_uuid()` | 主キー |
| `bot_id` | UUID | 必須 |  | → `trading_bot.id` (ON DELETE CASCADE) |
| `status` | TEXT | 必須 | `starting` |  |
| `code_version` | TEXT | 必須 |  |  |
| `started_at` | TIMESTAMP WITH TIME ZONE | 必須 | `now()` |  |
| `stopped_at` | TIMESTAMP WITH TIME ZONE |  |  |  |
| `stop_reason` | TEXT |  |  |  |
| `heartbeat_at` | TIMESTAMP WITH TIME ZONE |  |  |  |

### `signal`

| 列 | 型 | NULL | 既定値 | 参照・備考 |
|---|---|---|---|---|
| `id` | UUID | 必須 | `gen_random_uuid()` | 主キー |
| `workspace_id` | UUID | 必須 |  | → `workspace.id` (ON DELETE CASCADE) |
| `bot_run_id` | UUID | 必須 |  | → `bot_run.id` (ON DELETE CASCADE) |
| `candle_id` | UUID | 必須 |  | → `candle.id` (ON DELETE RESTRICT) |
| `strategy_version_id` | UUID | 必須 |  | → `strategy_version.id` (ON DELETE RESTRICT) |
| `model_artifact_id` | UUID |  |  |  |
| `action` | TEXT | 必須 |  |  |
| `score` | NUMERIC(20, 10) |  |  |  |
| `rationale` | JSONB | 必須 |  |  |
| `input_checksum` | VARCHAR(64) | 必須 |  |  |
| `correlation_id` | UUID | 必須 | `gen_random_uuid()` |  |
| `created_at` | TIMESTAMP WITH TIME ZONE | 必須 | `now()` |  |

- 一意: `bot_run_id`, `candle_id`, `strategy_version_id`, `input_checksum`(`uq_signal_idempotency`)

### `risk_decision`

| 列 | 型 | NULL | 既定値 | 参照・備考 |
|---|---|---|---|---|
| `id` | UUID | 必須 | `gen_random_uuid()` | 主キー |
| `signal_id` | UUID | 必須 |  | → `signal.id` (ON DELETE CASCADE) |
| `risk_profile_version_id` | UUID | 必須 |  | → `risk_profile_version.id` (ON DELETE RESTRICT) |
| `outcome` | TEXT | 必須 |  |  |
| `rule_results` | JSONB | 必須 |  |  |
| `adjusted_quantity` | NUMERIC(38, 18) |  |  |  |
| `reason_code` | TEXT |  |  |  |
| `decided_at` | TIMESTAMP WITH TIME ZONE | 必須 | `now()` |  |

- 一意: `signal_id`, `risk_profile_version_id`(`uq_risk_decision_signal`)

## 取引(注文・約定・台帳・建玉)

### `trading_account`

| 列 | 型 | NULL | 既定値 | 参照・備考 |
|---|---|---|---|---|
| `id` | UUID | 必須 | `gen_random_uuid()` | 主キー |
| `workspace_id` | UUID | 必須 |  | → `workspace.id` (ON DELETE CASCADE) |
| `connection_id` | UUID |  |  | → `exchange_connection.id` (ON DELETE RESTRICT) |
| `external_account_id` | UUID |  |  | → `external_account.id` (ON DELETE RESTRICT) |
| `mode` | TEXT | 必須 |  |  |
| `base_currency` | VARCHAR(16) | 必須 |  |  |
| `selection_mode` | TEXT | 必須 | `manual` |  |
| `selection_policy_id` | UUID |  |  | → `account_selection_policy.id` (ON DELETE RESTRICT) |
| `status` | TEXT | 必須 | `active` |  |
| `created_at` | TIMESTAMP WITH TIME ZONE | 必須 | `now()` |  |
| `updated_at` | TIMESTAMP WITH TIME ZONE | 必須 | `now()` |  |

### `account_snapshot`

Point-in-time equity/margin observation (2026-08-16 initial migration; this ORM mapping added 2026-09-20 while implementing the Risk Gate, which needs a time series of equity to check daily/weekly lo…

| 列 | 型 | NULL | 既定値 | 参照・備考 |
|---|---|---|---|---|
| `id` | UUID | 必須 | `gen_random_uuid()` | 主キー |
| `account_id` | UUID | 必須 |  | → `trading_account.id` (ON DELETE CASCADE) |
| `captured_at` | TIMESTAMP WITH TIME ZONE | 必須 |  |  |
| `balances` | JSONB | 必須 |  |  |
| `equity` | NUMERIC(38, 18) | 必須 |  |  |
| `unrealized_pnl` | NUMERIC(38, 18) | 必須 | `0` |  |
| `margin_used` | NUMERIC(38, 18) |  |  |  |
| `margin_available` | NUMERIC(38, 18) |  |  |  |
| `margin_call_percent` | NUMERIC(20, 10) |  |  |  |
| `margin_closeout_percent` | NUMERIC(20, 10) |  |  |  |
| `source` | TEXT | 必須 |  |  |

- 一意: `account_id`, `captured_at`(`uq_account_snapshot_time`)

### `ledger_transaction`

Not named in `03_ER図とデータ定義.md`'s table list (which only describes `ledger_entry`'s own `transaction_id` column) but already exists as its own table in the live DB; included so `LedgerEntry`'s require…

| 列 | 型 | NULL | 既定値 | 参照・備考 |
|---|---|---|---|---|
| `id` | UUID | 必須 | `gen_random_uuid()` | 主キー |
| `account_id` | UUID | 必須 |  | → `trading_account.id` (ON DELETE RESTRICT) |
| `reference_type` | TEXT | 必須 |  |  |
| `reference_id` | UUID |  |  |  |
| `description` | TEXT | 必須 |  |  |
| `occurred_at` | TIMESTAMP WITH TIME ZONE | 必須 |  |  |
| `created_at` | TIMESTAMP WITH TIME ZONE | 必須 | `now()` |  |

### `trading_position`

Maps the `trading_position` table (named to avoid the `position` reserved word; `03_ER図とデータ定義.md` calls the concept `position`).

| 列 | 型 | NULL | 既定値 | 参照・備考 |
|---|---|---|---|---|
| `id` | UUID | 必須 | `gen_random_uuid()` | 主キー |
| `account_id` | UUID | 必須 |  | → `trading_account.id` (ON DELETE RESTRICT) |
| `instrument_id` | UUID | 必須 |  | → `instrument.id` (ON DELETE RESTRICT) |
| `side` | TEXT | 必須 |  |  |
| `quantity` | NUMERIC(38, 18) | 必須 |  |  |
| `average_entry_price` | NUMERIC(38, 18) |  |  |  |
| `realized_pnl` | NUMERIC(38, 18) | 必須 | `0` |  |
| `unrealized_pnl` | NUMERIC(38, 18) | 必須 | `0` |  |
| `stop_price` | NUMERIC(38, 18) |  |  |  |
| `status` | TEXT | 必須 |  |  |
| `version` | BIGINT | 必須 | `1` |  |
| `opened_at` | TIMESTAMP WITH TIME ZONE |  |  |  |
| `closed_at` | TIMESTAMP WITH TIME ZONE |  |  |  |
| `created_at` | TIMESTAMP WITH TIME ZONE | 必須 | `now()` |  |
| `updated_at` | TIMESTAMP WITH TIME ZONE | 必須 | `now()` |  |

### `order_intent`

| 列 | 型 | NULL | 既定値 | 参照・備考 |
|---|---|---|---|---|
| `id` | UUID | 必須 | `gen_random_uuid()` | 主キー |
| `signal_id` | UUID | 必須 |  | → `signal.id` (ON DELETE RESTRICT) |
| `risk_decision_id` | UUID | 必須 |  | → `risk_decision.id` (ON DELETE RESTRICT) |
| `side` | TEXT | 必須 |  |  |
| `order_type` | TEXT | 必須 |  |  |
| `requested_quantity` | NUMERIC(38, 18) | 必須 |  |  |
| `limit_price` | NUMERIC(38, 18) |  |  |  |
| `stop_price` | NUMERIC(38, 18) |  |  |  |
| `take_profit_price` | NUMERIC(38, 18) |  |  |  |
| `max_slippage_bps` | INTEGER |  |  |  |
| `expires_at` | TIMESTAMP WITH TIME ZONE |  |  |  |
| `created_at` | TIMESTAMP WITH TIME ZONE | 必須 | `now()` |  |

- 一意: `risk_decision_id`(`uq_order_intent_risk`)
- 一意: `signal_id`(`uq_order_intent_signal`)

### `trade_order`

Maps the `trade_order` table (named to avoid the `order` reserved word; `03_ER図とデータ定義.md` calls the concept `order`).

| 列 | 型 | NULL | 既定値 | 参照・備考 |
|---|---|---|---|---|
| `id` | UUID | 必須 | `gen_random_uuid()` | 主キー |
| `workspace_id` | UUID | 必須 |  | → `workspace.id` (ON DELETE CASCADE) |
| `account_id` | UUID | 必須 |  | → `trading_account.id` (ON DELETE RESTRICT) |
| `order_intent_id` | UUID |  |  | → `order_intent.id` (ON DELETE RESTRICT) |
| `client_order_id` | TEXT | 必須 |  |  |
| `external_order_id` | TEXT |  |  |  |
| `instrument_id` | UUID | 必須 |  | → `instrument.id` (ON DELETE RESTRICT) |
| `side` | TEXT | 必須 |  |  |
| `order_type` | TEXT | 必須 |  |  |
| `time_in_force` | TEXT | 必須 |  |  |
| `quantity` | NUMERIC(38, 18) | 必須 |  |  |
| `filled_quantity` | NUMERIC(38, 18) | 必須 | `0` |  |
| `limit_price` | NUMERIC(38, 18) |  |  |  |
| `stop_price` | NUMERIC(38, 18) |  |  |  |
| `status` | TEXT | 必須 | `pending` |  |
| `submitted_at` | TIMESTAMP WITH TIME ZONE |  |  |  |
| `created_at` | TIMESTAMP WITH TIME ZONE | 必須 | `now()` |  |
| `updated_at` | TIMESTAMP WITH TIME ZONE | 必須 | `now()` |  |

- 一意: `account_id`, `client_order_id`(`uq_trade_order_client_id`)
- 一意: `account_id`, `external_order_id`(`uq_trade_order_external_id`)

### `fill`

| 列 | 型 | NULL | 既定値 | 参照・備考 |
|---|---|---|---|---|
| `id` | UUID | 必須 | `gen_random_uuid()` | 主キー |
| `order_id` | UUID | 必須 |  | → `trade_order.id` (ON DELETE RESTRICT) |
| `external_fill_id` | TEXT |  |  |  |
| `price` | NUMERIC(38, 18) | 必須 |  |  |
| `quantity` | NUMERIC(38, 18) | 必須 |  |  |
| `fee_amount` | NUMERIC(38, 18) | 必須 | `0` |  |
| `fee_asset` | VARCHAR(32) |  |  |  |
| `liquidity_role` | TEXT |  |  |  |
| `executed_at` | TIMESTAMP WITH TIME ZONE | 必須 |  |  |
| `recorded_at` | TIMESTAMP WITH TIME ZONE | 必須 | `now()` |  |

- 一意: `order_id`, `external_fill_id`(`uq_fill_external_id`)

### `order_status_history`

One row per `TradeOrder.status` transition (`from_status` is NULL for the first), written by `order_flow.py` next to the status change itself.

| 列 | 型 | NULL | 既定値 | 参照・備考 |
|---|---|---|---|---|
| `id` | UUID | 必須 | `gen_random_uuid()` | 主キー |
| `order_id` | UUID | 必須 |  | → `trade_order.id` (ON DELETE CASCADE) |
| `from_status` | TEXT |  |  |  |
| `to_status` | TEXT | 必須 |  |  |
| `reason_code` | TEXT |  |  |  |
| `raw_ref` | TEXT |  |  |  |
| `occurred_at` | TIMESTAMP WITH TIME ZONE | 必須 | `now()` |  |

### `ledger_entry`

Append-only; see module docstring.

| 列 | 型 | NULL | 既定値 | 参照・備考 |
|---|---|---|---|---|
| `id` | UUID | 必須 | `gen_random_uuid()` | 主キー |
| `transaction_id` | UUID | 必須 |  | → `ledger_transaction.id` (ON DELETE RESTRICT) |
| `account_id` | UUID | 必須 |  | → `trading_account.id` (ON DELETE RESTRICT) |
| `fill_id` | UUID |  |  | → `fill.id` (ON DELETE RESTRICT) |
| `asset` | VARCHAR(32) | 必須 |  |  |
| `amount` | NUMERIC(38, 18) | 必須 |  |  |
| `entry_type` | TEXT | 必須 |  |  |
| `occurred_at` | TIMESTAMP WITH TIME ZONE | 必須 |  |  |

## バックテスト

### `dataset_snapshot`

| 列 | 型 | NULL | 既定値 | 参照・備考 |
|---|---|---|---|---|
| `id` | UUID | 必須 | `gen_random_uuid()` | 主キー |
| `workspace_id` | UUID | 必須 |  | → `workspace.id` (ON DELETE CASCADE) |
| `instruments` | JSONB | 必須 |  |  |
| `intervals` | JSONB | 必須 |  |  |
| `from_time` | TIMESTAMP WITH TIME ZONE | 必須 |  |  |
| `to_time` | TIMESTAMP WITH TIME ZONE | 必須 |  |  |
| `feature_schema` | JSONB | 必須 |  |  |
| `row_count` | BIGINT | 必須 |  |  |
| `quality_report` | JSONB | 必須 |  |  |
| `checksum` | VARCHAR(64) | 必須 |  |  |
| `storage_uri` | TEXT | 必須 |  |  |
| `created_at` | TIMESTAMP WITH TIME ZONE | 必須 | `now()` |  |

- 一意: `workspace_id`, `checksum`(`uq_dataset_snapshot_checksum`)

### `backtest_run`

| 列 | 型 | NULL | 既定値 | 参照・備考 |
|---|---|---|---|---|
| `id` | UUID | 必須 | `gen_random_uuid()` | 主キー |
| `workspace_id` | UUID | 必須 |  | → `workspace.id` (ON DELETE CASCADE) |
| `strategy_version_id` | UUID | 必須 |  | → `strategy_version.id` (ON DELETE RESTRICT) |
| `risk_profile_version_id` | UUID | 必須 |  | → `risk_profile_version.id` (ON DELETE RESTRICT) |
| `dataset_snapshot_id` | UUID | 必須 |  | → `dataset_snapshot.id` (ON DELETE RESTRICT) |
| `parameters` | JSONB | 必須 |  |  |
| `code_version` | TEXT | 必須 |  |  |
| `status` | TEXT | 必須 | `queued` |  |
| `summary_metrics` | JSONB | 必須 | `{}` |  |
| `started_at` | TIMESTAMP WITH TIME ZONE |  |  |  |
| `finished_at` | TIMESTAMP WITH TIME ZONE |  |  |  |
| `created_at` | TIMESTAMP WITH TIME ZONE | 必須 | `now()` |  |

### `backtest_trade`

| 列 | 型 | NULL | 既定値 | 参照・備考 |
|---|---|---|---|---|
| `id` | UUID | 必須 | `gen_random_uuid()` | 主キー |
| `backtest_run_id` | UUID | 必須 |  | → `backtest_run.id` (ON DELETE CASCADE) |
| `sequence_no` | INTEGER | 必須 |  |  |
| `instrument_id` | UUID | 必須 |  | → `instrument.id` (ON DELETE RESTRICT) |
| `side` | TEXT | 必須 |  |  |
| `entry_time` | TIMESTAMP WITH TIME ZONE | 必須 |  |  |
| `exit_time` | TIMESTAMP WITH TIME ZONE |  |  |  |
| `entry_price` | NUMERIC(38, 18) | 必須 |  |  |
| `exit_price` | NUMERIC(38, 18) |  |  |  |
| `quantity` | NUMERIC(38, 18) | 必須 |  |  |
| `fees` | NUMERIC(38, 18) | 必須 | `0` |  |
| `realized_pnl` | NUMERIC(38, 18) |  |  |  |
| `metadata` | JSONB | 必須 | `{}` |  |

- 一意: `backtest_run_id`, `sequence_no`(`uq_backtest_trade_sequence`)

## 監査・イベント・通知の送信待ち(Outbox)

### `outbox_event`

Horizon5 Group D / Unit 8 (docs/plans/horizon5-implementation-plan.md).

| 列 | 型 | NULL | 既定値 | 参照・備考 |
|---|---|---|---|---|
| `id` | UUID | 必須 | `gen_random_uuid()` | 主キー |
| `aggregate_type` | TEXT | 必須 |  |  |
| `aggregate_id` | UUID | 必須 |  |  |
| `event_type` | TEXT | 必須 |  |  |
| `payload` | JSONB | 必須 |  |  |
| `correlation_id` | UUID | 必須 |  |  |
| `status` | TEXT | 必須 | `pending` |  |
| `attempts` | INTEGER | 必須 | `0` |  |
| `available_at` | TIMESTAMP WITH TIME ZONE | 必須 | `now()` |  |
| `published_at` | TIMESTAMP WITH TIME ZONE |  |  |  |
| `created_at` | TIMESTAMP WITH TIME ZONE | 必須 | `now()` |  |

### `audit_log`

| 列 | 型 | NULL | 既定値 | 参照・備考 |
|---|---|---|---|---|
| `id` | UUID | 必須 | `gen_random_uuid()` | 主キー |
| `workspace_id` | UUID | 必須 |  | → `workspace.id` (ON DELETE RESTRICT) |
| `actor_id` | UUID |  |  | → `app_user.id` |
| `action` | TEXT | 必須 |  |  |
| `resource_type` | TEXT | 必須 |  |  |
| `resource_id` | UUID |  |  |  |
| `before_data` | JSONB |  |  |  |
| `after_data` | JSONB |  |  |  |
| `correlation_id` | UUID | 必須 |  |  |
| `ip_address` | INET |  |  |  |
| `user_agent` | TEXT |  |  |  |
| `occurred_at` | TIMESTAMP WITH TIME ZONE | 必須 | `now()` |  |

### `system_event`

Distinct from `AuditLog`: audit records prove *who changed what*; this records *what happened in the system* (03_ER図とデータ定義.md).

| 列 | 型 | NULL | 既定値 | 参照・備考 |
|---|---|---|---|---|
| `id` | UUID | 必須 | `gen_random_uuid()` | 主キー |
| `workspace_id` | UUID | 必須 |  | → `workspace.id` (ON DELETE CASCADE) |
| `occurred_at` | TIMESTAMP WITH TIME ZONE | 必須 | `now()` |  |
| `severity` | TEXT | 必須 |  |  |
| `category` | TEXT | 必須 |  |  |
| `event_type` | TEXT | 必須 |  |  |
| `reason_code` | TEXT |  |  |  |
| `source_type` | TEXT | 必須 |  |  |
| `source_id` | UUID |  |  |  |
| `target_type` | TEXT |  |  |  |
| `target_id` | UUID |  |  |  |
| `correlation_id` | UUID | 必須 |  |  |
| `message` | TEXT | 必須 |  |  |
| `payload` | JSONB | 必須 | `{}` |  |
| `contains_sensitive_data` | BOOLEAN | 必須 | `false` |  |

## 通知

### `notification`

Horizon5 Group D / Unit 8 (docs/plans/horizon5-implementation-plan.md).

| 列 | 型 | NULL | 既定値 | 参照・備考 |
|---|---|---|---|---|
| `id` | UUID | 必須 | `gen_random_uuid()` | 主キー |
| `workspace_id` | UUID | 必須 |  | → `workspace.id` (ON DELETE CASCADE) |
| `event_id` | UUID | 必須 |  | → `system_event.id` (ON DELETE CASCADE) |
| `channel` | TEXT | 必須 |  |  |
| `status` | TEXT | 必須 | `queued` |  |
| `recipient_ref` | TEXT | 必須 |  |  |
| `delivery_attempts` | INTEGER | 必須 | `0` |  |
| `created_at` | TIMESTAMP WITH TIME ZONE | 必須 | `now()` |  |
| `sent_at` | TIMESTAMP WITH TIME ZONE |  |  |  |
| `acknowledged_at` | TIMESTAMP WITH TIME ZONE |  |  |  |
| `acknowledged_by` | UUID |  |  | → `app_user.id` |
