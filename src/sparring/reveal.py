"""揭示：后台综合 + 立场对照（对照揭示）。

综合 = 单模型调用（不带 上游 LLMJudge 体系）；对照 = 用户立场卡 vs 综合的逐点 diff。
"""
from __future__ import annotations

import logging

from .config import ModelSpec
from .divergence import extract_json
from .domain import DivergenceMap, ModelAnswer
from .llm import LLMClient

logger = logging.getLogger(__name__)

SYNTHESIS_SYSTEM = """你是决策综合者。把多位顾问的建议和分歧地图，综合成一份对用户最有用的完整建议。

输出 markdown，结构固定：
## 综合建议
开头一句话亮明：如果必须现在选，更稳妥的是哪个方向。
## 关键条件分支
"如果你的情况是X → 选甲；如果是Y → 选乙"，最多 3 条。每个分支内要有明确倾向。
## 主要理由
最多 3 条，吸收各方最硬的论点，标注来自哪位顾问。
## 这个建议的最大风险
1-2 条，诚实。

不和稀泥。全文 500 字内。"""

COMPARISON_SYSTEM = """你是对照分析师。用户刚在决策对练中亮明了立场，现在对照"顾问综合建议"做复盘。

严格输出 JSON：
{
  "you_saw": ["用户立场/理由里顾问们没覆盖到的独到点（没有就给空数组，不要硬夸）"],
  "you_missed": ["顾问们提了、但用户立场和理由始终没处理的点（最多3条，按重要性排）"],
  "key_difference": "用户立场与综合建议的最关键差异，一句话（一致就说一致在哪）",
  "blind_spot_pattern": "从整场对话看到的一个思维倾向，行为描述不打分，一句话（没有就留空）",
  "next_checks": ["落地前值得先验证的事（最多3条）"]
}

诚实优先：you_saw 宁缺毋滥；you_missed 必须引用户真没接住的具体点。"""


def _answers_block(answers: list[dict | ModelAnswer]) -> str:
    parts = []
    for i, a in enumerate(answers):
        name = a.model_name if isinstance(a, ModelAnswer) else a.get("model_name", f"顾问{i + 1}")
        text = a.text if isinstance(a, ModelAnswer) else a.get("text", "")
        parts.append(f"## 顾问{chr(65 + i)}（{name}）\n{text}")
    return "\n\n".join(parts)


async def synthesize(
    client: LLMClient,
    spec: ModelSpec,
    question: str,
    answers: list[dict | ModelAnswer],
    dmap: DivergenceMap,
    *,
    session_id: str = "",
    user_id: str = "",
) -> str:
    user_block = (
        f"# 决策问题\n{question}\n\n# 顾问建议\n{_answers_block(answers)}\n\n"
        f"# 分歧地图摘要\n共识：{'；'.join(dmap.consensus_points) or '无'}\n"
        f"分歧：{'；'.join(p.topic for p in dmap.divergence_points) or '无'}"
    )
    result = await client.call(
        spec, SYNTHESIS_SYSTEM, user_block,
        role="synthesis", session_id=session_id, user_id=user_id,
        retryable=True, max_tokens=1200,
    )
    return result.text.strip()


async def compare(
    client: LLMClient,
    spec: ModelSpec,
    question: str,
    user_stance: str,
    stance_reason: str,
    synthesis_md: str,
    dialogue_digest: str,
    *,
    session_id: str = "",
    user_id: str = "",
) -> dict:
    user_block = (
        f"# 决策问题\n{question}\n\n"
        f"# 用户立场卡\n立场：{user_stance}\n理由：{stance_reason or '（未给理由）'}\n\n"
        f"# 顾问综合建议\n{synthesis_md}\n\n"
        f"# 对练过程摘录\n{dialogue_digest}"
    )
    try:
        result = await client.call(
            spec, COMPARISON_SYSTEM, user_block,
            role="comparison", session_id=session_id, user_id=user_id,
            retryable=True, max_tokens=700,
        )
        data = extract_json(result.text)
        return {
            "you_saw": [str(x) for x in data.get("you_saw", [])][:3],
            "you_missed": [str(x) for x in data.get("you_missed", [])][:3],
            "key_difference": str(data.get("key_difference", "")).strip(),
            "blind_spot_pattern": str(data.get("blind_spot_pattern", "")).strip(),
            "next_checks": [str(x) for x in data.get("next_checks", [])][:3],
        }
    except Exception as e:
        logger.warning("对照生成失败（%s），降级为保守对照", e)
        # 审查：fail-soft 不能让备忘录空着"落地验证"——给保守通用检查项并显式标记
        return {
            "you_saw": [],
            "you_missed": [],
            "key_difference": "对照分析生成失败，请以上方综合建议为准。",
            "blind_spot_pattern": "",
            "next_checks": [
                "把综合建议里的条件分支逐条对照你的真实情况，确认自己落在哪个分支",
                "给你的立场找一条最强的反面证据，扛得住再定",
            ],
            "comparison_fallback": True,
        }
