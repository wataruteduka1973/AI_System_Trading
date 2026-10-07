"""Several instruments in one request (app/trading/application/backtest_batch.py,
docs/plans/backtest-batch.md): the budget is checked before anything runs, and one instrument's
failure does not take the others with it."""

from datetime import UTC, datetime
from decimal import Decimal
from unittest.mock import MagicMock
from uuid import uuid4

import pytest
from app.api.routes import backtests as routes
from app.db.session import get_db
from app.main import app
from app.models.backtest import BacktestRun
from app.models.instruments import Instrument
from app.models.workspace import AppUser
from app.security.rbac import require_operator_role, require_viewer_role
from app.trading.application import backtest_batch as batch
from app.trading.application.backtest_provisioning import BacktestProvisioningError
from app.trading.application.backtest_walk_forward import WalkForwardError
from fastapi.testclient import TestClient

FROM, TO = datetime(2024, 1, 1, tzinfo=UTC), datetime(2026, 9, 30, tzinfo=UTC)
client = TestClient(app)
_USER = AppUser(id=uuid4(), email="me@example.com", display_name="Me", status="active")


def _instrument(symbol: str) -> Instrument:
    return Instrument(id=uuid4(), symbol=symbol)


def _run() -> BacktestRun:
    now = datetime.now(UTC)
    return BacktestRun(
        id=uuid4(),
        workspace_id=uuid4(),
        strategy_version_id=uuid4(),
        risk_profile_version_id=uuid4(),
        dataset_snapshot_id=uuid4(),
        parameters={},
        code_version="x",
        status="succeeded",
        summary_metrics={},
        started_at=now,
        finished_at=now,
        created_at=now,
    )


def _patch(monkeypatch, counts, outcomes=None):
    """`counts` maps a symbol to its bar count; `outcomes` to what running it does."""
    outcomes = outcomes or {}
    ran = []
    monkeypatch.setattr(
        batch,
        "count_final_candles",
        lambda db, instrument_id, *_a: counts[ids[instrument_id]],
    )

    def fake_run(db, workspace_id, instrument, **kwargs):
        ran.append(instrument.symbol)
        outcome = outcomes.get(instrument.symbol, "ok")
        if isinstance(outcome, Exception):
            raise outcome
        return [_run()]

    monkeypatch.setattr(batch, "run_backtest_for_workspace", fake_run)
    return ran


ids: dict = {}


def _batch(monkeypatch, counts, outcomes=None, **kwargs):
    instruments = [_instrument(symbol) for symbol in counts]
    ids.clear()
    ids.update({instrument.id: instrument.symbol for instrument in instruments})
    ran = _patch(monkeypatch, counts, outcomes)
    db = MagicMock()
    params = dict(timeframe="4h", from_time=FROM, to_time=TO, initial_equity=Decimal(1_000_000))
    params.update(kwargs)
    return db, instruments, ran, params


def test_every_instrument_is_run_in_order_and_counted(monkeypatch) -> None:
    db, instruments, ran, params = _batch(monkeypatch, {"BTCUSDT": 6000, "ETHUSDT": 5000})

    items = batch.run_backtest_batch(db, uuid4(), instruments, **params)

    assert ran == ["BTCUSDT", "ETHUSDT"]
    assert [(i.instrument.symbol, i.bars, len(i.runs)) for i in items] == [
        ("BTCUSDT", 6000, 1),
        ("ETHUSDT", 5000, 1),
    ]
    assert all(i.error is None for i in items)


def test_a_batch_over_the_budget_is_refused_before_anything_runs(monkeypatch) -> None:
    db, instruments, ran, params = _batch(
        monkeypatch, {"BTCUSDT": 200_000, "ETHUSDT": 150_000, "XRPUSDT": 10}
    )

    with pytest.raises(batch.BatchTooLargeError) as error:
        batch.run_backtest_batch(db, uuid4(), instruments, **params)

    assert error.value.code == "batch_too_large"
    message = str(error.value)
    assert "合計350,010本" in message and "上限の300,000本" in message
    assert "BTCUSDT 200,000本" in message and "ETHUSDT 150,000本" in message
    assert ran == []  # nothing was run, nothing to clean up
    db.commit.assert_not_called()


