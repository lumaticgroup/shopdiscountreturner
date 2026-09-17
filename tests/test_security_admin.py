import hmac
import time
import pytest
from bot import (
    _check_login_rate_limit,
    _record_failed_login,
    _clear_login_attempts,
    _MAX_LOGIN_ATTEMPTS,
    _LOCKOUT_WINDOW_SECONDS,
)
import config


def test_rate_limit_allows_under_threshold():
    chat_id = 999001
    _clear_login_attempts(chat_id)

    # 4 failed attempts should still be allowed
    for _ in range(4):
        _record_failed_login(chat_id)
        allowed, _ = _check_login_rate_limit(chat_id)
        assert allowed is True


def test_rate_limit_locks_out_after_max_attempts():
    chat_id = 999002
    _clear_login_attempts(chat_id)

    # Record 5 failed attempts (reaching the max)
    for _ in range(_MAX_LOGIN_ATTEMPTS):
        _record_failed_login(chat_id)

    allowed, cooldown = _check_login_rate_limit(chat_id)
    assert allowed is False
    assert 0 < cooldown <= _LOCKOUT_WINDOW_SECONDS

    # Clear attempts resets lockout
    _clear_login_attempts(chat_id)
    allowed, cooldown = _check_login_rate_limit(chat_id)
    assert allowed is True
    assert cooldown == 0


def test_hmac_compare_digest_behavior():
    secret = "SuperSecretAdminPassword123!"
    correct_input = "SuperSecretAdminPassword123!"
    wrong_input = "WrongPassword123!"

    assert hmac.compare_digest(correct_input, secret) is True
    assert hmac.compare_digest(wrong_input, secret) is False


def test_db_path_configured():
    assert config.DB_PATH is not None
    assert len(config.DB_PATH) > 0
