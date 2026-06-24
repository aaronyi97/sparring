"""会话存储高风险路径：owner fail-closed / 往返一致 / TTL / 列表隔离。"""
import sqlite3
import time

import pytest

from sparring.store import Session, SessionStore, Turn


@pytest.fixture
def store(tmp_path):
    return SessionStore(tmp_path / "s.db")


def test_create_and_get(store):
    s = store.create("u1", "要不要换城市？")
    got = store.get(s.id, "u1")
    assert got is not None and got.question == "要不要换城市？" and got.state == "phase1"


def test_get_wrong_owner_fail_closed(store):
    s = store.create("u1", "q")
    assert store.get(s.id, "u2") is None
    assert store.get(s.id, "") is None  # owner 为空也拒（owner=0 同型防线）


def test_create_empty_owner_rejected(store):
    with pytest.raises(ValueError):
        store.create("", "q")


def test_owner_sentinel_values_rejected(store):
    """审查：owner=0/null 等哨兵值全拒（fail-closed 合约）。"""
    for bad in ("0", "null", "NONE", " anonymous "):
        with pytest.raises(ValueError):
            store.create(bad, "q")
    s = store.create("u1", "q")
    assert store.get(s.id, "0") is None


def test_save_zero_rows_raises(store):
    """审查：owner 被污染时 UPDATE 0 行必须炸，不许静默丢写。"""
    s = store.create("u1", "q")
    s.owner_id = "u2"
    with pytest.raises(LookupError):
        store.save(s)


def test_delete_scoped_to_owner(store):
    s = store.create("u1", "q")
    store.delete(s.id, "u2")  # 错 owner 删不动
    assert store.get(s.id, "u1") is not None
    store.delete(s.id, "u1")
    assert store.get(s.id, "u1") is None


def test_save_roundtrip_turns_and_map(store):
    s = store.create("u1", "q")
    s.divergence_map = {"consensus_points": ["a"], "divergence_points": [], "overall_consensus_score": 0.5}
    s.turns.append(Turn(role="coach", text="第一问", meta={"track": "A"}))
    s.user_stance = "先不换"
    s.state = "coaching"
    store.save(s)
    got = store.get(s.id, "u1")
    assert got.divergence_map["consensus_points"] == ["a"]
    assert got.turns[0].text == "第一问" and got.turns[0].meta["track"] == "A"
    assert got.user_stance == "先不换"


def test_invalid_state_rejected(store):
    s = store.create("u1", "q")
    s.state = "hacked"
    with pytest.raises(ValueError):
        store.save(s)


def test_expired_session_not_returned(store, tmp_path):
    s = store.create("u1", "q")
    con = sqlite3.connect(tmp_path / "s.db")
    con.execute("UPDATE sessions SET expires_at = ? WHERE id = ?", (int(time.time()) - 10, s.id))
    con.commit()
    con.close()
    assert store.get(s.id, "u1") is None


def test_list_for_owner_isolated(store):
    store.create("u1", "q1")
    store.create("u2", "q2")
    lst = store.list_for_owner("u1")
    assert len(lst) == 1 and lst[0]["question"] == "q1"
    assert store.list_for_owner("") == []
