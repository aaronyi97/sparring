"""会话存储：SQLite WAL · owner fail-closed · TTL 回收 · 上限驱逐。

schema 合约（审查）：owner_id 索引 / expires_at / state / 全端点先 owner 校验。
"""
from __future__ import annotations

import json
import sqlite3
import threading
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path

TTL_SECONDS = 30 * 24 * 3600
MAX_SESSIONS = 200  # 上限驱逐（上游 同款保护）

_SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions (
  id TEXT PRIMARY KEY,
  owner_id TEXT NOT NULL,
  question TEXT NOT NULL,
  state TEXT NOT NULL DEFAULT 'phase1',
  divergence_map TEXT NOT NULL DEFAULT '',
  contributors TEXT NOT NULL DEFAULT '[]',
  turns TEXT NOT NULL DEFAULT '[]',
  user_stance TEXT NOT NULL DEFAULT '',
  stance_reason TEXT NOT NULL DEFAULT '',
  closure_reason TEXT NOT NULL DEFAULT '',
  synthesis TEXT NOT NULL DEFAULT '',
  comparison TEXT NOT NULL DEFAULT '',
  memo_md TEXT NOT NULL DEFAULT '',
  rounds_used INTEGER NOT NULL DEFAULT 0,
  active_dp_index INTEGER NOT NULL DEFAULT 0,
  created_at INTEGER NOT NULL,
  updated_at INTEGER NOT NULL,
  expires_at INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_sessions_owner ON sessions(owner_id, updated_at DESC);
"""

# 状态机：phase1 → coaching → stance → revealed（expired 由 TTL 隐式表达）
VALID_STATES = {"phase1", "coaching", "stance", "revealed"}

# owner 哨兵值黑名单（审查：要求 owner=0 也拒）
_INVALID_OWNERS = {"", "0", "null", "none", "undefined", "anonymous"}


def _valid_owner(owner_id: str) -> bool:
    return bool(owner_id) and str(owner_id).strip().lower() not in _INVALID_OWNERS


@dataclass
class Turn:
    role: str  # user | coach | system
    text: str
    meta: dict = field(default_factory=dict)
    ts: int = 0


@dataclass
class Session:
    id: str
    owner_id: str
    question: str
    state: str = "phase1"
    divergence_map: dict | None = None
    contributors: list = field(default_factory=list)
    turns: list = field(default_factory=list)  # list[Turn]
    user_stance: str = ""
    stance_reason: str = ""
    closure_reason: str = ""
    synthesis: str = ""
    comparison: dict | None = None
    memo_md: str = ""
    rounds_used: int = 0
    active_dp_index: int = 0
    created_at: int = 0
    updated_at: int = 0
    expires_at: int = 0


class SessionStore:
    def __init__(self, db_path: Path | str):
        db_path = Path(db_path)
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self._path = str(db_path)
        self._lock = threading.Lock()
        with self._connect() as con:
            con.executescript(_SCHEMA)

    def _connect(self) -> sqlite3.Connection:
        con = sqlite3.connect(self._path, timeout=10)
        con.execute("PRAGMA journal_mode=WAL")
        con.execute("PRAGMA busy_timeout=10000")
        con.row_factory = sqlite3.Row
        return con

    def create(self, owner_id: str, question: str) -> Session:
        if not _valid_owner(owner_id):
            raise ValueError("owner_id 非法（fail-closed，含 0/null 等哨兵值）")
        now = int(time.time())
        s = Session(
            id=uuid.uuid4().hex[:16],
            owner_id=owner_id,
            question=question,
            created_at=now,
            updated_at=now,
            expires_at=now + TTL_SECONDS,
        )
        with self._lock, self._connect() as con:
            self._evict(con)
            con.execute(
                "INSERT INTO sessions (id, owner_id, question, state, created_at, updated_at, expires_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (s.id, s.owner_id, s.question, s.state, s.created_at, s.updated_at, s.expires_at),
            )
        return s

    def get(self, session_id: str, owner_id: str) -> Session | None:
        """owner 不匹配 / owner 非法 → 一律 None（fail-closed，owner=0 也拒）。"""
        if not _valid_owner(owner_id) or not session_id:
            return None
        with self._connect() as con:
            row = con.execute(
                "SELECT * FROM sessions WHERE id = ? AND owner_id = ? AND expires_at > ?",
                (session_id, owner_id, int(time.time())),
            ).fetchone()
        return self._row_to_session(row) if row else None

    def save(self, s: Session) -> None:
        if s.state not in VALID_STATES:
            raise ValueError(f"非法状态: {s.state}")
        s.updated_at = int(time.time())
        with self._lock, self._connect() as con:
            cur = con.execute(
                "UPDATE sessions SET state=?, divergence_map=?, contributors=?, turns=?, "
                "user_stance=?, stance_reason=?, closure_reason=?, synthesis=?, comparison=?, "
                "memo_md=?, rounds_used=?, active_dp_index=?, updated_at=? "
                "WHERE id=? AND owner_id=?",
                (
                    s.state,
                    json.dumps(s.divergence_map, ensure_ascii=False) if s.divergence_map else "",
                    json.dumps(s.contributors, ensure_ascii=False),
                    json.dumps([asdict(t) if isinstance(t, Turn) else t for t in s.turns], ensure_ascii=False),
                    s.user_stance,
                    s.stance_reason,
                    s.closure_reason,
                    s.synthesis,
                    json.dumps(s.comparison, ensure_ascii=False) if s.comparison else "",
                    s.memo_md,
                    s.rounds_used,
                    s.active_dp_index,
                    s.updated_at,
                    s.id,
                    s.owner_id,
                ),
            )
            if cur.rowcount != 1:
                # 审查：0 行更新 = 会话不存在或 owner 被污染，拒绝静默丢写
                raise LookupError(f"save 影响 0 行（session={s.id}），拒绝静默丢写")

    def delete(self, session_id: str, owner_id: str) -> None:
        """启动失败清理用（审查：不留 phase1 卡死尸体）。"""
        if not _valid_owner(owner_id) or not session_id:
            return
        with self._lock, self._connect() as con:
            con.execute("DELETE FROM sessions WHERE id = ? AND owner_id = ?", (session_id, owner_id))

    def list_for_owner(self, owner_id: str, limit: int = 50) -> list[dict]:
        if not _valid_owner(owner_id):
            return []
        with self._connect() as con:
            rows = con.execute(
                "SELECT id, question, state, rounds_used, user_stance, created_at, updated_at "
                "FROM sessions WHERE owner_id = ? AND expires_at > ? ORDER BY updated_at DESC LIMIT ?",
                (owner_id, int(time.time()), limit),
            ).fetchall()
        return [dict(r) for r in rows]

    def _evict(self, con: sqlite3.Connection) -> None:
        now = int(time.time())
        con.execute("DELETE FROM sessions WHERE expires_at <= ?", (now,))
        n = con.execute("SELECT COUNT(*) FROM sessions").fetchone()[0]
        if n >= MAX_SESSIONS:
            con.execute(
                "DELETE FROM sessions WHERE id IN "
                "(SELECT id FROM sessions ORDER BY updated_at ASC LIMIT ?)",
                (n - MAX_SESSIONS + 1,),
            )

    @staticmethod
    def _row_to_session(row: sqlite3.Row) -> Session:
        def _j(s, default):
            try:
                return json.loads(s) if s else default
            except (ValueError, TypeError):
                return default

        turns = [Turn(**t) if isinstance(t, dict) else t for t in _j(row["turns"], [])]
        return Session(
            id=row["id"],
            owner_id=row["owner_id"],
            question=row["question"],
            state=row["state"],
            divergence_map=_j(row["divergence_map"], None),
            contributors=_j(row["contributors"], []),
            turns=turns,
            user_stance=row["user_stance"],
            stance_reason=row["stance_reason"],
            closure_reason=row["closure_reason"],
            synthesis=row["synthesis"],
            comparison=_j(row["comparison"], None),
            memo_md=row["memo_md"],
            rounds_used=row["rounds_used"],
            active_dp_index=row["active_dp_index"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            expires_at=row["expires_at"],
        )
