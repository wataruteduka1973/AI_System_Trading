-- FX Trading Rebuild
-- PostgreSQL physical schema draft v0.1
-- Target: PostgreSQL 16+
-- Generated from requirements/design documents v0.4/v0.5 draft.
-- Monetary and quantity values use NUMERIC. All business timestamps use TIMESTAMPTZ.

BEGIN;

CREATE EXTENSION IF NOT EXISTS pgcrypto;
CREATE SCHEMA IF NOT EXISTS fx;

SET search_path TO fx, public;

CREATE OR REPLACE FUNCTION fx.touch_updated_at()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    NEW.updated_at = CURRENT_TIMESTAMP;
    RETURN NEW;
END;
$$;

CREATE TABLE workspace (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    name text NOT NULL,
    status text NOT NULL DEFAULT 'active'
        CHECK (status IN ('active', 'suspended', 'closed')),
    created_at timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE app_user (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    email text NOT NULL,
    display_name text NOT NULL,
    status text NOT NULL DEFAULT 'active'
        CHECK (status IN ('invited', 'active', 'disabled')),
    oidc_subject text,
    created_at timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT uq_app_user_email UNIQUE (email),
    CONSTRAINT uq_app_user_oidc_subject UNIQUE (oidc_subject)
);

CREATE TABLE user_membership (
    workspace_id uuid NOT NULL REFERENCES workspace(id) ON DELETE CASCADE,
    user_id uuid NOT NULL REFERENCES app_user(id) ON DELETE CASCADE,
    role text NOT NULL CHECK (role IN ('owner', 'operator', 'viewer')),
    created_at timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (workspace_id, user_id)
);

CREATE TABLE exchange (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    code text NOT NULL,
    name text NOT NULL,
    status text NOT NULL DEFAULT 'active'
        CHECK (status IN ('active', 'deprecated', 'disabled')),
    created_at timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT uq_exchange_code UNIQUE (code)
);

CREATE TABLE market (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    code text NOT NULL,
    asset_class text NOT NULL
        CHECK (asset_class IN ('foreign_fx', 'crypto')),
    product_type text NOT NULL
        CHECK (product_type IN ('fx_spot', 'spot', 'margin', 'perpetual', 'futures')),
    settlement_type text NOT NULL
        CHECK (settlement_type IN ('physical', 'cash', 'rolling_spot')),
    created_at timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT uq_market_code UNIQUE (code)
);

CREATE TABLE exchange_connection (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    workspace_id uuid NOT NULL REFERENCES workspace(id) ON DELETE CASCADE,
    exchange_id uuid NOT NULL REFERENCES exchange(id) ON DELETE RESTRICT,
    label text NOT NULL,
    environment text NOT NULL
        CHECK (environment IN ('practice', 'testnet', 'paper_data', 'live')),
    api_base_url text NOT NULL,
    secret_ref text,
    status text NOT NULL DEFAULT 'pending_credentials'
        CHECK (status IN (
            'pending_credentials', 'verifying', 'verified', 'invalid',
            'disabled', 'revoked'
        )),
    capabilities jsonb NOT NULL DEFAULT '{}'::jsonb,
    last_verified_at timestamptz,
    created_at timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT uq_connection_label UNIQUE (workspace_id, label),
    CONSTRAINT ck_connection_secret_state CHECK (
        status = 'pending_credentials' OR secret_ref IS NOT NULL
    )
);

CREATE TABLE external_account (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    connection_id uuid NOT NULL REFERENCES exchange_connection(id) ON DELETE CASCADE,
    external_account_ref_encrypted text NOT NULL,
    external_account_ref_hash text NOT NULL,
    external_account_ref_masked text NOT NULL,
    alias text,
    environment text NOT NULL
        CHECK (environment IN ('practice', 'testnet', 'live')),
    currency varchar(16) NOT NULL,
    hedging_enabled boolean,
    margin_rate numeric(20, 10),
    mt4_account_ref_masked text,
    gslo_mode text
        CHECK (gslo_mode IS NULL OR gslo_mode IN ('disabled', 'allowed', 'required')),
    capabilities jsonb NOT NULL DEFAULT '{}'::jsonb,
    status text NOT NULL DEFAULT 'active'
        CHECK (status IN ('active', 'restricted', 'unavailable', 'disabled')),
    synced_at timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP,
    created_at timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT uq_external_account UNIQUE (connection_id, external_account_ref_hash),
    CONSTRAINT ck_external_account_margin_rate CHECK (
        margin_rate IS NULL OR margin_rate > 0
    )
);

CREATE TABLE account_selection_policy (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    workspace_id uuid NOT NULL REFERENCES workspace(id) ON DELETE CASCADE,
    name text NOT NULL,
    version integer NOT NULL CHECK (version > 0),
    criteria jsonb NOT NULL,
    checksum varchar(64) NOT NULL,
    status text NOT NULL DEFAULT 'draft'
        CHECK (status IN ('draft', 'approved', 'retired')),
    created_by uuid REFERENCES app_user(id) ON DELETE SET NULL,
    created_at timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT uq_account_selection_policy_version UNIQUE (workspace_id, name, version),
    CONSTRAINT uq_account_selection_policy_checksum UNIQUE (workspace_id, checksum)
);

CREATE TABLE instrument (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    exchange_id uuid NOT NULL REFERENCES exchange(id) ON DELETE RESTRICT,
    market_id uuid NOT NULL REFERENCES market(id) ON DELETE RESTRICT,
    symbol text NOT NULL,
    base_asset varchar(32) NOT NULL,
    quote_asset varchar(32) NOT NULL,
    contract_size numeric(38, 18),
    price_scale smallint NOT NULL CHECK (price_scale BETWEEN 0 AND 18),
    quantity_scale smallint NOT NULL CHECK (quantity_scale BETWEEN 0 AND 18),
    tick_size numeric(38, 18) NOT NULL CHECK (tick_size > 0),
    step_size numeric(38, 18) NOT NULL CHECK (step_size > 0),
    min_quantity numeric(38, 18),
    max_quantity numeric(38, 18),
    min_notional numeric(38, 18),
    margin_asset varchar(32),
    allowed_order_types text[] NOT NULL DEFAULT ARRAY[]::text[],
    capabilities jsonb NOT NULL DEFAULT '{}'::jsonb,
    status text NOT NULL DEFAULT 'active'
        CHECK (status IN ('active', 'halted', 'delisted', 'disabled')),
    rules_synced_at timestamptz,
    created_at timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT uq_instrument_symbol UNIQUE (exchange_id, market_id, symbol),
    CONSTRAINT ck_instrument_quantity_range CHECK (
        min_quantity IS NULL OR max_quantity IS NULL OR min_quantity <= max_quantity
    ),
    CONSTRAINT ck_instrument_min_notional CHECK (min_notional IS NULL OR min_notional >= 0)
);

CREATE TABLE candle (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    instrument_id uuid NOT NULL REFERENCES instrument(id) ON DELETE RESTRICT,
    timeframe text NOT NULL CHECK (timeframe IN ('1m', '5m', '15m', '30m', '1h', '4h', '1d')),
    open_time timestamptz NOT NULL,
    close_time timestamptz NOT NULL,
    open numeric(38, 18) NOT NULL CHECK (open > 0),
    high numeric(38, 18) NOT NULL CHECK (high > 0),
    low numeric(38, 18) NOT NULL CHECK (low > 0),
    close numeric(38, 18) NOT NULL CHECK (close > 0),
    volume numeric(38, 18) CHECK (volume IS NULL OR volume >= 0),
    trade_count bigint CHECK (trade_count IS NULL OR trade_count >= 0),
    source text NOT NULL,
    quality_status text NOT NULL DEFAULT 'complete'
        CHECK (quality_status IN ('provisional', 'complete', 'backfilled', 'corrected', 'invalid')),
    is_final boolean NOT NULL DEFAULT true,
    received_at timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP,
    corrected_at timestamptz,
    CONSTRAINT uq_candle_business_key UNIQUE (instrument_id, timeframe, open_time),
    CONSTRAINT ck_candle_time CHECK (close_time > open_time),
    CONSTRAINT ck_candle_ohlc CHECK (
        high >= GREATEST(open, close, low)
        AND low <= LEAST(open, close, high)
    )
);

CREATE TABLE market_data_gap (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    instrument_id uuid NOT NULL REFERENCES instrument(id) ON DELETE RESTRICT,
    timeframe text NOT NULL,
    from_time timestamptz NOT NULL,
    to_time timestamptz NOT NULL,
    expected_count integer CHECK (expected_count IS NULL OR expected_count >= 0),
    missing_count integer CHECK (missing_count IS NULL OR missing_count >= 0),
    reason_code text NOT NULL,
    status text NOT NULL DEFAULT 'open'
        CHECK (status IN ('open', 'backfilling', 'validating', 'resolved', 'ignored', 'failed')),
    detected_at timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP,
    resolved_at timestamptz,
    CONSTRAINT ck_market_data_gap_time CHECK (to_time > from_time)
);

CREATE TABLE backfill_job (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    gap_id uuid REFERENCES market_data_gap(id) ON DELETE SET NULL,
    workspace_id uuid NOT NULL REFERENCES workspace(id) ON DELETE CASCADE,
    instrument_id uuid NOT NULL REFERENCES instrument(id) ON DELETE RESTRICT,
    timeframe text NOT NULL,
    from_time timestamptz NOT NULL,
    to_time timestamptz NOT NULL,
    requested_by uuid REFERENCES app_user(id) ON DELETE SET NULL,
    trigger_type text NOT NULL CHECK (trigger_type IN ('automatic', 'manual')),
    status text NOT NULL DEFAULT 'queued'
        CHECK (status IN ('queued', 'running', 'validating', 'succeeded', 'failed', 'cancelled')),
    attempts integer NOT NULL DEFAULT 0 CHECK (attempts >= 0),
    rows_written integer NOT NULL DEFAULT 0 CHECK (rows_written >= 0),
    validation_result jsonb NOT NULL DEFAULT '{}'::jsonb,
    error_code text,
    started_at timestamptz,
    finished_at timestamptz,
    created_at timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT ck_backfill_time CHECK (to_time > from_time)
);

CREATE TABLE strategy (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    workspace_id uuid NOT NULL REFERENCES workspace(id) ON DELETE CASCADE,
    name text NOT NULL,
    mode text NOT NULL CHECK (mode IN ('technical', 'ai_model')),
    status text NOT NULL DEFAULT 'active'
        CHECK (status IN ('active', 'retired')),
    created_at timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT uq_strategy_name UNIQUE (workspace_id, name)
);

CREATE TABLE strategy_version (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    strategy_id uuid NOT NULL REFERENCES strategy(id) ON DELETE CASCADE,
    version integer NOT NULL CHECK (version > 0),
    supported_market_types text[] NOT NULL,
    definition jsonb NOT NULL,
    feature_schema jsonb NOT NULL DEFAULT '{}'::jsonb,
    checksum varchar(64) NOT NULL,
    lifecycle_status text NOT NULL DEFAULT 'draft'
        CHECK (lifecycle_status IN (
            'draft', 'validated', 'backtested', 'walk_forward_passed',
            'paper_approved', 'live_approved', 'suspended', 'retired'
        )),
    created_by uuid REFERENCES app_user(id) ON DELETE SET NULL,
    created_at timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT uq_strategy_version UNIQUE (strategy_id, version),
    CONSTRAINT uq_strategy_version_checksum UNIQUE (strategy_id, checksum)
);

CREATE TABLE risk_profile (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    workspace_id uuid NOT NULL REFERENCES workspace(id) ON DELETE CASCADE,
    name text NOT NULL,
    status text NOT NULL DEFAULT 'active'
        CHECK (status IN ('active', 'retired')),
    created_at timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT uq_risk_profile_name UNIQUE (workspace_id, name)
);

CREATE TABLE risk_profile_version (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    risk_profile_id uuid NOT NULL REFERENCES risk_profile(id) ON DELETE CASCADE,
    version integer NOT NULL CHECK (version > 0),
    rules jsonb NOT NULL,
    checksum varchar(64) NOT NULL,
    template_code text,
    status text NOT NULL DEFAULT 'draft'
        CHECK (status IN ('draft', 'approved', 'retired')),
    created_by uuid REFERENCES app_user(id) ON DELETE SET NULL,
    created_at timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT uq_risk_profile_version UNIQUE (risk_profile_id, version),
    CONSTRAINT uq_risk_profile_version_checksum UNIQUE (risk_profile_id, checksum)
);

CREATE TABLE model_source (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    name text NOT NULL,
    provider_type text NOT NULL CHECK (provider_type IN ('huggingface_hub', 'internal')),
    api_base_url text,
    enabled boolean NOT NULL DEFAULT false,
    policy jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT uq_model_source_name UNIQUE (name)
);

CREATE TABLE model_candidate (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    source_id uuid NOT NULL REFERENCES model_source(id) ON DELETE RESTRICT,
    external_repo_id text NOT NULL,
    revision text NOT NULL,
    task text NOT NULL,
    license_spdx text,
    formats text[] NOT NULL DEFAULT ARRAY[]::text[],
    metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
    review_status text NOT NULL DEFAULT 'discovered'
        CHECK (review_status IN (
            'discovered', 'quarantined', 'security_reviewed', 'compatible',
            'evaluated', 'approved', 'rejected'
        )),
    discovered_at timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT uq_model_candidate_revision UNIQUE (source_id, external_repo_id, revision)
);

CREATE TABLE dataset_snapshot (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    workspace_id uuid NOT NULL REFERENCES workspace(id) ON DELETE CASCADE,
    instruments jsonb NOT NULL,
    intervals jsonb NOT NULL,
    from_time timestamptz NOT NULL,
    to_time timestamptz NOT NULL,
    feature_schema jsonb NOT NULL,
    row_count bigint NOT NULL CHECK (row_count >= 0),
    quality_report jsonb NOT NULL,
    checksum varchar(64) NOT NULL,
    storage_uri text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT uq_dataset_snapshot_checksum UNIQUE (workspace_id, checksum),
    CONSTRAINT ck_dataset_snapshot_time CHECK (to_time > from_time)
);

CREATE TABLE training_run (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    workspace_id uuid NOT NULL REFERENCES workspace(id) ON DELETE CASCADE,
    dataset_snapshot_id uuid NOT NULL REFERENCES dataset_snapshot(id) ON DELETE RESTRICT,
    architecture text NOT NULL,
    hyperparameters jsonb NOT NULL,
    seed bigint NOT NULL,
    code_version text NOT NULL,
    resource_limits jsonb NOT NULL,
    status text NOT NULL DEFAULT 'queued'
        CHECK (status IN ('queued', 'running', 'succeeded', 'failed', 'cancelled')),
    metrics jsonb NOT NULL DEFAULT '{}'::jsonb,
    error_code text,
    started_at timestamptz,
    finished_at timestamptz,
    created_at timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE model_artifact (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    workspace_id uuid NOT NULL REFERENCES workspace(id) ON DELETE CASCADE,
    training_run_id uuid REFERENCES training_run(id) ON DELETE RESTRICT,
    candidate_id uuid REFERENCES model_candidate(id) ON DELETE RESTRICT,
    name text NOT NULL,
    version integer NOT NULL CHECK (version > 0),
    artifact_uri text NOT NULL,
    format text NOT NULL CHECK (format IN ('safetensors', 'onnx')),
    checksum varchar(64) NOT NULL,
    feature_schema jsonb NOT NULL,
    metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
    status text NOT NULL DEFAULT 'quarantined'
        CHECK (status IN (
            'quarantined', 'security_reviewed', 'compatible', 'evaluated',
            'approved', 'paper_observation', 'deployable', 'deployed',
            'retired', 'rejected'
        )),
    created_at timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT uq_model_artifact_version UNIQUE (workspace_id, name, version),
    CONSTRAINT uq_model_artifact_checksum UNIQUE (workspace_id, checksum),
    CONSTRAINT ck_model_artifact_origin CHECK (
        (training_run_id IS NOT NULL AND candidate_id IS NULL)
        OR (training_run_id IS NULL AND candidate_id IS NOT NULL)
    )
);

CREATE TABLE model_security_review (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    model_candidate_id uuid NOT NULL REFERENCES model_candidate(id) ON DELETE CASCADE,
    revision text NOT NULL,
    license_result jsonb NOT NULL,
    format_result jsonb NOT NULL,
    malware_result jsonb NOT NULL,
    remote_code_result jsonb NOT NULL,
    checksum_result jsonb NOT NULL,
    status text NOT NULL CHECK (status IN ('passed', 'failed', 'manual_review')),
    reviewed_at timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT uq_model_security_review UNIQUE (model_candidate_id, revision)
);

CREATE TABLE model_evaluation (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    model_artifact_id uuid NOT NULL REFERENCES model_artifact(id) ON DELETE CASCADE,
    dataset_snapshot_id uuid NOT NULL REFERENCES dataset_snapshot(id) ON DELETE RESTRICT,
    evaluation_type text NOT NULL
        CHECK (evaluation_type IN ('holdout', 'walk_forward', 'backtest', 'paper')),
    metrics jsonb NOT NULL,
    trading_metrics jsonb NOT NULL,
    leakage_checks jsonb NOT NULL,
    status text NOT NULL CHECK (status IN ('passed', 'failed', 'inconclusive')),
    evaluated_at timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE trading_account (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    workspace_id uuid NOT NULL REFERENCES workspace(id) ON DELETE CASCADE,
    connection_id uuid REFERENCES exchange_connection(id) ON DELETE RESTRICT,
    external_account_id uuid REFERENCES external_account(id) ON DELETE RESTRICT,
    mode text NOT NULL CHECK (mode IN ('paper', 'live')),
    base_currency varchar(16) NOT NULL,
    selection_mode text NOT NULL DEFAULT 'manual'
        CHECK (selection_mode IN ('manual', 'policy')),
    selection_policy_id uuid REFERENCES account_selection_policy(id) ON DELETE RESTRICT,
    status text NOT NULL DEFAULT 'active'
        CHECK (status IN ('active', 'halted', 'disabled')),
    created_at timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT ck_trading_account_selection CHECK (
        (selection_mode = 'manual' AND selection_policy_id IS NULL)
        OR (selection_mode = 'policy' AND selection_policy_id IS NOT NULL)
    ),
    CONSTRAINT ck_live_account_external CHECK (
        mode = 'paper' OR external_account_id IS NOT NULL
    )
);

CREATE TABLE trading_bot (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    workspace_id uuid NOT NULL REFERENCES workspace(id) ON DELETE CASCADE,
    name text NOT NULL,
    execution_mode text NOT NULL CHECK (execution_mode IN ('paper', 'live')),
    strategy_mode text NOT NULL CHECK (strategy_mode IN ('technical', 'ai_model')),
    connection_id uuid NOT NULL REFERENCES exchange_connection(id) ON DELETE RESTRICT,
    account_id uuid NOT NULL REFERENCES trading_account(id) ON DELETE RESTRICT,
    instrument_id uuid NOT NULL REFERENCES instrument(id) ON DELETE RESTRICT,
    timeframe text NOT NULL,
    strategy_version_id uuid NOT NULL REFERENCES strategy_version(id) ON DELETE RESTRICT,
    risk_profile_version_id uuid NOT NULL REFERENCES risk_profile_version(id) ON DELETE RESTRICT,
    desired_state text NOT NULL DEFAULT 'stopped'
        CHECK (desired_state IN ('stopped', 'running', 'paused')),
    actual_state text NOT NULL DEFAULT 'stopped'
        CHECK (actual_state IN ('stopped', 'starting', 'running', 'pausing', 'paused', 'stopping', 'failed')),
    live_trading_enabled boolean NOT NULL DEFAULT false,
    version bigint NOT NULL DEFAULT 1 CHECK (version > 0),
    created_at timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT uq_trading_bot_name UNIQUE (workspace_id, name),
    CONSTRAINT ck_live_bot_enablement CHECK (
        live_trading_enabled = false OR execution_mode = 'live'
    )
);

CREATE TABLE algorithm_evaluation (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    strategy_version_id uuid NOT NULL REFERENCES strategy_version(id) ON DELETE CASCADE,
    evaluation_type text NOT NULL
        CHECK (evaluation_type IN ('static', 'backtest', 'walk_forward', 'paper')),
    dataset_snapshot_id uuid REFERENCES dataset_snapshot(id) ON DELETE RESTRICT,
    metrics jsonb NOT NULL,
    risk_metrics jsonb NOT NULL,
    status text NOT NULL CHECK (status IN ('passed', 'failed', 'inconclusive')),
    evaluated_at timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE algorithm_approval (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    strategy_version_id uuid NOT NULL REFERENCES strategy_version(id) ON DELETE CASCADE,
    environment text NOT NULL CHECK (environment IN ('paper', 'live')),
    decision text NOT NULL CHECK (decision IN ('approved', 'rejected', 'revoked')),
    approved_by uuid REFERENCES app_user(id) ON DELETE SET NULL,
    reason text NOT NULL,
    expires_at timestamptz,
    decided_at timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE algorithm_deployment (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    strategy_version_id uuid NOT NULL REFERENCES strategy_version(id) ON DELETE RESTRICT,
    bot_id uuid NOT NULL REFERENCES trading_bot(id) ON DELETE CASCADE,
    environment text NOT NULL CHECK (environment IN ('paper', 'live')),
    status text NOT NULL CHECK (status IN ('pending', 'active', 'rolled_back', 'retired', 'failed')),
    deployed_by uuid REFERENCES app_user(id) ON DELETE SET NULL,
    deployed_at timestamptz,
    rolled_back_from_id uuid REFERENCES algorithm_deployment(id) ON DELETE SET NULL,
    created_at timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE model_deployment (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    model_artifact_id uuid NOT NULL REFERENCES model_artifact(id) ON DELETE RESTRICT,
    bot_id uuid NOT NULL REFERENCES trading_bot(id) ON DELETE CASCADE,
    environment text NOT NULL CHECK (environment IN ('paper', 'live')),
    status text NOT NULL CHECK (status IN ('pending', 'active', 'retired', 'failed')),
    approved_by uuid REFERENCES app_user(id) ON DELETE SET NULL,
    approved_at timestamptz,
    deployed_at timestamptz,
    retired_at timestamptz,
    created_at timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE bot_run (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    bot_id uuid NOT NULL REFERENCES trading_bot(id) ON DELETE CASCADE,
    status text NOT NULL DEFAULT 'starting'
        CHECK (status IN ('starting', 'running', 'paused', 'stopping', 'stopped', 'failed')),
    code_version text NOT NULL,
    started_at timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP,
    stopped_at timestamptz,
    stop_reason text,
    heartbeat_at timestamptz,
    CONSTRAINT ck_bot_run_time CHECK (stopped_at IS NULL OR stopped_at >= started_at)
);

CREATE TABLE signal (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    workspace_id uuid NOT NULL REFERENCES workspace(id) ON DELETE CASCADE,
    bot_run_id uuid NOT NULL REFERENCES bot_run(id) ON DELETE CASCADE,
    candle_id uuid NOT NULL REFERENCES candle(id) ON DELETE RESTRICT,
    strategy_version_id uuid NOT NULL REFERENCES strategy_version(id) ON DELETE RESTRICT,
    model_artifact_id uuid REFERENCES model_artifact(id) ON DELETE RESTRICT,
    action text NOT NULL CHECK (action IN ('buy', 'sell', 'hold', 'exit')),
    score numeric(20, 10),
    rationale jsonb NOT NULL,
    input_checksum varchar(64) NOT NULL,
    correlation_id uuid NOT NULL DEFAULT gen_random_uuid(),
    created_at timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT uq_signal_idempotency UNIQUE (
        bot_run_id, candle_id, strategy_version_id, input_checksum
    )
);

CREATE TABLE risk_decision (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    signal_id uuid NOT NULL REFERENCES signal(id) ON DELETE CASCADE,
    risk_profile_version_id uuid NOT NULL REFERENCES risk_profile_version(id) ON DELETE RESTRICT,
    outcome text NOT NULL CHECK (outcome IN ('allow', 'deny', 'allow_with_adjustment')),
    rule_results jsonb NOT NULL,
    adjusted_quantity numeric(38, 18),
    reason_code text,
    decided_at timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT uq_risk_decision_signal UNIQUE (signal_id, risk_profile_version_id)
);

CREATE TABLE system_event (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    workspace_id uuid NOT NULL REFERENCES workspace(id) ON DELETE CASCADE,
    occurred_at timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP,
    severity text NOT NULL CHECK (severity IN ('debug', 'info', 'warning', 'error', 'critical')),
    category text NOT NULL CHECK (category IN (
        'market_data', 'connection', 'strategy', 'model', 'risk',
        'order', 'fill', 'system', 'security', 'notification'
    )),
    event_type text NOT NULL,
    reason_code text,
    source_type text NOT NULL,
    source_id uuid,
    target_type text,
    target_id uuid,
    correlation_id uuid NOT NULL,
    message text NOT NULL,
    payload jsonb NOT NULL DEFAULT '{}'::jsonb,
    contains_sensitive_data boolean NOT NULL DEFAULT false,
    CONSTRAINT ck_system_event_sensitive CHECK (contains_sensitive_data = false)
);

CREATE TABLE trading_halt (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    workspace_id uuid NOT NULL REFERENCES workspace(id) ON DELETE CASCADE,
    scope_type text NOT NULL CHECK (scope_type IN (
        'system', 'workspace', 'connection', 'account', 'bot', 'instrument'
    )),
    scope_id uuid,
    level text NOT NULL CHECK (level IN (
        'warning', 'entry_halted', 'all_trading_halted', 'emergency_stopped'
    )),
    reason_code text NOT NULL,
    trigger_event_id uuid REFERENCES system_event(id) ON DELETE SET NULL,
    auto_releasable boolean NOT NULL DEFAULT false,
    release_condition jsonb NOT NULL DEFAULT '{}'::jsonb,
    status text NOT NULL DEFAULT 'active'
        CHECK (status IN ('active', 'release_pending', 'released')),
    halted_at timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP,
    released_at timestamptz,
    released_by uuid REFERENCES app_user(id) ON DELETE SET NULL,
    CONSTRAINT ck_trading_halt_scope CHECK (
        (scope_type IN ('system', 'workspace') AND scope_id IS NULL)
        OR (scope_type NOT IN ('system', 'workspace') AND scope_id IS NOT NULL)
    ),
    CONSTRAINT ck_trading_halt_release CHECK (
        (status <> 'released' AND released_at IS NULL)
        OR (status = 'released' AND released_at IS NOT NULL)
    )
);

CREATE TABLE notification (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    workspace_id uuid NOT NULL REFERENCES workspace(id) ON DELETE CASCADE,
    event_id uuid NOT NULL REFERENCES system_event(id) ON DELETE CASCADE,
    channel text NOT NULL CHECK (channel IN ('in_app', 'email')),
    status text NOT NULL DEFAULT 'queued'
        CHECK (status IN ('queued', 'sent', 'failed', 'acknowledged')),
    recipient_ref text NOT NULL,
    delivery_attempts integer NOT NULL DEFAULT 0 CHECK (delivery_attempts >= 0),
    created_at timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP,
    sent_at timestamptz,
    acknowledged_at timestamptz,
    acknowledged_by uuid REFERENCES app_user(id) ON DELETE SET NULL
);

CREATE TABLE order_intent (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    signal_id uuid NOT NULL REFERENCES signal(id) ON DELETE RESTRICT,
    risk_decision_id uuid NOT NULL REFERENCES risk_decision(id) ON DELETE RESTRICT,
    side text NOT NULL CHECK (side IN ('buy', 'sell')),
    order_type text NOT NULL CHECK (order_type IN ('market', 'limit')),
    requested_quantity numeric(38, 18) NOT NULL CHECK (requested_quantity > 0),
    limit_price numeric(38, 18),
    stop_price numeric(38, 18),
    take_profit_price numeric(38, 18),
    max_slippage_bps integer CHECK (max_slippage_bps IS NULL OR max_slippage_bps >= 0),
    expires_at timestamptz,
    created_at timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT uq_order_intent_signal UNIQUE (signal_id),
    CONSTRAINT uq_order_intent_risk UNIQUE (risk_decision_id),
    CONSTRAINT ck_order_intent_limit CHECK (
        (order_type = 'market' AND limit_price IS NULL)
        OR (order_type = 'limit' AND limit_price IS NOT NULL AND limit_price > 0)
    ),
    CONSTRAINT ck_order_intent_stop CHECK (stop_price IS NULL OR stop_price > 0),
    CONSTRAINT ck_order_intent_take_profit CHECK (
        take_profit_price IS NULL OR take_profit_price > 0
    )
);

CREATE TABLE trade_order (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    workspace_id uuid NOT NULL REFERENCES workspace(id) ON DELETE CASCADE,
    account_id uuid NOT NULL REFERENCES trading_account(id) ON DELETE RESTRICT,
    order_intent_id uuid REFERENCES order_intent(id) ON DELETE RESTRICT,
    client_order_id text NOT NULL,
    external_order_id text,
    instrument_id uuid NOT NULL REFERENCES instrument(id) ON DELETE RESTRICT,
    side text NOT NULL CHECK (side IN ('buy', 'sell')),
    order_type text NOT NULL CHECK (order_type IN ('market', 'limit')),
    time_in_force text NOT NULL CHECK (time_in_force IN ('gtc', 'gtd', 'gfd', 'fok', 'ioc')),
    quantity numeric(38, 18) NOT NULL CHECK (quantity > 0),
    filled_quantity numeric(38, 18) NOT NULL DEFAULT 0 CHECK (filled_quantity >= 0),
    limit_price numeric(38, 18),
    stop_price numeric(38, 18),
    status text NOT NULL DEFAULT 'pending'
        CHECK (status IN (
            'pending', 'submitted', 'partially_filled', 'filled',
            'cancel_pending', 'cancelled', 'rejected', 'expired', 'unknown'
        )),
    submitted_at timestamptz,
    created_at timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT uq_trade_order_client_id UNIQUE (account_id, client_order_id),
    CONSTRAINT uq_trade_order_external_id UNIQUE NULLS NOT DISTINCT (account_id, external_order_id),
    CONSTRAINT ck_trade_order_filled_quantity CHECK (filled_quantity <= quantity),
    CONSTRAINT ck_trade_order_limit_price CHECK (
        order_type <> 'limit' OR (limit_price IS NOT NULL AND limit_price > 0)
    )
);

CREATE TABLE order_status_history (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    order_id uuid NOT NULL REFERENCES trade_order(id) ON DELETE CASCADE,
    from_status text,
    to_status text NOT NULL,
    reason_code text,
    raw_ref text,
    occurred_at timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE fill (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    order_id uuid NOT NULL REFERENCES trade_order(id) ON DELETE RESTRICT,
    external_fill_id text,
    price numeric(38, 18) NOT NULL CHECK (price > 0),
    quantity numeric(38, 18) NOT NULL CHECK (quantity > 0),
    fee_amount numeric(38, 18) NOT NULL DEFAULT 0 CHECK (fee_amount >= 0),
    fee_asset varchar(32),
    liquidity_role text CHECK (liquidity_role IS NULL OR liquidity_role IN ('maker', 'taker', 'unknown')),
    executed_at timestamptz NOT NULL,
    recorded_at timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT uq_fill_external_id UNIQUE NULLS NOT DISTINCT (order_id, external_fill_id)
);

CREATE TABLE trading_position (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    account_id uuid NOT NULL REFERENCES trading_account(id) ON DELETE RESTRICT,
    instrument_id uuid NOT NULL REFERENCES instrument(id) ON DELETE RESTRICT,
    side text NOT NULL CHECK (side IN ('long', 'short', 'net')),
    quantity numeric(38, 18) NOT NULL CHECK (quantity >= 0),
    average_entry_price numeric(38, 18),
    realized_pnl numeric(38, 18) NOT NULL DEFAULT 0,
    unrealized_pnl numeric(38, 18) NOT NULL DEFAULT 0,
    status text NOT NULL CHECK (status IN ('open', 'closed')),
    version bigint NOT NULL DEFAULT 1 CHECK (version > 0),
    opened_at timestamptz,
    closed_at timestamptz,
    created_at timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT ck_position_price CHECK (
        average_entry_price IS NULL OR average_entry_price > 0
    ),
    CONSTRAINT ck_position_closed CHECK (
        (status = 'open' AND closed_at IS NULL)
        OR (status = 'closed' AND closed_at IS NOT NULL)
    )
);

CREATE TABLE position_movement (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    position_id uuid NOT NULL REFERENCES trading_position(id) ON DELETE RESTRICT,
    fill_id uuid NOT NULL REFERENCES fill(id) ON DELETE RESTRICT,
    quantity_delta numeric(38, 18) NOT NULL,
    realized_pnl_delta numeric(38, 18) NOT NULL DEFAULT 0,
    occurred_at timestamptz NOT NULL,
    CONSTRAINT uq_position_movement_fill UNIQUE (position_id, fill_id)
);

CREATE TABLE ledger_transaction (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    account_id uuid NOT NULL REFERENCES trading_account(id) ON DELETE RESTRICT,
    reference_type text NOT NULL,
    reference_id uuid,
    description text NOT NULL,
    occurred_at timestamptz NOT NULL,
    created_at timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE ledger_entry (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    transaction_id uuid NOT NULL REFERENCES ledger_transaction(id) ON DELETE RESTRICT,
    account_id uuid NOT NULL REFERENCES trading_account(id) ON DELETE RESTRICT,
    fill_id uuid REFERENCES fill(id) ON DELETE RESTRICT,
    asset varchar(32) NOT NULL,
    amount numeric(38, 18) NOT NULL CHECK (amount <> 0),
    entry_type text NOT NULL CHECK (entry_type IN (
        'cash', 'position', 'fee', 'realized_pnl', 'financing',
        'funding', 'deposit', 'withdrawal', 'adjustment'
    )),
    occurred_at timestamptz NOT NULL
);

CREATE TABLE account_snapshot (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    account_id uuid NOT NULL REFERENCES trading_account(id) ON DELETE CASCADE,
    captured_at timestamptz NOT NULL,
    balances jsonb NOT NULL,
    equity numeric(38, 18) NOT NULL,
    unrealized_pnl numeric(38, 18) NOT NULL DEFAULT 0,
    margin_used numeric(38, 18),
    margin_available numeric(38, 18),
    margin_call_percent numeric(20, 10),
    margin_closeout_percent numeric(20, 10),
    source text NOT NULL CHECK (source IN ('paper_ledger', 'exchange_api', 'reconciliation')),
    CONSTRAINT uq_account_snapshot_time UNIQUE (account_id, captured_at)
);

CREATE TABLE backtest_run (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    workspace_id uuid NOT NULL REFERENCES workspace(id) ON DELETE CASCADE,
    strategy_version_id uuid NOT NULL REFERENCES strategy_version(id) ON DELETE RESTRICT,
    risk_profile_version_id uuid NOT NULL REFERENCES risk_profile_version(id) ON DELETE RESTRICT,
    dataset_snapshot_id uuid NOT NULL REFERENCES dataset_snapshot(id) ON DELETE RESTRICT,
    parameters jsonb NOT NULL,
    code_version text NOT NULL,
    status text NOT NULL DEFAULT 'queued'
        CHECK (status IN ('queued', 'running', 'succeeded', 'failed', 'cancelled')),
    summary_metrics jsonb NOT NULL DEFAULT '{}'::jsonb,
    started_at timestamptz,
    finished_at timestamptz,
    created_at timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE backtest_trade (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    backtest_run_id uuid NOT NULL REFERENCES backtest_run(id) ON DELETE CASCADE,
    sequence_no integer NOT NULL CHECK (sequence_no > 0),
    instrument_id uuid NOT NULL REFERENCES instrument(id) ON DELETE RESTRICT,
    side text NOT NULL CHECK (side IN ('buy', 'sell')),
    entry_time timestamptz NOT NULL,
    exit_time timestamptz,
    entry_price numeric(38, 18) NOT NULL CHECK (entry_price > 0),
    exit_price numeric(38, 18),
    quantity numeric(38, 18) NOT NULL CHECK (quantity > 0),
    fees numeric(38, 18) NOT NULL DEFAULT 0 CHECK (fees >= 0),
    realized_pnl numeric(38, 18),
    metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
    CONSTRAINT uq_backtest_trade_sequence UNIQUE (backtest_run_id, sequence_no),
    CONSTRAINT ck_backtest_trade_time CHECK (exit_time IS NULL OR exit_time >= entry_time),
    CONSTRAINT ck_backtest_trade_exit_price CHECK (exit_price IS NULL OR exit_price > 0)
);

CREATE TABLE audit_log (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    workspace_id uuid NOT NULL REFERENCES workspace(id) ON DELETE RESTRICT,
    actor_id uuid REFERENCES app_user(id) ON DELETE SET NULL,
    action text NOT NULL,
    resource_type text NOT NULL,
    resource_id uuid,
    before_data jsonb,
    after_data jsonb,
    correlation_id uuid NOT NULL,
    ip_address inet,
    user_agent text,
    occurred_at timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE outbox_event (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    aggregate_type text NOT NULL,
    aggregate_id uuid NOT NULL,
    event_type text NOT NULL,
    payload jsonb NOT NULL,
    correlation_id uuid NOT NULL,
    status text NOT NULL DEFAULT 'pending'
        CHECK (status IN ('pending', 'published', 'failed')),
    attempts integer NOT NULL DEFAULT 0 CHECK (attempts >= 0),
    available_at timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP,
    published_at timestamptz,
    created_at timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE idempotency_record (
    workspace_id uuid NOT NULL REFERENCES workspace(id) ON DELETE CASCADE,
    idempotency_key text NOT NULL,
    operation text NOT NULL,
    request_hash varchar(64) NOT NULL,
    response_status integer,
    response_body jsonb,
    resource_type text,
    resource_id uuid,
    expires_at timestamptz NOT NULL,
    created_at timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (workspace_id, idempotency_key, operation)
);

-- One open position per account/instrument/side. Netting accounts use side='net'.
CREATE UNIQUE INDEX uq_position_open
    ON trading_position (account_id, instrument_id, side)
    WHERE status = 'open';

-- Only one active deployment of each kind per bot.
CREATE UNIQUE INDEX uq_algorithm_deployment_active
    ON algorithm_deployment (bot_id)
    WHERE status = 'active';

CREATE UNIQUE INDEX uq_model_deployment_active
    ON model_deployment (bot_id)
    WHERE status = 'active';

-- Only one active halt with the same scope and reason.
CREATE UNIQUE INDEX uq_trading_halt_active
    ON trading_halt (workspace_id, scope_type, COALESCE(scope_id, workspace_id), reason_code)
    WHERE status IN ('active', 'release_pending');

CREATE INDEX ix_candle_lookup
    ON candle (instrument_id, timeframe, open_time DESC);
CREATE INDEX ix_market_data_gap_open
    ON market_data_gap (instrument_id, timeframe, status, from_time);
CREATE INDEX ix_backfill_job_status
    ON backfill_job (workspace_id, status, created_at);
CREATE INDEX ix_training_run_status
    ON training_run (workspace_id, status, created_at DESC);
CREATE INDEX ix_model_evaluation_artifact
    ON model_evaluation (model_artifact_id, evaluation_type, evaluated_at DESC);
CREATE INDEX ix_bot_run_recent
    ON bot_run (bot_id, started_at DESC);
CREATE INDEX ix_signal_recent
    ON signal (bot_run_id, created_at DESC);
CREATE INDEX ix_system_event_workspace_time
    ON system_event (workspace_id, occurred_at DESC);
CREATE INDEX ix_system_event_correlation
    ON system_event (correlation_id, occurred_at);
CREATE INDEX ix_trade_order_account_status
    ON trade_order (account_id, status, updated_at DESC);
CREATE INDEX ix_fill_order_time
    ON fill (order_id, executed_at);
CREATE INDEX ix_position_account_status
    ON trading_position (account_id, status);
CREATE INDEX ix_ledger_entry_account_time
    ON ledger_entry (account_id, occurred_at DESC);
CREATE INDEX ix_account_snapshot_recent
    ON account_snapshot (account_id, captured_at DESC);
CREATE INDEX ix_audit_log_workspace_time
    ON audit_log (workspace_id, occurred_at DESC);
CREATE INDEX ix_outbox_pending
    ON outbox_event (status, available_at)
    WHERE status IN ('pending', 'failed');
CREATE INDEX ix_idempotency_expiry
    ON idempotency_record (expires_at);

CREATE TRIGGER trg_workspace_touch_updated_at
BEFORE UPDATE ON workspace
FOR EACH ROW EXECUTE FUNCTION fx.touch_updated_at();

CREATE TRIGGER trg_app_user_touch_updated_at
BEFORE UPDATE ON app_user
FOR EACH ROW EXECUTE FUNCTION fx.touch_updated_at();

CREATE TRIGGER trg_exchange_connection_touch_updated_at
BEFORE UPDATE ON exchange_connection
FOR EACH ROW EXECUTE FUNCTION fx.touch_updated_at();

CREATE TRIGGER trg_external_account_touch_updated_at
BEFORE UPDATE ON external_account
FOR EACH ROW EXECUTE FUNCTION fx.touch_updated_at();

CREATE TRIGGER trg_instrument_touch_updated_at
BEFORE UPDATE ON instrument
FOR EACH ROW EXECUTE FUNCTION fx.touch_updated_at();

CREATE TRIGGER trg_strategy_touch_updated_at
BEFORE UPDATE ON strategy
FOR EACH ROW EXECUTE FUNCTION fx.touch_updated_at();

CREATE TRIGGER trg_risk_profile_touch_updated_at
BEFORE UPDATE ON risk_profile
FOR EACH ROW EXECUTE FUNCTION fx.touch_updated_at();

CREATE TRIGGER trg_trading_account_touch_updated_at
BEFORE UPDATE ON trading_account
FOR EACH ROW EXECUTE FUNCTION fx.touch_updated_at();

CREATE TRIGGER trg_trading_bot_touch_updated_at
BEFORE UPDATE ON trading_bot
FOR EACH ROW EXECUTE FUNCTION fx.touch_updated_at();

CREATE TRIGGER trg_trade_order_touch_updated_at
BEFORE UPDATE ON trade_order
FOR EACH ROW EXECUTE FUNCTION fx.touch_updated_at();

CREATE TRIGGER trg_position_touch_updated_at
BEFORE UPDATE ON trading_position
FOR EACH ROW EXECUTE FUNCTION fx.touch_updated_at();

COMMENT ON SCHEMA fx IS 'FX Trading application schema. Secrets are stored outside PostgreSQL; only secret references are persisted.';
COMMENT ON TABLE candle IS 'Normalized immutable/final OHLCV bars. Corrections must be audited.';
COMMENT ON TABLE system_event IS 'Operational events. This is separate from actor-focused audit_log.';
COMMENT ON TABLE ledger_entry IS 'Append-only account ledger entries. Application code must not update or delete rows.';
COMMENT ON TABLE strategy_version IS 'Immutable algorithm definition version after creation.';
COMMENT ON TABLE risk_profile_version IS 'Immutable risk rule version after creation.';

COMMIT;
