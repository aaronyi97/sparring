"""每日配额（M4 · schema 合约 UNIQUE(user_id,date)）。

按"开始一局对练"计数；check_and_consume 原子判断+递增，超限不计数。
"""
from __future__ import annotations

import sqlite3
import threading
import time
from pathlib import Path

_SCHEMA = """
CREATE TABLE IF NOT EXISTS daily_quota (
  user_id TEXT NOT NULL,
  date TEXT NOT NULL,
  count INTEGER NOT NULL DEFAULT 0,
  PRIMARY KEY (user_id, date)
);
"""


def _today(ts: float | None = None) -> str:
    return time.strftime("%Y-%m-%d", time.localtime(ts))


class QuotaStore:
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

    def check_and_consume(self, user_id: str, limit: int) -> bool:
        """DB 级原子（审查）：单条 UPSERT 带 WHERE count<limit + rowcount 判定。

        新行首次插入恒成功（limit>=1 时 count=1 合法）；冲突时仅当未达上限才递增。
        多进程下也不会超发——不依赖进程内读后写。
        """
        if limit <= 0:
            return False
        date = _today()
        with self._lock, self._connect() as con:
            cur = con.execute(
                "INSERT INTO daily_quota (user_id, date, count) VALUES (?, ?, 1) "
                "ON CONFLICT(user_id, date) DO UPDATE SET count = count + 1 WHERE count < ?",
                (user_id, date, limit),
            )
            return cur.rowcount == 1

    def remaining(self, user_id: str, limit: int) -> int:
        with self._connect() as con:
            row = con.execute(
                "SELECT count FROM daily_quota WHERE user_id = ? AND date = ?", (user_id, _today())
            ).fetchone()
        return max(0, limit - (int(row["count"]) if row else 0))