def test_a_batch_exactly_at_the_budget_runs(monkeypatch) -> None:
    db, instruments, ran, params = _batch(monkeypatch, {"BTCUSDT": 100, "ETHUSDT": 200})

    batch.run_backtest_batch(db, uuid4(), instruments, max_bars=300, **params)

    assert ran == ["BTCUSDT", "ETHUSDT"]


def test_one_instrument_failing_does_not_stop_the_others(monkeypatch) -> None:
    db, instruments, ran, params = _batch(
        monkeypatch,
        {"BTCUSDT": 10, "ETHUSDT": 0, "XRPUSDT": 10},
        outcomes={"ETHUSDT": BacktestProvisioningError("no_candles", "No final candles")},
    )

    items = batch.run_backtest_batch(db, uuid4(), instruments, **params)

    assert ran == ["BTCUSDT", "ETHUSDT", "XRPUSDT"]
    by_symbol = {i.instrument.symbol: i for i in items}
    assert by_symbol["ETHUSDT"].runs == []
    assert (by_symbol["ETHUSDT"].error_code, by_symbol["ETHUSDT"].error) == (
        "no_candles",
        "No final candles",
    )
    assert len(by_symbol["BTCUSDT"].runs) == 1 and len(by_symbol["XRPUSDT"].runs) == 1
    db.rollback.assert_called_once()  # the failed one's partial work only


def test_a_walk_forward_error_is_reported_the_same_way(monkeypatch) -> None:
    db, instruments, _, params = _batch(
        monkeypatch,
        {"BTCUSDT": 10},
        outcomes={"BTCUSDT": WalkForwardError("too_few_candles", "need more")},
    )

    (item,) = batch.run_backtest_batch(db, uuid4(), instruments, walk_forward=True, **params)

    assert (item.error_code, item.error) == ("too_few_candles", "need more")


def test_an_unexpected_failure_keeps_only_the_exception_type(monkeypatch) -> None:
    db, instruments, ran, params = _batch(
        monkeypatch,
        {"BTCUSDT": 10, "ETHUSDT": 10},
        outcomes={"BTCUSDT": RuntimeError("password=hunter2 in a query")},
    )

    items = batch.run_backtest_batch(db, uuid4(), instruments, **params)

    assert ran == ["BTCUSDT", "ETHUSDT"]
    assert (items[0].error_code, items[0].error) == ("internal_error", "RuntimeError")
    assert "hunter2" not in str((items[0].error, items[0].error_code))
    assert len(items[1].runs) == 1


@pytest.mark.parametrize("count", [0, batch.MAX_BATCH_INSTRUMENTS + 1])
def test_the_number_of_instruments_is_bounded(monkeypatch, count: int) -> None:
    db, _, _, params = _batch(monkeypatch, {"BTCUSDT": 1})
    many = [_instrument(f"S{i}") for i in range(count)]

    with pytest.raises(BacktestProvisioningError) as error:
        batch.run_backtest_batch(db, uuid4(), many, **params)

    assert error.value.code == "invalid_batch"


def test_the_same_instrument_twice_is_refused(monkeypatch) -> None:
    db, instruments, _, params = _batch(monkeypatch, {"BTCUSDT": 1})

    with pytest.raises(BacktestProvisioningError) as error:
        batch.run_backtest_batch(db, uuid4(), [instruments[0], instruments[0]], **params)

    assert error.value.code == "invalid_batch"


# ---- the HTTP layer ----


def _override(session) -> None:
    app.dependency_overrides[get_db] = lambda: session
    app.dependency_overrides[require_viewer_role] = lambda: _USER
    app.dependency_overrides[require_operator_role] = lambda: _USER


def _body(*instrument_ids, **overrides):
    body = {
        "instrument_ids": [str(i) for i in instrument_ids],
        "timeframe": "4h",
        "from_time": "2024-01-01T00:00:00Z",
        "to_time": "2026-09-30T00:00:00Z",
        "initial_equity": "1000000",
    }
    body.update(overrides)
    return body


