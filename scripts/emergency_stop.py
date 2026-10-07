"""Emergency stop without the API (docs/concept/FXtrading_rebuild/05 §5: the stop must be
possible while the API is down). Does what `POST .../emergency-stop` does, straight on the
database: activates the `emergency_stopped` halt, stops the bots, optionally closes the
positions at market. An Owner releases the halt afterwards through the API
(`/trading-halts/{id}/emergency-release`).

Run: python scripts/emergency_stop.py --workspace-id <uuid> [--bot-id <uuid>]
     [--close-positions] [--reason "text"]
Without --bot-id the whole workspace is stopped.
"""

import argparse
from uuid import UUID

from app.db.session import SessionLocal
from app.models.strategy import TradingBot
from app.trading.application import emergency_stop


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace-id", type=UUID, required=True)
    parser.add_argument("--bot-id", type=UUID, default=None)
    parser.add_argument("--close-positions", action="store_true")
    parser.add_argument("--reason", default="operator script")
    args = parser.parse_args()
    with SessionLocal() as db:
        if args.bot_id is None:
            result = emergency_stop.emergency_stop_workspace(
                db,
                args.workspace_id,
                requested_by=None,
                close_positions=args.close_positions,
                reason=args.reason,
            )
        else:
            bot = db.get(TradingBot, args.bot_id)
            if bot is None or bot.workspace_id != args.workspace_id:
                parser.error("bot not found in this workspace")
            result = emergency_stop.emergency_stop_bot(
                db,
                bot,
                requested_by=None,
                close_positions=args.close_positions,
                reason=args.reason,
            )
        halt_id, scope_type = result.halt.id, result.halt.scope_type  # while the session is open
    state = "already active" if result.already_active else "activated"
    print(f"[OK] emergency stop {state}: halt {halt_id} ({scope_type})")
    print(f"     stopped bots: {len(result.stopped_bot_ids)}")
    for failure in result.bot_stop_failures:
        print(f"[WARN] bot {failure['bot_id']} could not be stopped: {failure['code']}")
    if args.close_positions:
        print(f"     closing orders: {len(result.closing_order_ids)}")
        for failure in result.close_failures:
            print(f"[WARN] position {failure['position_id']} not closed: {failure['code']}")
    return 1 if result.bot_stop_failures or result.close_failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
