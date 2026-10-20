"""Pairing codes and owner resolution (no telegram imports needed)."""
from friday.channels.pairing import (
    _ALPHABET,
    LEGACY_SETTING,
    MAX_ATTEMPTS,
    PAIRED_SETTING,
    OwnerStatus,
    PairingCode,
    PairResult,
    owner_status_line,
    resolve_owner,
)
from friday.config import Config
from friday.memory import Memory


def test_code_format_is_easy_to_type():
    for _ in range(20):
        code = PairingCode().display
        a, b = code.split("-")
        assert len(a) == len(b) == 4
        assert set(a + b) <= set(_ALPHABET)
        assert not set("01OIL") & set(a + b)


def test_codes_are_random():
    assert len({PairingCode().display for _ in range(50)}) == 50


def test_check_normalises_input():
    code = PairingCode()
    assert code.check(f"  {code.display.lower().replace('-', '')} ") is PairResult.OK


def test_wrong_attempts_are_capped():
    code = PairingCode()
    for _ in range(MAX_ATTEMPTS):
        assert code.check("nope") is PairResult.WRONG
    assert not code.open
    assert code.check(code.display) is PairResult.LOCKED


def test_expiry_uses_the_clock():
    now = [0.0]
    code = PairingCode(clock=lambda: now[0])
    now[0] = 601
    assert code.check(code.display) is PairResult.EXPIRED


def test_resolve_owner_precedence(tmp_path):
    cfg = Config()
    mem = Memory(tmp_path / "db.sqlite")
    assert resolve_owner(cfg, mem) == OwnerStatus(None, None, None)
    mem.set_setting(LEGACY_SETTING, "666")
    assert resolve_owner(cfg, mem) == OwnerStatus(None, None, "666")  # legacy never honoured
    mem.set_setting(PAIRED_SETTING, "111")
    assert resolve_owner(cfg, mem).owner_id == 111
    cfg.telegram_owner_id = "5"
    assert resolve_owner(cfg, mem) == OwnerStatus(5, "env", "666")
    assert resolve_owner(cfg, None) == OwnerStatus(5, "env", None)
    mem.close()


def test_owner_status_lines():
    assert owner_status_line(OwnerStatus(5, "env")) == "owner: 5 (from .env)"
    assert owner_status_line(OwnerStatus(111, "paired")) == "owner: 111 (paired, pin it in .env)"
    assert owner_status_line(OwnerStatus(None, None)) == "owner: NOT SET — run `python run.py telegram` and pair"
