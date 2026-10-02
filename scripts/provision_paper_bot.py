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

Run: python scripts/provision_paper_bot.py --symbol BTCUSDT [--allocation 166667]
     [--workspace "Local Test Workspace"] [--no-start]
"""

import argparse
import asyncio
from datetime import UTC, datetime
from decimal import Decimal

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
    PaperBotSpec,
    ProvisioningError,
    provision_paper_bot,
)
from app.trading.application.public_price_refresh import INITIAL_BARS
from app.trading.application.risk_gate import CONSERVATIVE_V1_RULES
from sqlalchemy import select

TIMEFRAME = "4h"
STRATEGY_NAME = "donchian-55-20-stop-loss"
STRATEGY_DEFINITION = {
    "kind": "donchian_breakout",
    "entry_period": 55,
    "exit_period": 20,
    "exit_policy": "stop_loss",
}
RISK_PROFILE_NAME = "conservative-v1"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--symbol", required=True)
    parser.add_argument("--allocation", type=Decimal, default=Decimal(166_667))
    parser.add_argument("--workspace", default="Local Test Workspace")
    parser.add_argument("--no-start", action="store_true")
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
        instrument = find_public_research_instrument(db, args.symbol)
        if instrument is None:
            print(f"[NG] no public price instrument for {args.symbol}; run the fetch script first")
            return 1

        spec = PaperBotSpec(
            workspace_id=workspace.id,
            connection_id=connections[0].id,
            instrument_id=instrument.id,
            quote_asset=instrument.quote_asset,
            bot_name=f"{args.symbol.lower()}-{TIMEFRAME}-donchian",
            timeframe=TIMEFRAME,
            allocation=args.allocation,
            strategy_name=STRATEGY_NAME,
            strategy_definition=STRATEGY_DEFINITION,
            risk_profile_name=RISK_PROFILE_NAME,
            risk_rules=CONSERVATIVE_V1_RULES,
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
                TIMEFRAME,
                initial_bars=INITIAL_BARS,
                now=datetime.now(UTC),
            )
        )
        print(f"[OK] refreshed {args.symbol} {TIMEFRAME}: {refreshed} new bar(s)")
        try:
            bot_run = bot_lifecycle.start_bot(db, bot)
        except bot_lifecycle.BotLifecycleError as exc:
            print(f"[NG] start refused: {exc.code}: {exc}")
            return 1
        print(f"[OK] started (bot_run={bot_run.id})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
