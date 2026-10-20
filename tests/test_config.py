from friday.config import Config, _as_bool


def test_as_bool():
    assert _as_bool("true") and _as_bool("1") and _as_bool("YES") and _as_bool("on")
    assert not _as_bool("false") and not _as_bool(None) and not _as_bool("")
    assert _as_bool(None, True) is True


def test_validate_flags_missing_key():
    cfg = Config()
    cfg.anthropic_api_key = ""
    problems = cfg.validate()
    assert any("ANTHROPIC_API_KEY" in p for p in problems)


def test_validate_bad_effort():
    cfg = Config()
    cfg.anthropic_api_key = "x"
    cfg.effort = "turbo"
    problems = cfg.validate()
    assert any("FRIDAY_EFFORT" in p for p in problems)


def test_validate_clean():
    cfg = Config()
    cfg.anthropic_api_key = "x"
    cfg.effort = "high"
    assert cfg.validate() == []


def test_ensure_dirs(tmp_path):
    cfg = Config()
    cfg.workspace = tmp_path / "ws"
    cfg.db_path = tmp_path / "sub" / "db.sqlite"
    cfg.ensure_dirs()
    assert cfg.workspace.is_dir() and cfg.db_path.parent.is_dir()


def test_validate_owner_must_be_a_positive_user_id():
    cfg = Config()
    cfg.anthropic_api_key = "x"
    for bad in ("-100123", "0", "abc"):
        cfg.telegram_owner_id = bad
        assert any("TELEGRAM_OWNER_ID" in p for p in cfg.validate())
    cfg.telegram_owner_id = "12345"
    assert cfg.validate() == []
