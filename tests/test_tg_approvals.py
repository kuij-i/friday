"""Per-action Telegram approvals and expiring trust — offline, with fake Telegram objects.

Each turn runs the real Agent (with FakeClient) in a worker thread via
`_respond`, exactly as in production, while the test taps buttons on the loop.
"""
import asyncio
import time
from types import SimpleNamespace

import pytest

from friday.agent import Agent as RealAgent
from friday.channels import telegram_bot
from friday.channels.telegram_bot import TelegramFriday
from friday.config import Config
from friday.tools.base import ToolResult
from tests.fakes import FakeClient, FakeResponse, TextBlock, ToolUseBlock

OWNER = 5


class FakeBot:
    def __init__(self):
        self.sent: list[dict] = []
        self.edits: list[dict] = []

    async def send_message(self, chat_id, text, **kw):
        self.sent.append({"chat_id": chat_id, "text": text, **kw})
        return SimpleNamespace(message_id=len(self.sent))

    async def edit_message_text(self, chat_id, message_id, text, **kw):
        self.edits.append({"chat_id": chat_id, "message_id": message_id, "text": text, **kw})

    async def send_chat_action(self, **_kw):
        return None


class Harness:
    def __init__(self, tmp_path, monkeypatch, script, timeout=5.0):
        cfg = Config()
        cfg.anthropic_api_key = "x"
        cfg.telegram_owner_id = str(OWNER)
        cfg.db_path = tmp_path / "db.sqlite"
        cfg.workspace = tmp_path / "ws"
        cfg.enable_web_search = False
        cfg.ensure_dirs()
        self.bot = TelegramFriday(cfg)
        self.tg = FakeBot()
        self.bot._app = SimpleNamespace(bot=self.tg)
        self.bot._approvals.timeout = timeout
        self.client = FakeClient(script)
        self.calls: dict[str, list[dict]] = {"run_shell": [], "write_file": []}

        def make_agent(config, memory, channel, approver):
            agent = RealAgent(config, memory, channel=channel, approver=approver, client=self.client)
            for name, calls in self.calls.items():
                agent.registry.get(name).handler = lambda _c=calls, **kw: (_c.append(kw), ToolResult("ran"))[1]
            return agent

        monkeypatch.setattr(telegram_bot, "Agent", make_agent)

    def close(self):
        self.bot.memory.close()

    # fake Telegram updates
    @staticmethod
    def message(text="do it", uid=OWNER):
        replies: list[str] = []

        async def reply_text(t):
            replies.append(t)

        upd = SimpleNamespace(
            effective_user=SimpleNamespace(id=uid),
            effective_chat=SimpleNamespace(id=uid, type="private"),
            effective_message=SimpleNamespace(text=text, reply_text=reply_text),
        )
        return upd, replies

    def ctx(self, *args):
        return SimpleNamespace(args=list(args), bot=self.tg)

    @staticmethod
    def tap(data, uid=OWNER, chat_id=OWNER, chat_type="private"):
        answers: list[str] = []

        async def answer(text=None, **_kw):
            answers.append(text)

        query = SimpleNamespace(
            data=data,
            from_user=SimpleNamespace(id=uid),
            message=SimpleNamespace(chat=SimpleNamespace(id=chat_id, type=chat_type)),
            answer=answer,
        )
        return SimpleNamespace(callback_query=query), answers

    async def press(self, data, **kw):
        upd, answers = self.tap(data, **kw)
        await self.bot.on_callback(upd, self.ctx())
        return answers[0]

    def start_turn(self, text="do it"):
        self.bot._approvals.loop = asyncio.get_running_loop()
        upd, replies = self.message(text)
        return asyncio.create_task(self.bot._respond(upd, self.ctx(), text)), replies

    async def wait_for_buttons(self, count=1):
        """Wait until `count` approval cards (messages with buttons) were sent; return their ids."""
        for _ in range(500):
            cards = [m for m in self.tg.sent if m.get("reply_markup") is not None]
            if len(cards) >= count:
                return [c["reply_markup"].inline_keyboard[0][0].callback_data[3:] for c in cards]
            await asyncio.sleep(0.01)
        raise AssertionError("no approval request was sent")

    def tool_results(self):
        return [
            block
            for m in self.client.messages.calls[-1]["messages"]
            if m["role"] == "user" and isinstance(m["content"], list)
            for block in m["content"]
        ]


def _script(name, tool_input, final="done"):
    return [
        FakeResponse([ToolUseBlock(name, tool_input)], "tool_use"),
        FakeResponse([TextBlock(final)], "end_turn"),
    ]


def run(coro):
    return asyncio.run(asyncio.wait_for(coro, 20))


@pytest.fixture
def harness(tmp_path, monkeypatch):
    made = []

    def make(script, **kw):
        h = Harness(tmp_path, monkeypatch, script, **kw)
        made.append(h)
        return h

    yield make
    for h in made:
        h.close()


