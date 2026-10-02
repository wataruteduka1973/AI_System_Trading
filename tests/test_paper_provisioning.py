"""`app/trading/application/paper_provisioning.py` (docs/plans/paper-trading-live-data.md
Unit 4): idempotent creation of a paper bot with its own funded account, strategy
version and risk profile version. Stored versions are immutable, so a stored
definition that differs from the requested one is refused, not overwritten."""

from decimal import Decimal
from unittest.mock import MagicMock
from uuid import uuid4

import pytest
from app.models.strategy import (
    RiskProfile,
    RiskProfileVersion,
    Strategy,
    StrategyVersion,
    TradingBot,
)
from app.models.trading import LedgerEntry, TradingAccount
from app.trading.application import paper_provisioning as pp

DEFINITION = {
    "kind": "donchian_breakout",
    "entry_period": 55,
    "exit_period": 20,
    "exit_policy": "stop_loss",
}
RULES = {"risk_per_trade": "0.005"}


def _spec(**overrides: object) -> pp.PaperBotSpec:
    values: dict[str, object] = dict(
        workspace_id=uuid4(),
        connection_id=uuid4(),
        instrument_id=uuid4(),
        quote_asset="USDT",
        bot_name="btcusdt-4h-donchian",
        timeframe="4h",
        allocation=Decimal("166667"),
        strategy_name="donchian-55-20-stop-loss",
        strategy_definition=DEFINITION,
        risk_profile_name="conservative-v1",
        risk_rules=RULES,
    )
    values.update(overrides)
    return pp.PaperBotSpec(**values)  # type: ignore[arg-type]


def _added(db: MagicMock, model: type) -> list:
    return [c.args[0] for c in db.add.call_args_list if isinstance(c.args[0], model)]


def test_a_new_bot_gets_its_own_funded_account_strategy_and_risk_profile() -> None:
    db = MagicMock()
    # bot, strategy, risk profile: nothing exists yet.
    db.scalar.side_effect = [None, None, None]
    spec = _spec()

    bot = pp.provision_paper_bot(db, spec)

    [account] = _added(db, TradingAccount)
    assert (account.mode, account.base_currency, account.connection_id) == (
        "paper", "USDT", spec.connection_id,
    )  # fmt: skip
    [deposit] = [e for e in _added(db, LedgerEntry) if e.entry_type == "deposit"]
    assert (deposit.amount, deposit.asset) == (Decimal("166667"), "USDT")
    [version] = _added(db, StrategyVersion)
    assert version.definition == DEFINITION
    assert version.lifecycle_status == "paper_approved"
    [risk_version] = _added(db, RiskProfileVersion)
    assert (risk_version.rules, risk_version.status) == (RULES, "approved")
    assert isinstance(bot, TradingBot)
    assert (bot.name, bot.execution_mode, bot.timeframe, bot.instrument_id) == (
        "btcusdt-4h-donchian", "paper", "4h", spec.instrument_id,
    )  # fmt: skip
    assert bot.actual_state != "running"  # starting is a separate, explicit step
    db.commit.assert_called_once()


def test_an_existing_bot_is_returned_without_a_new_account_or_deposit() -> None:
    db = MagicMock()
    existing = TradingBot(id=uuid4(), name="btcusdt-4h-donchian", account_id=uuid4())
    db.scalar.side_effect = [existing]

    assert pp.provision_paper_bot(db, _spec()) is existing
    assert not _added(db, TradingAccount)
    assert not _added(db, LedgerEntry)


def test_a_stored_strategy_version_with_a_different_definition_is_refused() -> None:
    db = MagicMock()
    strategy = Strategy(id=uuid4(), name="donchian-55-20-stop-loss")
    stored = StrategyVersion(id=uuid4(), version=1, definition={**DEFINITION, "entry_period": 20})
    db.scalar.side_effect = [None, strategy, stored]

    with pytest.raises(pp.ProvisioningError) as exc:
        pp.provision_paper_bot(db, _spec())
    assert exc.value.code == "strategy_definition_mismatch"
    db.commit.assert_not_called()


def test_a_stored_risk_profile_version_with_different_rules_is_refused() -> None:
    db = MagicMock()
    strategy = Strategy(id=uuid4(), name="donchian-55-20-stop-loss")
    stored = StrategyVersion(id=uuid4(), version=1, definition=DEFINITION)
    profile = RiskProfile(id=uuid4(), name="conservative-v1")
    stored_rules = RiskProfileVersion(id=uuid4(), version=1, rules={"risk_per_trade": "0.02"})
    db.scalar.side_effect = [None, strategy, stored, profile, stored_rules]

    with pytest.raises(pp.ProvisioningError) as exc:
        pp.provision_paper_bot(db, _spec())
    assert exc.value.code == "risk_rules_mismatch"
    db.commit.assert_not_called()


def test_a_non_positive_allocation_is_refused() -> None:
    with pytest.raises(pp.ProvisioningError):
        pp.provision_paper_bot(MagicMock(), _spec(allocation=Decimal(0)))
