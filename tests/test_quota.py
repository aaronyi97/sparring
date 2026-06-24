"""每日配额（M4）：原子限额 / 隔离 / 剩余计数。"""
from sparring.quota import QuotaStore


def test_consume_until_limit(tmp_path):
    q = QuotaStore(tmp_path / "q.db")
    assert all(q.check_and_consume("u1", 3) for _ in range(3))
    assert q.check_and_consume("u1", 3) is False  # 第 4 次超限


def test_remaining_counts_down(tmp_path):
    q = QuotaStore(tmp_path / "q.db")
    assert q.remaining("u1", 5) == 5
    q.check_and_consume("u1", 5)
    q.check_and_consume("u1", 5)
    assert q.remaining("u1", 5) == 3


def test_users_isolated(tmp_path):
    q = QuotaStore(tmp_path / "q.db")
    q.check_and_consume("u1", 1)
    assert q.check_and_consume("u1", 1) is False
    assert q.check_and_consume("u2", 1) is True  # u2 不受 u1 影响


def test_limit_zero_blocks_all(tmp_path):
    q = QuotaStore(tmp_path / "q.db")
    assert q.check_and_consume("u1", 0) is False
