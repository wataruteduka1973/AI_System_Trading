import asyncio
from uuid import uuid4

from app.market_data.infrastructure.leases import FeedKey, LeaseClaim, WorkRef
from app.market_data.worker.runner import WorkerRunner


def work(kind="backfill"):
    return WorkRef(FeedKey(uuid4(), uuid4(), "1m"), kind, uuid4())


def claim_for(work_ref):
    return LeaseClaim(work_ref, uuid4(), uuid4(), None)


class FakeLeases:
    def __init__(self, *, claimable=None):
        self.claimable = claimable
        self.claim_calls: list[WorkRef] = []
        self.heartbeat_calls: list[LeaseClaim] = []
        self.recover_calls = 0
        self.heartbeat_result = True

    def recover_expired(self, limit):
        self.recover_calls += 1
        return 0

    def claim(self, work_ref, owner_id):
        self.claim_calls.append(work_ref)
        if self.claimable is not None and work_ref not in self.claimable:
            return None
        return claim_for(work_ref)

    def heartbeat(self, claim):
        self.heartbeat_calls.append(claim)
        return self.heartbeat_result


class FakeExecute:
    def __init__(self, *, on_execute=None, fail_for=frozenset()):
        self.calls: list[LeaseClaim] = []
        self.on_execute = on_execute
        self.fail_for = fail_for

    async def execute(self, claim):
        self.calls.append(claim)
        if self.on_execute is not None:
            await self.on_execute(claim)
        if claim.work in self.fail_for:
            raise RuntimeError("synthetic failure")
        return "saved"


def make_runner(leases, execute, candidates, **kwargs):
    return WorkerRunner(leases, execute, lambda limit: list(candidates), **kwargs)


async def _run_bounded(runner, cycles=1):
    await runner.run(asyncio.Event(), max_cycles=cycles)


def test_fair_round_robin_processes_each_due_candidate_once_per_pass():
    a, b, c = work(), work("polling"), work()
    leases, execute = FakeLeases(), FakeExecute()
    runner = make_runner(leases, execute, [a, b, c])
    asyncio.run(_run_bounded(runner))
    assert [claim.work for claim in execute.calls] == [a, b, c]
    assert leases.claim_calls == [a, b, c]


def test_claim_none_is_skipped_without_executing():
    a, b = work(), work()
    leases, execute = FakeLeases(claimable={b}), FakeExecute()
    runner = make_runner(leases, execute, [a, b])
    asyncio.run(_run_bounded(runner))
    assert [claim.work for claim in execute.calls] == [b]


def test_stop_event_breaks_between_candidates_without_starting_new_work():
    a, b = work(), work()
    leases, execute = FakeLeases(), FakeExecute()
    stop = asyncio.Event()

    async def stop_after_first(_claim):
        stop.set()

    execute.on_execute = stop_after_first
    runner = make_runner(leases, execute, [a, b])
    asyncio.run(runner.run(stop, max_cycles=1))
    assert [claim.work for claim in execute.calls] == [a]


def test_unexpected_exception_from_execute_does_not_abort_the_scan_pass():
    a, b = work(), work()
    leases, execute = FakeLeases(), FakeExecute(fail_for={a})
    runner = make_runner(leases, execute, [a, b])
    asyncio.run(_run_bounded(runner))
    assert [claim.work for claim in execute.calls] == [a, b]


def test_recover_expired_and_discover_run_once_per_cycle():
    leases, execute = FakeLeases(), FakeExecute()
    discovered = []

    def discover(limit):
        discovered.append(limit)
        return []

    runner = WorkerRunner(leases, execute, discover, candidate_limit=42, scan_interval_seconds=0.01)
    asyncio.run(runner.run(asyncio.Event(), max_cycles=3))
    assert leases.recover_calls == 3
    assert discovered == [42, 42, 42]


def test_heartbeat_keepalive_ticks_during_a_long_page_and_stops_after():
    a = work()
    leases = FakeLeases()

    async def slow(_claim):
        await asyncio.sleep(0.05)

    execute = FakeExecute(on_execute=slow)
    runner = make_runner(leases, execute, [a], heartbeat_interval_seconds=0.01)
    asyncio.run(_run_bounded(runner))
    assert len(leases.heartbeat_calls) >= 1
    ticks_after_execute = len(leases.heartbeat_calls)
    asyncio.run(asyncio.sleep(0.05))
    assert len(leases.heartbeat_calls) == ticks_after_execute


def test_heartbeat_loss_stops_keepalive_without_raising():
    a = work()
    leases = FakeLeases()
    leases.heartbeat_result = False

    async def slow(_claim):
        await asyncio.sleep(0.03)

    execute = FakeExecute(on_execute=slow)
    runner = make_runner(leases, execute, [a], heartbeat_interval_seconds=0.01)
    asyncio.run(_run_bounded(runner))
    assert len(leases.heartbeat_calls) >= 1
