"""Tells people when market-data collection stops by itself (docs/plans/notification-sources.md).

Called from the two places where the worker gives up on a target and commits to it in the same
transaction (`pages.PageStore.fail` and `leases.LeaseStore.recover_expired`): a subscription that
becomes `blocked` (live collection of that instrument and time frame has stopped, and bots that
read it will hit the data-delay halt) or a backfill job that becomes `failed`. A retry that will
be tried again is not announced.

Seven time frames of one instrument usually fail together (an authentication failure hits all of
them), so an announcement is skipped when the same workspace, instrument and cause were announced
within `_QUIET_PERIOD`; the first message names the time frame that failed first.

Payload: identifiers, the cause's code and counts. Never the exception message.
"""

from datetime import timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.market_data.infrastructure.models import WorkerBackfill, WorkerSubscription
from app.models.audit import SystemEvent
from app.models.instruments import Instrument
from app.notifications.application.publish_event import publish_system_event

EVENT_TYPE = "market_data_stopped"
_QUIET_PERIOD = timedelta(hours=1)

_CAUSE_LABEL = {
    "authentication_failed": "取引所の認証に失敗",
    "credentials_missing": "資格情報が不足",
    "credentials_unreadable": "保存済みの資格情報を読めない",
    "access_unavailable": "有効な接続または選択口座が無い",
    "access_changed": "取得中に接続または口座の設定が変わった",
    "communication_failed": "取引所との通信に失敗",
    "rate_limited": "取引所のレート制限",
    "invalid_candles": "取引所から不正なローソク足を受信",
    "worker_interrupted": "取得処理が繰り返し中断",
}


def announce_stopped(
    db: Session, target: WorkerSubscription | WorkerBackfill, *, error_code: str
) -> None:
    """Records the event in the caller's transaction (nothing is committed here)."""
    instrument = db.get(Instrument, target.instrument_id)
    symbol = instrument.symbol if instrument else str(target.instrument_id)
    already_told = db.scalar(
        select(func.count())
        .select_from(SystemEvent)
        .where(
            SystemEvent.workspace_id == target.workspace_id,
            SystemEvent.event_type == EVENT_TYPE,
            SystemEvent.target_id == target.instrument_id,
            SystemEvent.reason_code == error_code,
            SystemEvent.occurred_at > func.now() - _QUIET_PERIOD,
        )
    )
    if already_told:
        return
    is_backfill = isinstance(target, WorkerBackfill)
    what = "過去データの取得" if is_backfill else "ローソク足の自動取得"
    cause = _CAUSE_LABEL.get(error_code, error_code)
    publish_system_event(
        db,
        workspace_id=target.workspace_id,
        severity="warning" if is_backfill else "error",
        category="market_data",
        event_type=EVENT_TYPE,
        reason_code=error_code,
        message=f"{symbol} {target.timeframe} の{what}が停止しました(原因: {cause})",
        payload={
            "kind": "backfill" if is_backfill else "subscription",
            "symbol": symbol,
            "instrument_id": str(target.instrument_id),
            "timeframe": target.timeframe,
            "error_code": error_code,
            "consecutive_failures": target.consecutive_failures,
        },
        aggregate_type="backfill_job" if is_backfill else "market_data_subscription",
        aggregate_id=target.id,
        source_type="market_data_worker",
        target_type="instrument",
        target_id=target.instrument_id,
    )
