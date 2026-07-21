"""Allowlist / deny-if-empty unit coverage (SEC / T-02-auth)."""
from config import Settings, is_user_authorized


def test_unauthorized_user_rejected():
    s = Settings(TELEGRAM_ALLOWED_USERS="111,222")
    allowed = s.allowed_user_ids()
    assert 999 not in allowed
    assert is_user_authorized(999, allowed) is False


def test_empty_allowlist_denies_all():
    s = Settings(TELEGRAM_ALLOWED_USERS="")
    allowed = s.allowed_user_ids()
    assert allowed == set()
    assert is_user_authorized(111, allowed) is False
    assert is_user_authorized(None, allowed) is False


def test_authorized_user_allowed():
    s = Settings(TELEGRAM_ALLOWED_USERS="111,222")
    allowed = s.allowed_user_ids()
    assert allowed == {111, 222}
    assert is_user_authorized(111, allowed) is True
    assert is_user_authorized(222, allowed) is True


def test_whitespace_and_csv_parsing():
    s = Settings(TELEGRAM_ALLOWED_USERS=" 10, 20 ,30 ")
    assert s.allowed_user_ids() == {10, 20, 30}


def test_token_alias_loads():
    s = Settings(TELEGRAM_BOT_TOKEN="tok-abc")
    assert s.telegram_bot_token == "tok-abc"
