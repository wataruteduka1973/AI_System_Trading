"""Standalone soak-test monitor for the realtime market-data WS stream (RT-14).

See docs/plans/realtime-market-data-stream.md (work unit 8, RT-14) and
docs/design/modules/realtime-market-data-stream.md sections 4/5/7 for the
wire protocol this client speaks.

This script is deliberately independent of the app package: it drives the
already-running API process (started separately, e.g. via start-local.bat)
purely over HTTP/WebSocket, the same way a browser tab would. It never opens
a DB connection and never reads secrets other than the owner token it is
given on the command line or via the AIST_OWNER_TOKEN environment variable.

What it measures:

- Connection health: reconnect count, close codes/reasons, resume outcome
  ("fresh"/"replayed"/"gap_fill_required") reported by each stream_state.
- Event traffic: counts by event_type, including gap_notice occurrences.
- Silence: the longest gap between two received messages, as a proxy for
  "the feed went quiet without telling us".

What it deliberately does NOT measure: the API process's own memory usage.
A client-side script has no reliable, portable way to read another
process's memory, so that half of RT-14 ("memory growth ... in acceptable
range") has to be checked separately -- see the companion instructions for
a one-line PowerShell command to sample it a few times over the run.

Usage:

    python scripts/stream_soak_monitor.py \\
        --base-url http://127.0.0.1:8000 \\
        --workspace-id <uuid> \\
        --instrument-id <uuid> \\
        --timeframe 1m \\
        --hours 24 \\
        --out soak_test_log.jsonl

The owner token is read from AIST_OWNER_TOKEN unless --owner-token is
passed explicitly. It is never written to the log file or printed.
"""

import argparse
import asyncio
import contextlib
import json
import os
import random
import sys
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import websockets
from websockets.exceptions import ConnectionClosed

# Non-retryable per docs/design/modules/realtime-market-data-stream.md
# section 7: the ticket itself was rejected or access was denied, so
# reconnecting with a fresh ticket against the same inputs would just fail
# again in the same way.
NON_RETRYABLE_CLOSE_CODES = {4401, 4403}

HEARTBEAT_INTERVAL_SECONDS = 60.0
TICKET_REQUEST_TIMEOUT_SECONDS = 10.0

INITIAL_RECONNECT_BACKOFF_SECONDS = 1.0
MAX_RECONNECT_BACKOFF_SECONDS = 60.0
_BACKOFF_MULTIPLIER = 2.0
"""Deliberately duplicated (not imported) from
app.market_data.infrastructure.binance_stream.compute_reconnect_backoff_seconds
-- this script is intentionally independent of the app package (see module
docstring). The 2026-09-19 soak-test incident's log (soak_test_log.jsonl)
showed this script itself retrying a refused connection every ~5s with no
backoff for over 12 minutes once the API process went down; this closes
that gap in the test tool, matching the app-side fix."""


def _compute_reconnect_backoff_seconds(attempt: int, *, rng: random.Random) -> float:
    base = min(
        INITIAL_RECONNECT_BACKOFF_SECONDS * (_BACKOFF_MULTIPLIER ** (attempt - 1)),
        MAX_RECONNECT_BACKOFF_SECONDS,
    )
    return rng.uniform(base / 2, base)


@dataclass
class SoakStats:
    started_at: datetime
    event_counts: dict[str, int] = field(default_factory=dict)
    resume_outcomes: dict[str, int] = field(default_factory=dict)
    reconnect_count: int = 0
    gap_notice_count: int = 0
    last_message_at: datetime | None = None
    longest_silence_seconds: float = 0.0

    def record_event(self, event_type: str) -> None:
        self.event_counts[event_type] = self.event_counts.get(event_type, 0) + 1
        if event_type == "gap_notice":
            self.gap_notice_count += 1
        self._mark_message_received()

    def record_resume_outcome(self, resume: str) -> None:
        self.resume_outcomes[resume] = self.resume_outcomes.get(resume, 0) + 1
        self._mark_message_received()

    def _mark_message_received(self) -> None:
        now = datetime.now(UTC)
        if self.last_message_at is not None:
            silence = (now - self.last_message_at).total_seconds()
            if silence > self.longest_silence_seconds:
                self.longest_silence_seconds = silence
        self.last_message_at = now

    def snapshot(self) -> dict[str, Any]:
        elapsed = (datetime.now(UTC) - self.started_at).total_seconds()
        return {
            "elapsed_seconds": round(elapsed, 1),
            "reconnect_count": self.reconnect_count,
            "event_counts": dict(self.event_counts),
            "resume_outcomes": dict(self.resume_outcomes),
            "gap_notice_count": self.gap_notice_count,
            "longest_silence_seconds": round(self.longest_silence_seconds, 1),
        }


def append_log(path: Path, record: dict[str, Any]) -> None:
    line = json.dumps({"logged_at": datetime.now(UTC).isoformat(), **record}, ensure_ascii=False)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(line + "\n")
    print(line)


def issue_ticket(
    base_url: str, owner_token: str, workspace_id: str, instrument_id: str, timeframe: str
) -> dict[str, Any]:
    url = f"{base_url}/api/v1/workspaces/{workspace_id}/market-stream-tickets"
    body = json.dumps({"instrument_id": instrument_id, "timeframe": timeframe}).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=body,
        method="POST",
        headers={"Content-Type": "application/json", "X-Owner-Token": owner_token},
    )
    with urllib.request.urlopen(request, timeout=TICKET_REQUEST_TIMEOUT_SECONDS) as response:
        payload: dict[str, Any] = json.loads(response.read().decode("utf-8"))
        return payload


