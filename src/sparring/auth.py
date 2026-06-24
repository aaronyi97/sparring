"""邀请码鉴权（M4 · 关掉 审查 的 CRITICAL：客户端自报身份）。

机制：邀请码一码一人 → redeem 换服务端签发的随机 token + user_id；后续请求带
Bearer token，服务端用 token 反查真实 user_id——客户端无法再冒充任意 owner。

隐私（审查）：画像 consent 默认关（opt-in），用户须显式开启才被观察。
原子性（审查）：redeem 用条件 UPDATE 抢占 + rowcount 判定，多进程不超发。
"""
from __future__ import annotations

import secrets
import sqlite3
import threading
import time
from dataclasses import dataclass
from pathlib import Path

_SCHEMA = """
CREATE TABLE IF NOT EXISTS invites (
  code TEXT PRIMARY KEY,
  note TEXT NOT NULL DEFAULT '',
  created_at INTEGER NOT NULL,
  redeemed_by TEXT NOT NULL DEFAULT '',
  redeemed_at INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS users (
  id TEXT PRIMARY KEY,
  token TEXT NOT NULL UNIQUE,
  invite_code TEXT NOT NULL DEFAULT '',
  consent_profile INTEGER NOT NULL DEFAULT 0,
  created_at INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_users_token ON users(token);
"""


class AuthError(RuntimeError):
    pass


@dataclass
class Account:
    user_id: str
    token: str


class AuthStore:
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

    def create_invite(self, note: str = "") -> str:
        code = "sp-" + secrets.token_hex(5)
        with self._lock, self._connect() as con:
            con.execute(
                "INSERT INTO invites (code, note, created_at) VALUES (?, ?, ?)",
                (code, note, int(time.time())),
            )
        return code

    def redeem(self, code: str) -> Account:
        """邀请码换账号。一码一人——条件 UPDATE 抢占保证 DB 级原子（并发不双发）。"""
        code = (code or "").strip()
        if not code:
            raise AuthError("邀请码不能为空")
        user_id = "u-" + secrets.token_hex(8)
        token = secrets.token_urlsafe(32)
        now = int(time.time())
        with self._lock, self._connect() as con:
            # 抢占式 UPDATE：只有 redeemed_by 仍为空的码能被这次抢到（rowcount=1）
            cur = con.execute(
                "UPDATE invites SET redeemed_by = ?, redeemed_at = ? WHERE code = ? AND redeemed_by = ''",
                (user_id, now, code),
            )
            if cur.rowcount != 1:
                exists = con.execute("SELECT 1 FROM invites WHERE code = ?", (code,)).fetchone()
                raise AuthError("邀请码已被使用" if exists else "邀请码无效")
            con.execute(
                "INSERT INTO users (id, token, invite_code, consent_profile, created_at) VALUES (?, ?, ?, 0, ?)",
                (user_id, token, code, now),
            )
        return Account(user_id=user_id, token=token)

    def resolve_token(self, token: str) -> str | None:
        """Bearer token → user_id；无效返回 None（fail-closed）。"""
        token = (token or "").strip()
        if not token:
            return None
        with self._connect() as con:
            row = con.execute("SELECT id FROM users WHERE token = ?", (token,)).fetchone()
        return row["id"] if row else None

    def get_consent(self, user_id: str) -> bool:
        with self._connect() as con:
            row = con.execute("SELECT consent_profile FROM users WHERE id = ?", (user_id,)).fetchone()
        return bool(row["consent_profile"]) if row else False

    def set_consent(self, user_id: str, consent: bool) -> None:
        with self._lock, self._connect() as con:
            con.execute("UPDATE users SET consent_profile = ? WHERE id = ?", (1 if consent else 0, user_id))
