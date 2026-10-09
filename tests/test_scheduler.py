import asyncio
import time

from friday.memory import Memory
from friday.scheduler import Scheduler


def test_tick_delivers_and_closes_oneoff(tmp_path):
    mem = Memory(tmp_path / "db.sqlite")
    mem.add_reminder("tg:1", "standup", time.time() - 1, interval=None)
    delivered = []

    async def deliver(channel, text):
        delivered.append((channel, text))

    async def run():
        sched = Scheduler(mem, deliver)
        await sched._tick()

    asyncio.run(run())
    assert delivered == [("tg:1", "⏰ Reminder: standup")]
    # one-off should now be inactive → not due
    assert mem.due_reminders() == []
    mem.close()


def test_tick_reschedules_recurring(tmp_path):
    mem = Memory(tmp_path / "db.sqlite")
    mem.add_reminder("tg:2", "water", time.time() - 1, interval=3600)
    calls = []

    async def deliver(channel, text):
        calls.append(text)

    async def run():
        await Scheduler(mem, deliver)._tick()

    asyncio.run(run())
    assert len(calls) == 1
    # recurring stays active but is no longer due (next_run pushed ~1h out)
    assert mem.due_reminders() == []
    assert len(mem.list_reminders("tg:2")) == 1
    mem.close()


def test_delivery_failure_still_advances(tmp_path):
    mem = Memory(tmp_path / "db.sqlite")
    mem.add_reminder("tg:3", "x", time.time() - 1, interval=None)

    async def deliver(channel, text):
        raise RuntimeError("telegram down")

    async def run():
        await Scheduler(mem, deliver)._tick()

    asyncio.run(run())
    # even though delivery failed, the one-off is closed so it won't spam
    assert mem.due_reminders() == []
    mem.close()
