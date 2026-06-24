"""题型门卫：宽进，只拦高置信的纯查事实题（审查）。

任何解析/调用失败 → fail-open 放行为 decision（门卫是体验优化不是安全门）。
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

from .config import ModelSpec
from .divergence import extract_json
from .llm import LLMClient

logger = logging.getLogger(__name__)

GATE_SYSTEM = """你是"对练场"的入口分诊员。判断用户输入是否适合决策对练。

适合（decision）：取舍、选择、要不要做X、方向判断——包括含事实成分但本质是权衡的问题（如"该不该进美国市场"）。
不适合（factual）：纯查事实/查资料/查定义/查数据，对练不出东西。

宽进原则：拿不准就放行（decision）。只有高置信的纯查事实才标 factual。

严格输出 JSON：
{
  "classification": "decision 或 factual",
  "decision_axis": "这个决策真正要权衡的轴，一句话（factual 时留空）",
  "confidence": 0.0到1.0,
  "rewrite_suggestion": "若 factual：把它改写成取舍题的建议，一句话（decision 时留空）"
}"""


@dataclass
class GateResult:
    classification: str  # decision | factual
    decision_axis: str = ""
    confidence: float = 0.5
    rewrite_suggestion: str = ""

    @property
    def passed(self) -> bool:
        # 宽进：仅高置信 factual 才拦
        return not (self.classification == "factual" and self.confidence >= 0.7)


async def precheck(
    client: LLMClient,
    spec: ModelSpec,
    question: str,
    *,
    session_id: str = "",
    user_id: str = "",
) -> GateResult:
    try:
        result = await client.call(
            spec,
            GATE_SYSTEM,
            question,
            role="gate",
            session_id=session_id,
            user_id=user_id,
            retryable=True,
            max_tokens=800,  # 思考开销余量（M3.1，同 closure）
        )
        data = extract_json(result.text)
        cls = data.get("classification", "decision")
        if cls not in ("decision", "factual"):
            cls = "decision"
        try:
            conf = min(1.0, max(0.0, float(data.get("confidence", 0.5))))
        except (TypeError, ValueError):
            conf = 0.5
        return GateResult(
            classification=cls,
            decision_axis=str(data.get("decision_axis", "")).strip(),
            confidence=conf,
            rewrite_suggestion=str(data.get("rewrite_suggestion", "")).strip(),
        )
    except Exception as e:
        logger.warning("门卫失败（%s），fail-open 放行", e)
        return GateResult(classification="decision", confidence=0.0)
