"""画像观察（M4 · 跨会话画像）。

定位：用户可见层只做定性陈述（无 0-1 评分）；内部结构化存储（dimension /
behavior_tag / evidence_quote / confidence），按 behavior_tag 累积 repeat_count，
让跨会话能聚合出稳定模式而非散文日志（审查）。consent + 一键删除。
"""
from __future__ import annotations

import json
import logging
import sqlite3
import threading
import time
import uuid
from pathlib import Path

from .config import ModelSpec
from .divergence import extract_json
from .llm import LLMClient
from .store import Session, Turn

logger = logging.getLogger(__name__)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS observations (
  id TEXT PRIMARY KEY,
  owner_id TEXT NOT NULL,
  session_id TEXT NOT NULL,
  dimension TEXT NOT NULL,
  behavior_tag TEXT NOT NULL,
  evidence_quote TEXT NOT NULL DEFAULT '',
  confidence REAL NOT NULL DEFAULT 0.5,
  created_at INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_obs_owner ON observations(owner_id, created_at DESC);
"""

OBSERVE_SYSTEM = """你是决策对练的观察员。根据用户这一局的表现，记录 1-3 条关于其思维方式的客观观察。
你不打分，不评判好坏，只描述"做了什么"，像教练记训练笔记。

严格输出 JSON：
{
  "observations": [
    {
      "dimension": "推理深度 | 多面性 | 证据意识 | 反例接纳 | 锚定倾向 | 决断力 之一",
      "behavior_tag": "可跨会话聚合的稳定标签，2-6字短语，如'回避成本'、'先肯定再质疑'、'依赖第一直觉'",
      "evidence_quote": "用户原话里支撑这条观察的片段（引述，不超过40字）",
      "confidence": 0.0到1.0
    }
  ]
}

规则：
1. behavior_tag 要可复用——同一种行为在不同决策里应能落到同一个 tag，便于累积
2. 只记这一局真实出现的，宁少勿凑
3. evidence_quote 必须是用户真说过的话，不能编
4. 不写"优秀/糟糕/应该"，只写中性的行为描述"""


class ObservationStore:
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

    def add_many(self, owner_id: str, session_id: str, observations: list[dict]) -> int:
        if not owner_id or not observations:
            return 0
        now = int(time.time())
        rows = [
            (
                uuid.uuid4().hex,
                owner_id,
                session_id,
                str(o.get("dimension", "")).strip()[:20] or "其他",
                str(o.get("behavior_tag", "")).strip()[:30],
                str(o.get("evidence_quote", "")).strip()[:120],
                _clamp(o.get("confidence", 0.5)),
                now,
            )
            for o in observations
            if str(o.get("behavior_tag", "")).strip()
        ]
        if not rows:
            return 0
        with self._lock, self._connect() as con:
            con.executemany(
                "INSERT INTO observations "
                "(id, owner_id, session_id, dimension, behavior_tag, evidence_quote, confidence, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                rows,
            )
        return len(rows)

    def aggregate(self, owner_id: str) -> list[dict]:
        """按 behavior_tag 聚合跨会话模式：repeat_count 降序，带最近一次证据。"""
        if not owner_id:
            return []
        with self._connect() as con:
            rows = con.execute(
                "SELECT behavior_tag, dimension, COUNT(*) AS repeat_count, MAX(created_at) AS last_seen, "
                "(SELECT evidence_quote FROM observations o2 WHERE o2.owner_id = o1.owner_id "
                " AND o2.behavior_tag = o1.behavior_tag ORDER BY created_at DESC, rowid DESC LIMIT 1) AS recent_evidence "
                "FROM observations o1 WHERE owner_id = ? "
                "GROUP BY behavior_tag ORDER BY repeat_count DESC, last_seen DESC",
                (owner_id,),
            ).fetchall()
        return [dict(r) for r in rows]

    def delete_all(self, owner_id: str) -> int:
        if not owner_id:
            return 0
        with self._lock, self._connect() as con:
            cur = con.execute("DELETE FROM observations WHERE owner_id = ?", (owner_id,))
            return cur.rowcount


def _clamp(v) -> float:
    try:
        return min(1.0, max(0.0, float(v)))
    except (TypeError, ValueError):
        return 0.5


def _dialogue_digest(session: Session, max_turns: int = 12) -> str:
    lines = []
    for t in session.turns[-max_turns:]:
        role = t.role if isinstance(t, Turn) else t.get("role")
        text = t.text if isinstance(t, Turn) else t.get("text", "")
        lines.append(f"{'用户' if role == 'user' else '教练'}：{text}")
    return "\n".join(lines)


async def generate(
    client: LLMClient,
    spec: ModelSpec,
    session: Session,
    *,
    owner_id: str = "",
) -> list[dict]:
    """从一局对练生成结构化观察；fail-soft 返回空列表（画像缺一局无伤大雅）。"""
    comp = session.comparison or {}
    user_block = (
        f"# 决策问题\n{session.question}\n\n"
        f"# 用户立场\n{session.user_stance}\n理由：{session.stance_reason or '（未给）'}\n\n"
        f"# 对练过程\n{_dialogue_digest(session)}\n\n"
        f"# 揭示时发现用户漏掉的点\n{'；'.join(comp.get('you_missed', [])) or '（无）'}"
    )
    try:
        result = await client.call(
            spec, OBSERVE_SYSTEM, user_block,
            role="observe", session_id=session.id, user_id=owner_id,
            retryable=True, max_tokens=800,
        )
        data = extract_json(result.text)
        obs = data.get("observations", [])
        return obs if isinstance(obs, list) else []
    except Exception as e:
        logger.warning("观察生成失败（%s），本局跳过", e)
        return []
