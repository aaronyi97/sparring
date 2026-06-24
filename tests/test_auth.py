"""邀请码鉴权（M4 · 关 CRITICAL）：一码一人 / token 解析 / consent。"""
import pytest

from sparring.auth import AuthError, AuthStore


@pytest.fixture
def auth(tmp_path):
    return AuthStore(tmp_path / "a.db")


def test_redeem_issues_account(auth):
    code = auth.create_invite("种子用户A")
    acc = auth.redeem(code)
    assert acc.user_id.startswith("u-") and len(acc.token) > 20


def test_invite_is_one_time(auth):
    code = auth.create_invite()
    auth.redeem(code)
    with pytest.raises(AuthError):
        auth.redeem(code)  # 第二次必拒（一码一人）


def test_invalid_code_rejected(auth):
    with pytest.raises(AuthError):
        auth.redeem("sp-doesnotexist")
    with pytest.raises(AuthError):
        auth.redeem("")


def test_token_resolves_to_user(auth):
    acc = auth.redeem(auth.create_invite())
    assert auth.resolve_token(acc.token) == acc.user_id


def test_bad_token_returns_none(auth):
    assert auth.resolve_token("garbage") is None
    assert auth.resolve_token("") is None


def test_consent_default_off_and_toggle(auth):
    """审查：画像默认关（opt-in），显式开启才被观察。"""
    acc = auth.redeem(auth.create_invite())
    assert auth.get_consent(acc.user_id) is False
    auth.set_consent(acc.user_id, True)
    assert auth.get_consent(acc.user_id) is True


def test_two_users_distinct_identities(auth):
    a = auth.redeem(auth.create_invite())
    b = auth.redeem(auth.create_invite())
    assert a.user_id != b.user_id and a.token != b.token
