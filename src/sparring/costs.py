"""成本台账 — 上游"恒 0 债"的修复：每次真实调用都入账，request_id 幂等。"""
from __future__ import annotations

import sqlite3
import threading
import time
from pathlib import Path

_SCHEMA = """
CREATE TABLE IF NOT EXISTS cost_ledger (
  request_id TEXT PRIMARY KEY,
  ts INTEGER NOT NULL,
  session_id TEXT NOT NULL DEFAULT '',
  user_id TEXT NOT NULL DEFAULT '',
  role TEXT NOT NULL,
  model TEXT NOT NULL,
  tokens_in INTEGER NOT NULL,
  tokens_out INTEGER NOT NULL,
  cost_usd REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_cost_session ON cost_ledger(session_id);
"""


class CostLedger:
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
        return con

    def record(
        self,
        *,
        request_id: str,
        role: str,
        model: str,
        tokens_in: int,
        tokens_out: int,
        cost_usd: float,
        session_id: str = "",
        user_id: str = "",
    ) -> None:
        with self._lock, self._connect() as con:
            con.execute(
                "INSERT OR IGNORE INTO cost_ledger "
                "(request_id, ts, session_id, user_id, role, model, tokens_in, tokens_out, cost_usd) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (request_id, int(time.time()), session_id, user_id, role, model, tokens_in, tokens_out, cost_usd),
            )

    def session_total(self, session_id: str) -> tuple[float, int]:
        with self._connect() as con:
            row = con.execute(
                "SELECT COALESCE(SUM(cost_usd), 0), COUNT(*) FROM cost_ledger WHERE session_id = ?",
                (session_id,),
            ).fetchone()
        return float(row[0]), int(row[1])

    def grand_total(self) -> float:
        with self._connect() as con:
            row = con.execute("SELECT COALESCE(SUM(cost_usd), 0) FROM cost_ledger").fetchone()
        return float(row[0])
