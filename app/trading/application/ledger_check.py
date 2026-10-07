"""The periodic ledger reconciliation and its halt (取引停止マトリクス「注文・台帳不整合」,
docs/plans/ledger-reconciliation.md).

`ledger_reconciliation.reconcile_account` says whether an account's records agree. This runs it for
every account that has traded and, when they do not, stops the account: an `emergency_stopped`
`trading_halt` in the account scope (no new or increasing entry; announced to the workspace's Owners
and Operators), not auto-releasable. The matrix releases it after "照合完了＋Owner承認": the
Owner's `/emergency-release` runs the reconciliation again and refuses while it still finds
something (`api/routes/trading_halts.py`).

It runs in the notification worker, a different process from the one that writes the fills, so the
check does not share a failure with what it checks. An account that is already halted for this
reason is not looked at again; the release is what re-checks it.
"""

from uuid import UUID

import structlog
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.strategy import TradingHalt
from app.models.trading import TradeOrder, TradingAccount
from app.trading.application import ledger_reconciliation, trading_halt

logger = structlog.get_logger(__name__)

REASON_CODE = "ledger_mismatch"
_PAYLOAD_FINDINGS = 10


def findings_summary(findings: list[ledger_reconciliation.Finding], limit: int = 3) -> str:
    shown = "、".join(finding.message for finding in findings[:limit])
    more = f"(ほか{len(findings) - limit}件)" if len(findings) > limit else ""
    return f"{shown}{more}"


def _accounts_to_check(db: Session) -> list[tuple[UUID, UUID]]:
    """`(workspace_id, account_id)` of each account that has an order and no active halt for
    this reason yet."""
    halted = select(TradingHalt.scope_id).where(
        TradingHalt.reason_code == REASON_CODE,
        TradingHalt.scope_type == "account",
        TradingHalt.status == "active",
    )
    rows = db.execute(
        select(TradingAccount.workspace_id, TradingAccount.id)
        .where(
            TradingAccount.id.in_(select(TradeOrder.account_id)),
            TradingAccount.id.not_in(halted),
        )
        .order_by(TradingAccount.created_at)
    ).all()
    return [(row[0], row[1]) for row in rows]


def check_ledger_reconciliation(db: Session) -> int:
    """Reconciles each account and halts the ones that disagree, one transaction per account so
    that one failing does not stop the rest. Returns how many accounts were halted."""
    halted = 0
    for workspace_id, account_id in _accounts_to_check(db):
        try:
            findings = ledger_reconciliation.reconcile_account(db, account_id)
            if not findings:
                db.rollback()
                continue
            trading_halt.activate_or_escalate(
                db,
                trading_halt.HaltScope(workspace_id, "account", account_id),
                reason_code=REASON_CODE,
                level="emergency_stopped",
                auto_releasable=False,
                event=trading_halt.HaltEvent(
                    event_type=f"trading_halt.{REASON_CODE}",
                    message=(
                        f"注文と台帳の不整合を検知したため、口座の取引を止めました"
                        f"({findings_summary(findings)})。照合して直したうえで、Ownerが解除してください"
                    ),
                    payload={
                        "account_id": str(account_id),
                        "finding_count": len(findings),
                        "findings": [
                            {"code": f.code, "message": f.message, **f.detail}
                            for f in findings[:_PAYLOAD_FINDINGS]
                        ],
                    },
                    source_type="reconciliation",
                ),
            )
            db.commit()
            halted += 1
        except Exception as exc:
            db.rollback()
            logger.warning(
                "ledger_reconciliation_failed",
                account_id=str(account_id),
                error_type=type(exc).__name__,
            )
    return halted