def build_ws_url(
    base_url: str,
    ticket: str,
    resume_last_sequence: int | None,
    resume_feed_started_at: str | None,
) -> str:
    scheme, _, rest = base_url.partition("://")
    ws_scheme = "wss" if scheme == "https" else "ws"
    params = {"ticket": ticket}
    if resume_last_sequence is not None and resume_feed_started_at is not None:
        params["resume_last_sequence"] = str(resume_last_sequence)
        params["resume_feed_started_at"] = resume_feed_started_at
    return f"{ws_scheme}://{rest}/ws/v1/market-stream?{urllib.parse.urlencode(params)}"


async def heartbeat_loop(stats: SoakStats, log_path: Path, deadline: datetime) -> None:
    while datetime.now(UTC) < deadline:
        await asyncio.sleep(HEARTBEAT_INTERVAL_SECONDS)
        append_log(log_path, {"kind": "heartbeat", **stats.snapshot()})


async def run_connection(
    base_url: str,
    owner_token: str,
    workspace_id: str,
    instrument_id: str,
    timeframe: str,
    stats: SoakStats,
    log_path: Path,
    resume_last_sequence: int | None,
    resume_feed_started_at: str | None,
) -> tuple[int | None, str | None, bool]:
    """Run a single ticket + WS connection until it closes. Returns the
    (last_sequence, feed_started_at) resume state to try on the next
    connection, plus whether any message was received on this connection
    (used by the caller to decide whether to reset the reconnect backoff)."""
    ticket_info = issue_ticket(base_url, owner_token, workspace_id, instrument_id, timeframe)
    url = build_ws_url(
        base_url, ticket_info["ticket"], resume_last_sequence, resume_feed_started_at
    )

    last_sequence = resume_last_sequence
    feed_started_at = resume_feed_started_at
    received_any = False

    async with websockets.connect(url) as connection:
        async for raw_message in connection:
            received_any = True
            message: dict[str, Any] = json.loads(raw_message)
            if message.get("type") == "stream_state":
                feed_started_at = message["feed_started_at"]
                stats.record_resume_outcome(message.get("resume", "unknown"))
                append_log(log_path, {"kind": "stream_state", "message": message})
                continue
            event_type = message.get("event_type", "unknown")
            stats.record_event(event_type)
            if "sequence" in message:
                last_sequence = message["sequence"]
            if event_type == "gap_notice":
                append_log(log_path, {"kind": "gap_notice", "message": message})

    return last_sequence, feed_started_at, received_any


async def soak(args: argparse.Namespace) -> None:
    log_path = Path(args.out)
    stats = SoakStats(started_at=datetime.now(UTC))
    deadline = stats.started_at + timedelta(hours=args.hours)
    append_log(
        log_path,
        {
            "kind": "start",
            "workspace_id": args.workspace_id,
            "instrument_id": args.instrument_id,
            "timeframe": args.timeframe,
            "hours": args.hours,
        },
    )

    heartbeat_task = asyncio.create_task(heartbeat_loop(stats, log_path, deadline))
    resume_last_sequence: int | None = None
    resume_feed_started_at: str | None = None
    rng = random.Random()
    attempt = 0

    try:
        while datetime.now(UTC) < deadline:
            received_any = False
            try:
                resume_last_sequence, resume_feed_started_at, received_any = await run_connection(
                    args.base_url,
                    args.owner_token,
                    args.workspace_id,
                    args.instrument_id,
                    args.timeframe,
                    stats,
                    log_path,
                    resume_last_sequence,
                    resume_feed_started_at,
                )
                append_log(log_path, {"kind": "disconnected", "reason": "connection_closed"})
            except ConnectionClosed as exc:
                # exc.code/.reason are deprecated since websockets 13.1; the close
                # frame the server actually sent (if any) lives on exc.rcvd instead.
                close_code = exc.rcvd.code if exc.rcvd is not None else None
                close_reason = exc.rcvd.reason if exc.rcvd is not None else ""
                append_log(
                    log_path,
                    {"kind": "disconnected", "close_code": close_code, "reason": close_reason},
                )
                if close_code in NON_RETRYABLE_CLOSE_CODES:
                    append_log(
                        log_path,
                        {"kind": "stopping", "reason": "non_retryable_close_code"},
                    )
                    return
            except (OSError, urllib.error.URLError) as exc:
                append_log(log_path, {"kind": "connection_error", "error": str(exc)})

            stats.reconnect_count += 1
            if datetime.now(UTC) >= deadline:
                break
            if received_any:
                attempt = 0
            attempt += 1
            delay = _compute_reconnect_backoff_seconds(attempt, rng=rng)
            append_log(
                log_path,
                {"kind": "reconnect_scheduled", "attempt": attempt, "backoff_seconds": delay},
            )
            await asyncio.sleep(delay)
    finally:
        heartbeat_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await heartbeat_task
        append_log(log_path, {"kind": "finished", **stats.snapshot()})


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--owner-token", default=os.environ.get("AIST_OWNER_TOKEN"))
    parser.add_argument("--workspace-id", required=True)
    parser.add_argument("--instrument-id", required=True)
    parser.add_argument("--timeframe", default="1m")
    parser.add_argument("--hours", type=float, default=24.0)
    parser.add_argument("--out", default="soak_test_log.jsonl")
    args = parser.parse_args(argv)
    if not args.owner_token:
        parser.error("--owner-token or AIST_OWNER_TOKEN must be set")
    return args


def main() -> None:
    args = parse_args(sys.argv[1:])
    asyncio.run(soak(args))


if __name__ == "__main__":
    main()
