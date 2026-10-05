"""Create (if needed) and start a paper-trading bot on public production prices
(docs/plans/paper-trading-live-data.md Unit 4): 4h Donchian(55/20) with a
stop-loss and no take-profit, with its own paper account funded with its share
of the capital, on the workspace's verified Binance Testnet connection (used
only for the startup credential check -- paper bots never send an order).

Idempotent: re-running finds the bot by name and does not create a second
account or deposit. Stored strategy/risk versions are never rewritten; a
mismatch stops with an error instead.

The trading worker (`python -m app.trading.worker`, also started by
`scripts/start_local.py`) must keep running for the bot to evaluate new bars
and refresh prices. Entries happen only on the bar a breakout occurs, so a
machine that is asleep at that bar misses the entry.

`--variant horizon-18d` creates the comparison group instead (4h Donchian
110/40, user decision 2026-10-05; see docs/plans/decision-timeframes.md), named
`<symbol>-4h-donchian-110-40`, with its own account and the same allocation, so
the two groups can be compared on equal terms.

Run: python scripts/provision_paper_bot.py --symbol BTCUSDT [--allocation 166667]
     [--variant approved|horizon-18d] [--workspace "Local Test Workspace"] [--no-start]
"""

import argparse
import asyncio
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from app.db.session import SessionLocal
from app.exchanges.binance_public import get_binance_public_client
from app.market_data.application.public_research import (
    find_public_research_instrument,
    refresh_public_klines,
)
from app.models.connections import Exchange, ExchangeConnection
from app.models.workspace import Workspace
from app.trading.application import bot_lifecycle
from app.trading.application.paper_provisioning import (
    APPROVED_RISK_PROFILE_NAME,
    APPROVED_RISK_RULES,
    APPROVED_STRATEGY_DEFINITION,
    APPROVED_STRATEGY_NAME,
    APPROVED_TIMEFRAME,
    COMPARISON_STRATEGY_DEFINITION,
    COMPARISON_STRATEGY_NAME,
    PaperBotSpec,
    ProvisioningError,
    provision_paper_bot,
)
from app.trading.application.public_price_refresh import INITIAL_BARS
from sqlalchemy import select

VARIANTS: dict[str, tuple[str, dict[str, Any], str]] = {
    "approved": (APPROVED_STRATEGY_NAME, APPROVED_STRATEGY_DEFINITION, ""),
    "horizon-18d": (COMPARISON_STRATEGY_NAME, COMPARISON_STRATEGY_DEFINITION, "-110-40"),
}
"""--variant -> (strategy name, definition, bot name suffix)."""


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--symbol", required=True)
    parser.add_argument("--allocation", type=Decimal, default=Decimal(166_667))
    parser.add_argument("--workspace", default="Local Test Workspace")
    parser.add_argument("--no-start", action="store_true")
    parser.add_argument("--variant", choices=sorted(VARIANTS), default="approved")
    args = parser.parse_args()

    with SessionLocal() as db:
        workspace = db.scalar(select(Workspace).where(Workspace.name == args.workspace))
        if workspace is None:
            print(f"[NG] workspace '{args.workspace}' not found")
            return 1
        connections = db.scalars(
            select(ExchangeConnection)
            .join(Exchange, Exchange.id == ExchangeConnection.exchange_id)
            .where(
                ExchangeConnection.workspace_id == workspace.id,
                Exchange.code == "binance",
                ExchangeConnection.status == "verified",
            )
        ).all()
        if len(connections) != 1:
            print(f"[NG] expected one verified Binance connection, found {len(connections)}")
            return 1
        strategy_name, strategy_definition, bot_suffix = VARIANTS[args.variant]
        instrument = find_public_research_instrument(db, args.symbol)
        if instrument is None:
            print(f"[NG] no public price instrument for {args.symbol}; run the fetch script first")
            return 1

        spec = PaperBotSpec(
            workspace_id=workspace.id,
            connection_id=connections[0].id,
            instrument_id=instrument.id,
            quote_asset=instrument.quote_asset,
            bot_name=f"{args.symbol.lower()}-{APPROVED_TIMEFRAME}-donchian{bot_suffix}",
            timeframe=APPROVED_TIMEFRAME,
            allocation=args.allocation,
            strategy_name=strategy_name,
            strategy_definition=strategy_definition,
            risk_profile_name=APPROVED_RISK_PROFILE_NAME,
            risk_rules=APPROVED_RISK_RULES,
        )
        try:
            bot = provision_paper_bot(db, spec)
        except ProvisioningError as exc:
            print(f"[NG] {exc.code}: {exc}")
            return 1
        print(f"[OK] bot {bot.name} (id={bot.id}, account={bot.account_id}, {bot.actual_state})")

        if args.no_start or bot.desired_state != "stopped":
            return 0
        # The worker refreshes prices only for running bots, but starting requires
        # fresh prices -- so bring this bot's series up to date first.
        refreshed = asyncio.run(
            refresh_public_klines(
                db,
                get_binance_public_client(),
                instrument,
                APPROVED_TIMEFRAME,
                initial_bars=INITIAL_BARS,
                now=datetime.now(UTC),
            )
        )
        print(f"[OK] refreshed {args.symbol} {APPROVED_TIMEFRAME}: {refreshed} new bar(s)")
        try:
            bot_run = bot_lifecycle.start_bot(db, bot)
        except bot_lifecycle.BotLifecycleError as exc:
            print(f"[NG] start refused: {exc.code}: {exc}")
            return 1
        print(f"[OK] started (bot_run={bot_run.id})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