# ── approve / deny / timeout ────────────────────────────────────────────
def test_approve_runs_tool_once_while_turn_waits(harness):
    h = harness(_script("run_shell", {"command": "echo hi"}))

    async def main():
        task, replies = h.start_turn()
        (aid,) = await h.wait_for_buttons()
        assert not task.done()  # the turn is blocked on the approval...
        assert await h.press(f"ap:{aid}") == "Approved."  # ...and the tap is still handled
        await task
        await asyncio.sleep(0.05)
        return replies

    replies = run(main())
    assert h.calls["run_shell"] == [{"command": "echo hi"}]
    assert replies == ["done"]
    card = h.tg.sent[-1]
    assert card["text"].startswith("Approval needed — run_shell (DANGEROUS)")
    assert '"command": "echo hi"' in card["text"] and "parse_mode" not in card
    assert h.tg.edits[-1]["text"].endswith("→ Approved") and h.tg.edits[-1]["reply_markup"] is None
    assert h.bot._approvals.pending_ids() == []


def test_deny_never_runs_tool(harness):
    h = harness(_script("run_shell", {"command": "rm -rf ~"}))

    async def main():
        task, _ = h.start_turn()
        (aid,) = await h.wait_for_buttons()
        assert await h.press(f"dn:{aid}") == "Denied."
        await task
        await asyncio.sleep(0.05)

    run(main())
    assert h.calls["run_shell"] == []
    (result,) = h.tool_results()
    assert result["is_error"] and "User declined" in result["content"]
    assert h.tg.edits[-1]["text"].endswith("→ Denied")


def test_timeout_denies(harness):
    h = harness(_script("run_shell", {"command": "echo hi"}), timeout=0.3)

    async def main():
        task, _ = h.start_turn()
        await h.wait_for_buttons()
        await task
        await asyncio.sleep(0.05)

    run(main())
    assert h.calls["run_shell"] == []
    (result,) = h.tool_results()
    assert "User declined" in result["content"] and "no answer" in result["content"]
    assert h.tg.edits[-1]["text"].endswith("→ Expired")


def test_double_tap_is_ignored(harness):
    h = harness(_script("run_shell", {"command": "echo hi"}))

    async def main():
        task, _ = h.start_turn()
        (aid,) = await h.wait_for_buttons()
        first = await h.press(f"ap:{aid}")
        second = await h.press(f"ap:{aid}")
        third = await h.press(f"dn:{aid}")
        await task
        return first, second, third

    assert run(main()) == ("Approved.", "Already handled.", "Already handled.")
    assert len(h.calls["run_shell"]) == 1


def test_taps_from_non_owner_or_wrong_chat_are_ignored(harness):
    h = harness(_script("run_shell", {"command": "echo hi"}))

    async def main():
        task, _ = h.start_turn()
        (aid,) = await h.wait_for_buttons()
        answers = [
            await h.press(f"ap:{aid}", uid=6, chat_id=6),  # a stranger
            await h.press(f"ap:{aid}", chat_id=-100, chat_type="group"),  # owner, in a group
            await h.press(f"ap:{aid}", chat_id=7),  # owner, another private chat
        ]
        assert h.bot._approvals.pending_ids() == [aid] and h.calls["run_shell"] == []
        answers.append(await h.press(f"ap:{aid}"))
        await task
        return answers

    assert run(main()) == ["Not authorized.", "Not authorized.", "Not authorized.", "Approved."]
    assert len(h.calls["run_shell"]) == 1


def test_unknown_or_malformed_callback(harness):
    h = harness([])

    async def main():
        return [await h.press("ap:nope"), await h.press("xx:1"), await h.press("")]

    assert run(main()) == ["Already handled.", "Unknown action.", "Unknown action."]


# ── trust ───────────────────────────────────────────────────────────────
def test_active_trust_auto_approves_low_tools(harness):
    h = harness(_script("write_file", {"path": "a.txt", "content": "x"}))
    h.bot._trust_until[OWNER] = time.monotonic() + 600

    async def main():
        task, _ = h.start_turn()
        await task

    run(main())
    assert h.calls["write_file"] == [{"path": "a.txt", "content": "x"}]
    assert h.tg.sent == []  # no approval card


def test_expired_trust_asks_again_for_low_tools(harness):
    h = harness(_script("write_file", {"path": "a.txt", "content": "x"}))
    h.bot._trust_until[OWNER] = time.monotonic() - 1  # expired a second ago

    async def main():
        task, _ = h.start_turn()
        (aid,) = await h.wait_for_buttons()
        assert h.calls["write_file"] == []
        await h.press(f"ap:{aid}")
        await task

    run(main())
    assert h.tg.sent[-1]["text"].startswith("Approval needed — write_file (LOW)")
    assert len(h.calls["write_file"]) == 1


