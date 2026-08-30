import ast
import inspect
from datetime import UTC, datetime, timedelta, timezone
from typing import get_args
from unittest.mock import MagicMock
from uuid import uuid4

import pytest
from app.api.routes import market_data as routes
from app.market_data.application import use_cases as cases
from app.models.catalog import AuditLog, BackfillJob, MarketDataSubscription
from app.schemas.catalog import CandleBackfillCreate, Timeframe
from fastapi import BackgroundTasks
from sqlalchemy.dialects import postgresql


def test_application_has_no_http_or_response_schema_imports() -> None:
    tree = ast.parse(inspect.getsource(cases))
    modules = [node.module for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)]
    assert not any(
        module and module.startswith(("fastapi", "app.api", "app.schemas")) for module in modules
    )
    assert tuple(get_args(Timeframe)) == cases.SUPPORTED_TIMEFRAMES


@pytest.mark.parametrize("enabled", [True, False])
def test_bulk_updates_and_audits_all_frames_in_one_transaction(enabled) -> None:
    db, validator = MagicMock(), MagicMock()
    workspace_id, instrument_id = uuid4(), uuid4()
    db.scalar.side_effect = [instrument_id, *([None] * 7)]
    subscriptions = cases.update_subscriptions(db, workspace_id, instrument_id, enabled, validator)
    assert [sub.timeframe for sub in subscriptions] == list(cases.SUPPORTED_TIMEFRAMES)
    assert all(sub.enabled == enabled for sub in subscriptions)
    assert all(sub.workspace_id == workspace_id for sub in subscriptions)
    audits = [call.args[0] for call in db.add.call_args_list if isinstance(call.args[0], AuditLog)]
    assert len(audits) == 7
    assert all(audit.workspace_id == workspace_id for audit in audits)
    assert all(audit.action == "market_data.subscription_updated" for audit in audits)
    assert all(audit.after_data["enabled"] == enabled for audit in audits)
    assert validator.call_count == int(enabled)
    db.commit.assert_called_once()
    db.rollback.assert_not_called()
    assert db.refresh.call_count == 7
    lock = str(db.execute.call_args.args[0].compile(dialect=postgresql.dialect()))
    assert "pg_advisory_xact_lock" in lock


def test_legacy_single_timeframe_preserves_other_subscriptions() -> None:
    db, validator = MagicMock(), MagicMock()
    workspace_id, instrument_id = uuid4(), uuid4()
    subscription = MarketDataSubscription(
        id=uuid4(),
        workspace_id=workspace_id,
        instrument_id=instrument_id,
        timeframe="5m",
        enabled=True,
    )
    db.scalar.side_effect = [instrument_id, subscription]
    result = cases.update_subscriptions(db, workspace_id, instrument_id, False, validator, "5m")
    assert result == [subscription]
    assert not subscription.enabled
    validator.assert_not_called()
    db.commit.assert_called_once()
    assert db.scalar.call_count == 2
    audit = db.add.call_args.args[0]
    assert audit.after_data["before"] == {"enabled": True}


@pytest.mark.parametrize("failure_at", ["preflight", "third_write", "commit"])
def test_collection_failures_roll_back_without_partial_commit(failure_at) -> None:
    db, validator = MagicMock(), MagicMock()
    workspace_id, instrument_id = uuid4(), uuid4()
    failure = RuntimeError("injected failure")
    db.scalar.side_effect = [instrument_id, *([None] * 7)]
    if failure_at == "preflight":
        validator.side_effect = failure
    elif failure_at == "third_write":
        db.flush.side_effect = [None, None, failure]
    else:
        db.commit.side_effect = failure
    with pytest.raises(RuntimeError, match="injected failure"):
        cases.update_subscriptions(db, workspace_id, instrument_id, True, validator)
    db.rollback.assert_called_once()
    if failure_at != "commit":
        db.commit.assert_not_called()
    if failure_at == "preflight":
        db.add.assert_not_called()
    db.refresh.assert_not_called()


