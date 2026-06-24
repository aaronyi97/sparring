"""决策备忘录：一场对练的可带走产物。"""
from __future__ import annotations

import time

from .domain import DivergenceMap
from .store import Session, Turn


def build_memo(session: Session, dmap: DivergenceMap | None) -> str:
    date = time.strftime("%Y-%m-%d", time.localtime(session.updated_at or time.time()))
    lines = [
        f"# 决策备忘录 · {date}",
        "",
        "## 我的决策问题",
        session.question,
        "",
        "## 我的立场",
        f"**{session.user_stance or '（未表态）'}**",
    ]
    if session.stance_reason:
        lines += ["", f"理由：{session.stance_reason}"]

    if dmap:
        if dmap.consensus_points:
            lines += ["", "## 顾问共识"] + [f"- {c}" for c in dmap.consensus_points]
        if dmap.divergence_points:
            lines += ["", "## 关键分歧", "", "| 分歧 | 一方 | 另一方 | 性质 |", "|---|---|---|---|"]
            for p in dmap.sorted_points():
                a = p.positions[0].stance if p.positions else ""
                b = p.positions[1].stance if len(p.positions) > 1 else ""
                kind = {"factual": "可查证", "tradeoff": "利弊权衡", "values": "价值取舍"}.get(
                    p.decision_ambiguity, p.decision_ambiguity
                )
                lines.append(f"| {p.topic} | {a} | {b} | {kind} |")

    comp = session.comparison or {}
    if comp.get("you_saw"):
        lines += ["", "## 我看到了顾问没看到的"] + [f"- {x}" for x in comp["you_saw"]]
    if comp.get("you_missed"):
        lines += ["", "## 我可能漏掉的"] + [f"- {x}" for x in comp["you_missed"]]
    if comp.get("key_difference"):
        lines += ["", "## 我与综合建议的关键差异", comp["key_difference"]]
    if comp.get("next_checks"):
        lines += ["", "## 落地前先验证"] + [f"- {x}" for x in comp["next_checks"]]

    user_rounds = sum(
        1 for t in session.turns if (t.role if isinstance(t, Turn) else t.get("role")) == "user"
    )
    lines += ["", "---", f"*对练 {user_rounds} 轮 · 由对练场生成*"]
    return "\n".join(lines)