def test_trust_on_still_asks_for_dangerous_tools(harness):
    h = harness(_script("run_shell", {"command": "echo hi"}))

    async def main():
        upd, _ = h.message("/trust_on")
        await h.bot.trust_on(upd, h.ctx())
        assert h.bot._trust_left(OWNER) > 0
        task, _ = h.start_turn()
        (aid,) = await h.wait_for_buttons()
        assert h.calls["run_shell"] == []
        await h.press(f"dn:{aid}")
        await task

    run(main())
    assert h.calls["run_shell"] == []


def test_trust_off_releases_pending_waits(harness):
    h = harness(_script("run_shell", {"command": "sleep 1"}))

    async def main():
        task, _ = h.start_turn()
        await h.wait_for_buttons()
        upd, replies = h.message("/trust_off")
        await h.bot.trust_off(upd, h.ctx())
        await task
        await asyncio.sleep(0.05)
        return replies

    replies = run(main())
    assert "Cancelled 1 waiting request" in replies[0]
    assert h.calls["run_shell"] == []
    assert h.tg.edits[-1]["text"].endswith("→ Denied (trust turned off)")
    assert "trust turned off" in h.tool_results()[0]["content"]


def test_reset_and_shutdown_release_pending_waits(harness):
    h = harness(_script("run_shell", {"command": "echo"}) + _script("run_shell", {"command": "echo"}))

    async def main():
        task, _ = h.start_turn()
        await h.wait_for_buttons(1)
        upd, _ = h.message("/reset")
        await h.bot.reset(upd, h.ctx())
        await task
        task, _ = h.start_turn()
        await h.wait_for_buttons(2)
        assert h.bot._approvals.deny_all(None, "Friday is shutting down") == 1
        await task

    run(main())
    assert h.calls["run_shell"] == []


def test_trust_status_and_bounds(harness):
    h = harness([])

    async def main():
        upd, replies = h.message("/trust_on 500")
        await h.bot.trust_on(upd, h.ctx("500"))
        upd2, status = h.message("/status")
        await h.bot.status(upd2, h.ctx())
        upd3, bad = h.message("/trust_on soon")
        await h.bot.trust_on(upd3, h.ctx("soon"))
        return replies, status, bad

    replies, status, bad = run(main())
    assert "Trust ON for 120 min" in replies[0]  # clamped to the maximum
    assert "trust: ON for LOW actions — 120 min left" in status[0]
    assert "dangerous actions: always ask" in status[0]
    assert bad[0].startswith("Usage: /trust_on")


def test_status_warns_about_auto_approve(harness):
    h = harness([])
    h.bot.config.auto_approve = True

    async def main():
        upd, status = h.message("/status")
        await h.bot.status(upd, h.ctx())
        return status

    status = run(main())
    assert "trust: off" in status[0] and "AUTO-APPROVE IS ON" in status[0]


# ── long input ──────────────────────────────────────────────────────────
def test_long_command_is_split_but_approved_as_one_action(harness):
    command = "echo " + "x" * 10_000
    h = harness(_script("run_shell", {"command": command}))

    async def main():
        task, _ = h.start_turn()
        (aid,) = await h.wait_for_buttons()
        assert h.bot._approvals.pending_ids() == [aid]
        await h.press(f"ap:{aid}")
        await task

    run(main())
    assert len(h.tg.sent) >= 3
    assert all(m["reply_markup"] is None for m in h.tg.sent[:-1])
    assert h.tg.sent[-1]["reply_markup"] is not None
    assert all("parse_mode" not in m and len(m["text"]) <= 4096 for m in h.tg.sent)
    assert h.tg.sent[0]["text"].startswith(f"(part 1/{len(h.tg.sent)})")
    shown = "".join(m["text"].split("\n", 1)[1] for m in h.tg.sent)
    assert command in shown  # DANGEROUS input is never truncated
    assert h.calls["run_shell"] == [{"command": command}]


def test_callback_data_fits_telegram_limit(harness):
    h = harness(_script("run_shell", {"command": "echo"}))

    async def main():
        task, _ = h.start_turn()
        await h.wait_for_buttons()
        h.bot._approvals.deny_all(None, "test")
        await task

    run(main())
    buttons = h.tg.sent[-1]["reply_markup"].inline_keyboard[0]
    assert all(len(b.callback_data.encode()) <= 64 for b in buttons)


# ── wiring ──────────────────────────────────────────────────────────────
def test_app_processes_updates_concurrently(tmp_path):
    from telegram.ext import CallbackQueryHandler

    cfg = Config()
    cfg.anthropic_api_key = "x"
    cfg.telegram_owner_id = str(OWNER)
    cfg.telegram_token = "123456:offline-test-token"
    cfg.db_path, cfg.workspace = tmp_path / "db.sqlite", tmp_path / "ws"
    bot = TelegramFriday(cfg)
    app = bot.build()
    assert app.concurrent_updates > 1
    assert any(isinstance(h, CallbackQueryHandler) for h in app.handlers[0])
    bot.memory.close()


def test_no_loop_means_denied(harness):
    h = harness([])
    from friday.tools.base import Risk

    assert h.bot._approvals.request(OWNER, "run_shell", {"command": "x"}, Risk.DANGEROUS)[0] is False
