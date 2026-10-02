"""Creates a paper-trading bot together with its own funded paper account, its
strategy version and its risk profile version (docs/plans/paper-trading-live-data.md
Unit 4). Used by `scripts/provision_paper_bot.py`.

**Idempotent by bot name**: a bot that already exists in the workspace is
returned as is -- no second account, no second deposit -- so the script can be
re-run safely. The account has no name of its own; the bot that owns it is
what identifies it.

**Stored versions are never rewritten**: a strategy version or risk profile
version already stored under the requested name must match the requested
definition/rules exactly, or provisioning stops with `ProvisioningError`. A
version is what a bot's past signals and decisions were made under; silently
changing it would make that record lie. A different definition needs a new
name (or a new version, which this module does not create).

**One account per bot**: each bot's equity, risk budget and drawdown locks are
computed from its own account, so splitting capital across assets
(1/6 each, see the plan) means one account per asset.
"""

import hashlib
from dataclasses import dataclass
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.strategy import (
    RiskProfile,
    RiskProfileVersion,
    Strategy,
    StrategyVersion,
    TradingBot,
)
from app.models.trading import TradingAccount
from app.trading.application.account_funding import seed_paper_deposit


class ProvisioningError(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class PaperBotSpec:
    workspace_id: UUID
    connection_id: UUID
    instrument_id: UUID
    quote_asset: str
    bot_name: str
    timeframe: str
    allocation: Decimal
    """Paper capital deposited into the bot's new account, in `quote_asset`."""
    strategy_name: str
    strategy_definition: dict[str, Any]
    risk_profile_name: str
    risk_rules: dict[str, Any]


def _checksum(payload: object) -> str:
    return hashlib.sha256(repr(payload).encode()).hexdigest()


def _ensure_strategy_version(
    db: Session, workspace_id: UUID, name: str, definition: dict[str, Any]
) -> StrategyVersion:
    strategy = db.scalar(
        select(Strategy).where(Strategy.workspace_id == workspace_id, Strategy.name == name)
    )
    if strategy is not None:
        stored = db.scalar(
            select(StrategyVersion).where(
                StrategyVersion.strategy_id == strategy.id, StrategyVersion.version == 1
            )
        )
        if stored is not None:
            if stored.definition != definition:
                raise ProvisioningError(
                    "strategy_definition_mismatch",
                    f"strategy '{name}' v1 is stored with a different definition",
                )
            return stored
    else:
        strategy = Strategy(workspace_id=workspace_id, name=name, mode="technical")
        db.add(strategy)
        db.flush()
    version = StrategyVersion(
        strategy_id=strategy.id,
        version=1,
        supported_market_types=["crypto"],
        definition=definition,
        checksum=_checksum(definition),
        # Approved for paper by the user on 2026-10-02 (the plan's Unit 4 spec).
        lifecycle_status="paper_approved",
    )
    db.add(version)
    db.flush()
    return version


def _ensure_risk_profile_version(
    db: Session, workspace_id: UUID, name: str, rules: dict[str, Any]
) -> RiskProfileVersion:
    profile = db.scalar(
        select(RiskProfile).where(
            RiskProfile.workspace_id == workspace_id, RiskProfile.name == name
        )
    )
    if profile is not None:
        stored = db.scalar(
            select(RiskProfileVersion).where(
                RiskProfileVersion.risk_profile_id == profile.id, RiskProfileVersion.version == 1
            )
        )
        if stored is not None:
            if stored.rules != rules:
                raise ProvisioningError(
                    "risk_rules_mismatch",
                    f"risk profile '{name}' v1 is stored with different rules",
                )
            return stored
    else:
        profile = RiskProfile(workspace_id=workspace_id, name=name)
        db.add(profile)
        db.flush()
    version = RiskProfileVersion(
        risk_profile_id=profile.id,
        version=1,
        rules=rules,
        checksum=_checksum(rules),
        status="approved",
    )
    db.add(version)
    db.flush()
    return version


def provision_paper_bot(db: Session, spec: PaperBotSpec) -> TradingBot:
    """Returns the bot, creating it and everything it needs if it does not exist.
    The bot is left stopped; starting it is `bot_lifecycle.start_bot`'s job.
    Commits once, at the end -- nothing is stored if any check fails."""
    if spec.allocation <= 0:
        raise ProvisioningError("invalid_allocation", "allocation must be greater than 0")

    existing = db.scalar(
        select(TradingBot).where(
            TradingBot.workspace_id == spec.workspace_id, TradingBot.name == spec.bot_name
        )
    )
    if existing is not None:
        return existing

    strategy_version = _ensure_strategy_version(
        db, spec.workspace_id, spec.strategy_name, spec.strategy_definition
    )
    risk_profile_version = _ensure_risk_profile_version(
        db, spec.workspace_id, spec.risk_profile_name, spec.risk_rules
    )

    account = TradingAccount(
        workspace_id=spec.workspace_id,
        connection_id=spec.connection_id,
        external_account_id=None,
        mode="paper",
        base_currency=spec.quote_asset,
    )
    db.add(account)
    db.flush()
    seed_paper_deposit(
        db,
        account,
        amount=spec.allocation,
        asset=spec.quote_asset,
        note=f"paper allocation for bot {spec.bot_name}",
    )

    bot = TradingBot(
        workspace_id=spec.workspace_id,
        name=spec.bot_name,
        execution_mode="paper",
        strategy_mode="technical",
        connection_id=spec.connection_id,
        account_id=account.id,
        instrument_id=spec.instrument_id,
        timeframe=spec.timeframe,
        strategy_version_id=strategy_version.id,
        risk_profile_version_id=risk_profile_version.id,
    )
    db.add(bot)
    db.commit()
    db.refresh(bot)
    return bot