def _post(workspace_id, body):
    return client.post(f"/api/v1/workspaces/{workspace_id}/backtests/batch", json=body)


def test_the_endpoint_returns_one_item_per_instrument_in_request_order(monkeypatch) -> None:
    btc, eth = _instrument("BTCUSDT"), _instrument("ETHUSDT")
    session = MagicMock()
    session.get.side_effect = lambda _model, instrument_id: {btc.id: btc, eth.id: eth}[
        instrument_id
    ]
    _override(session)
    monkeypatch.setattr(routes, "_require_instrument_access", lambda *a: None)
    seen = {}

    def fake(db, workspace_id, instruments, **kwargs):
        seen.update(kwargs, symbols=[i.symbol for i in instruments])
        return [
            batch.BatchItem(btc, 6000, runs=[_run()]),
            batch.BatchItem(eth, 0, error_code="no_candles", error="No final candles"),
        ]

    monkeypatch.setattr(routes, "run_backtest_batch", fake)
    try:
        response = _post(uuid4(), _body(btc.id, eth.id, mode="walk_forward", spread="0.5"))
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 201
    body = response.json()
    assert body["total_bars"] == 6000
    assert [i["symbol"] for i in body["items"]] == ["BTCUSDT", "ETHUSDT"]
    assert len(body["items"][0]["runs"]) == 1 and body["items"][0]["error_code"] is None
    assert body["items"][1]["runs"] == [] and body["items"][1]["error_code"] == "no_candles"
    assert seen["symbols"] == ["BTCUSDT", "ETHUSDT"]
    assert seen["walk_forward"] is True and seen["spread"] == Decimal("0.5")


def test_an_over_budget_batch_is_a_422_that_says_why(monkeypatch) -> None:
    btc = _instrument("BTCUSDT")
    session = MagicMock()
    session.get.return_value = btc
    _override(session)
    monkeypatch.setattr(routes, "_require_instrument_access", lambda *a: None)
    monkeypatch.setattr(
        routes,
        "run_backtest_batch",
        MagicMock(
            side_effect=batch.BatchTooLargeError("対象の足が合計400,000本で、上限を超えています")
        ),
    )
    try:
        response = _post(uuid4(), _body(btc.id))
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 422
    assert "400,000本" in response.json()["detail"]


def test_an_instrument_the_workspace_cannot_use_stops_the_whole_batch(monkeypatch) -> None:
    from fastapi import HTTPException

    first, second = _instrument("BTCUSDT"), _instrument("ETHUSDT")
    session = MagicMock()
    _override(session)

    def access(db, workspace_id, instrument_id):
        if instrument_id == second.id:
            raise HTTPException(status_code=409, detail="Select an active account")

    monkeypatch.setattr(routes, "_require_instrument_access", access)
    run = MagicMock()
    monkeypatch.setattr(routes, "run_backtest_batch", run)
    try:
        response = _post(uuid4(), _body(first.id, second.id))
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 409
    run.assert_not_called()  # nothing runs for any instrument


def test_an_unknown_instrument_is_a_404(monkeypatch) -> None:
    session = MagicMock()
    session.get.return_value = None
    _override(session)
    monkeypatch.setattr(routes, "_require_instrument_access", lambda *a: None)
    try:
        response = _post(uuid4(), _body(uuid4()))
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 404


@pytest.mark.parametrize(
    "overrides",
    [
        {"instrument_ids": []},
        {"instrument_ids": [str(uuid4())] * 2},
        {"instrument_ids": [str(uuid4()) for _ in range(13)]},
        {"to_time": "2023-01-01T00:00:00Z"},
        {"initial_equity": "0"},
    ],
)
def test_the_request_is_validated(overrides: dict) -> None:
    _override(MagicMock())
    body = _body(uuid4())
    body.update(overrides)
    try:
        response = _post(uuid4(), body)
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 422


def test_the_endpoint_requires_the_operator_role() -> None:
    app.dependency_overrides[get_db] = lambda: MagicMock()
    app.dependency_overrides[require_viewer_role] = lambda: _USER
    try:
        response = _post(uuid4(), _body(uuid4()))
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 401
