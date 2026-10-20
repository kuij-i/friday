"""Entry points, config paths, CLI commands and Telegram wiring — all offline."""
from pathlib import Path

from friday import __main__ as entry
from friday.config import PROJECT_DIR, Config, _path


def test_main_help_version_and_unknown(capsys):
    assert entry.main(["help"]) == 0
    assert entry.main(["version"]) == 0
    assert entry.main(["nonsense"]) == 2
    assert "Usage" in capsys.readouterr().out


def test_doctor_reports_missing_key(monkeypatch, capsys, tmp_path):
    import friday.config as cfgmod

    monkeypatch.setattr(cfgmod.config, "anthropic_api_key", "")
    monkeypatch.setattr(cfgmod.config, "workspace", tmp_path / "ws")
    monkeypatch.setattr(cfgmod.config, "db_path", tmp_path / "data" / "db.sqlite")
    assert entry.main(["doctor"]) == 1
    assert "ANTHROPIC_API_KEY" in capsys.readouterr().out


def test_doctor_reports_telegram_owner(monkeypatch, capsys, tmp_path):
    import friday.config as cfgmod
    from friday.channels.pairing import PAIRED_SETTING
    from friday.memory import Memory

    db = tmp_path / "data" / "db.sqlite"
    monkeypatch.setattr(cfgmod.config, "workspace", tmp_path / "ws")
    monkeypatch.setattr(cfgmod.config, "db_path", db)
    monkeypatch.setattr(cfgmod.config, "telegram_owner_id", "")
    entry.main(["doctor"])
    assert "owner: NOT SET" in capsys.readouterr().out
    mem = Memory(db)
    mem.set_setting(PAIRED_SETTING, "111")
    mem.close()
    entry.main(["doctor"])
    assert "owner: 111 (paired, pin it in .env)" in capsys.readouterr().out
    monkeypatch.setattr(cfgmod.config, "telegram_owner_id", "5")
    entry.main(["doctor"])
    assert "owner: 5 (from .env)" in capsys.readouterr().out


def test_telegram_reset_owner_runs_offline(monkeypatch, capsys, tmp_path):
    import friday.config as cfgmod

    monkeypatch.setattr(cfgmod.config, "db_path", tmp_path / "none.sqlite")
    assert entry.main(["telegram", "--reset-owner"]) == 0
    assert "no stored Telegram owner" in capsys.readouterr().out


def test_relative_paths_resolve_against_project_dir(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)  # launch directory must not matter
    monkeypatch.setenv("FRIDAY_WORKSPACE", "some/ws")
    assert _path("FRIDAY_WORKSPACE", "./workspace") == (PROJECT_DIR / "some/ws").resolve()
    monkeypatch.setenv("FRIDAY_WORKSPACE", str(tmp_path / "abs"))
    assert _path("FRIDAY_WORKSPACE", "./workspace") == (tmp_path / "abs").resolve()
    monkeypatch.delenv("FRIDAY_WORKSPACE")
    assert _path("FRIDAY_WORKSPACE", "./workspace") == (PROJECT_DIR / "workspace").resolve()


def test_placeholder_key_is_flagged():
    cfg = Config()
    cfg.anthropic_api_key = "sk-ant-..."
    assert any("placeholder" in p for p in cfg.validate())


def test_cli_commands_never_crash_on_markup(tmp_path, capsys):
    from friday.channels import cli
    from friday.memory import Memory

    mem = Memory(tmp_path / "db.sqlite")
    mem.remember_fact("x", "closing tag [/bold] and [red]colour[/red] and [/]")
    mem.add_reminder("cli", "[/oops] stretch", 2_000_000_000)
    for cmd in ("/memory", "/reminders", "/help", "/bogus"):
        cli._command(cmd, mem)
    out = capsys.readouterr().out
    assert "[/bold]" in out and "[/oops] stretch" in out and "Unknown command" in out
    mem.close()


def test_cli_refuses_to_start_without_key(tmp_path):
    from friday.channels.cli import run_cli

    cfg = Config()
    cfg.anthropic_api_key = ""
    cfg.workspace, cfg.db_path = tmp_path / "ws", tmp_path / "db.sqlite"
    assert run_cli(cfg) == 1


def test_telegram_app_builds_offline(tmp_path):
    from friday.channels.telegram_bot import _COMMANDS, TelegramFriday

    cfg = Config()
    cfg.anthropic_api_key = "x"
    cfg.telegram_token = "123456:offline-test-token"
    cfg.db_path, cfg.workspace = tmp_path / "db.sqlite", tmp_path / "ws"
    bot = TelegramFriday(cfg)
    app = bot.build()
    commands = {c for h in app.handlers[0] for c in getattr(h, "commands", ())}
    assert {name for name, _ in _COMMANDS} <= commands | {"help"}
    bot.memory.close()


def test_run_py_shim_exists():
    root = Path(__file__).resolve().parents[1]
    assert "friday.__main__" in (root / "run.py").read_text()


def test_telegram_preflight_leaves_a_usable_event_loop(monkeypatch):
    # Older python-telegram-bot calls asyncio.get_event_loop() in run_polling,
    # which raises after a bare asyncio.run() on Python 3.12+.
    import asyncio

    from friday.channels import telegram_bot

    async def fake_check(_token):
        return "friday_test_bot"

    monkeypatch.setattr(telegram_bot, "_check_token", fake_check)
    assert telegram_bot.preflight("123:abc") == "friday_test_bot"
    loop = asyncio.get_event_loop()
    assert not loop.is_closed()
    loop.close()