@pytest.mark.parametrize("missing", ["workspace", "instrument"])
@pytest.mark.parametrize("operation", ["enqueue", "coverage", "subscriptions"])
def test_application_enforces_scope_without_http(missing, operation) -> None:
    db, validator = MagicMock(), MagicMock()
    workspace_id, instrument_id = uuid4(), uuid4()
    if missing == "workspace":
        db.get.return_value = None
    else:
        db.scalar.return_value = None
    with pytest.raises(cases.MarketDataApplicationError) as error:
        if operation == "enqueue":
            cases.enqueue_backfill(
                db, workspace_id, cases.BackfillCommand(instrument_id, "1m", 365), validator
            )
        elif operation == "coverage":
            cases.get_coverage(db, workspace_id, instrument_id, "1m")
        else:
            cases.update_subscriptions(db, workspace_id, instrument_id, True, validator)
    assert error.value.code == (
        "workspace_not_found" if missing == "workspace" else "instrument_unavailable"
    )
    validator.assert_not_called()
    db.add.assert_not_called()
    db.commit.assert_not_called()


def test_enqueue_persists_job_and_audit_before_return(monkeypatch) -> None:
    db, validator, overlap = MagicMock(), MagicMock(), MagicMock()
    workspace_id, instrument_id = uuid4(), uuid4()
    monkeypatch.setattr(cases, "ensure_no_overlapping_backfill", overlap)
    job = cases.enqueue_backfill(
        db, workspace_id, cases.BackfillCommand(instrument_id, "1m", 365), validator
    )
    assert isinstance(job, BackfillJob)
    assert job.workspace_id == workspace_id
    assert job.status == "queued"
    assert job.to_time - job.from_time == timedelta(days=365)
    validator.assert_called_once_with(db, workspace_id, instrument_id)
    overlap.assert_called_once_with(
        db,
        workspace_id=workspace_id,
        instrument_id=instrument_id,
        timeframe="1m",
        requested_from=job.from_time,
        requested_to=job.to_time,
    )
    audit = db.add.call_args_list[1].args[0]
    assert audit.action == "candle.backfill_queued"
    assert audit.after_data == {"instrument_id": str(instrument_id), "timeframe": "1m", "days": 365}
    db.commit.assert_called_once()


def test_failed_enqueue_never_dispatches_background_work(monkeypatch) -> None:
    db, tasks = MagicMock(), BackgroundTasks()
    db.commit.side_effect = RuntimeError("commit failed")
    monkeypatch.setattr(cases, "ensure_no_overlapping_backfill", MagicMock())
    monkeypatch.setattr(routes, "_validate_collection_configuration", MagicMock())
    with pytest.raises(RuntimeError, match="commit failed"):
        routes.create_candle_backfill(
            uuid4(), CandleBackfillCreate(instrument_id=uuid4()), tasks, db, "owner"
        )
    assert tasks.tasks == []
    db.rollback.assert_called_once()


def test_coverage_normalizes_offsets_without_http(monkeypatch) -> None:
    db, builder = MagicMock(), MagicMock(return_value={"stored_count": 0})
    monkeypatch.setattr(cases, "build_candle_coverage", builder)
    workspace_id, instrument_id = uuid4(), uuid4()
    start = datetime(2026, 8, 1, 9, tzinfo=timezone(timedelta(hours=9)))
    end = start + timedelta(days=1)
    result = cases.get_coverage(db, workspace_id, instrument_id, "1m", start, end)
    assert result == {"stored_count": 0}
    assert builder.call_args.args[3].tzinfo == UTC
    assert builder.call_args.args[4].tzinfo == UTC
    db.commit.assert_not_called()


@pytest.mark.parametrize("days,frame", [(0, "1m"), (366, "1m"), (365, "bogus")])
def test_direct_backfill_commands_validate_bounds(days, frame) -> None:
    db, validator = MagicMock(), MagicMock()
    with pytest.raises(cases.MarketDataApplicationError) as error:
        cases.enqueue_backfill(db, uuid4(), cases.BackfillCommand(uuid4(), frame, days), validator)
    assert error.value.code == "invalid_input"
    db.add.assert_not_called()
    validator.assert_not_called()
