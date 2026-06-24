"""独立收口判定器：每轮用户回复后、教练生成前跑（审查）。

与教练分离的原因：让同一次生成既出引导问题又自判收口，会自证"继续问"或过早"想通"。
判定失败 → fail-open 为 continue（对练继续，不因判定器挂掉卡死流程）。
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

from .config import ModelSpec
from .divergence import extract_json
from .llm import LLMClient
from .store import Session, Turn

logger = logging.getLogger(__name__)

CLOSURE_SYSTEM = """你是决策对练的收口判定器。根据用户最新回复，独立判断对话应走向哪里。你不是教练，只做判定。

严格输出 JSON：
{
  "closure": "settled | stuck | shifted | continue",
  "stuck_reason": "none | ambiguity | low_confidence | repeated_answer | no_progress | too_hard",
  "stance_summary": "用户当前立场一句话（看不出来就留空）",
  "basis": "判定依据一句话，引用户原话片段"
}

判定标准：
- settled：用户已形成有理由支撑的明确立场，且能回应主要反方观点（不要求和任何顾问一致）
- shifted：用户立场相比之前发生实质改变（这个时刻值得记录）
- stuck：原地打转 / 明显敷衍 / 表达想不下去 / 连续重复同样的话
- continue：正常推进中
- stuck_reason 仅在 stuck 时非 none：no_progress=原地打转；too_hard=当前分歧对用户太难；
  repeated_answer=重复作答；ambiguity=表达含混；low_confidence=犹豫不决"""

VALID_CLOSURE = {"settled", "stuck", "shifted", "continue"}
VALID_STUCK = {"none", "ambiguity", "low_confidence", "repeated_answer", "no_progress", "too_hard"}


@dataclass
class ClosureResult:
    closure: str = "continue"
    stuck_reason: str = "none"
    stance_summary: str = ""
    basis: str = ""


def _recent_dialogue(session: Session, latest_user_text: str, max_turns: int = 6) -> str:
    lines = []
    for t in session.turns[-max_turns:]:
        role = "用户" if (t.role if isinstance(t, Turn) else t.get("role")) == "user" else "教练"
        text = t.text if isinstance(t, Turn) else t.get("text", "")
        lines.append(f"{role}：{text}")
    lines.append(f"用户（最新回复）：{latest_user_text}")
    return "\n".join(lines)


async def classify(
    client: LLMClient,
    spec: ModelSpec,
    session: Session,
    latest_user_text: str,
    current_dp_topic: str,
    *,
    user_id: str = "",
) -> ClosureResult:
    user_block = (
        f"# 决策问题\n{session.question}\n\n"
        f"# 当前聚焦分歧\n{current_dp_topic or '（无）'}\n\n"
        f"# 对话\n{_recent_dialogue(session, latest_user_text)}"
    )
    try:
        result = await client.call(
            spec,
            CLOSURE_SYSTEM,
            user_block,
            role="closure",
            session_id=session.id,
            user_id=user_id,
            retryable=True,
            max_tokens=800,  # gpt-5.4 low 也有思考开销，给截断留余量（M3.1）
        )
        data = extract_json(result.text)
        closure = data.get("closure", "continue")
        if closure not in VALID_CLOSURE:
            closure = "continue"
        stuck = data.get("stuck_reason", "none")
        if stuck not in VALID_STUCK or closure != "stuck":
            stuck = "none" if closure != "stuck" else "no_progress"
        stance_summary = str(data.get("stance_summary", "")).strip()
        if closure in ("settled", "shifted") and not stance_summary:
            # 审查：说不出立场是什么的"想通/转向"不可信，降级继续（防过早收口）
            closure, stuck = "continue", "none"
        return ClosureResult(
            closure=closure,
            stuck_reason=stuck,
            stance_summary=stance_summary,
            basis=str(data.get("basis", "")).strip(),
        )
    except Exception as e:
        logger.warning("收口判定失败（%s），fail-open=continue", e)
        return ClosureResult()
