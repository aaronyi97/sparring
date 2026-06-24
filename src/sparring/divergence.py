"""分歧地图：多位顾问回答 → 结构化共识/分歧 JSON（决策化语义）。"""
from __future__ import annotations

import json
import logging

from .config import ModelSpec
from .domain import DivergenceMap, DivergencePoint, DivergencePosition, ModelAnswer
from .llm import LLMClient

logger = logging.getLogger(__name__)

# 决策化改写自 上游 divergence_analyzer：difficulty"有明确对错"语义
# 改为 decision_ambiguity 三档；显式允许"同立场不同理由"软分歧
DIVERGENCE_SYSTEM = """你是决策分歧分析师。多位顾问刚刚对同一个决策给出了独立建议，你要产出一张"分歧地图"。

严格输出 JSON（不要输出 JSON 之外的任何文字）：
{
  "consensus_points": ["顾问们都认同的点（没有就给空数组）"],
  "divergence_points": [
    {
      "id": "dp1",
      "topic": "分歧主题短语",
      "description": "他们在争什么（1-2 句）",
      "positions": [
        {"stance": "立场短句", "summary": "该立场的核心理由（1-2 句）", "models": ["顾问A"]}
      ],
      "decision_ambiguity": "factual | tradeoff | values 三选一",
      "salience": 0.0到1.0
    }
  ],
  "overall_consensus_score": 0.0到1.0
}

规则：
1. decision_ambiguity 判定：factual=查证事实即可分高下；tradeoff=利弊权衡、没有免费午餐；values=取决于价值观/偏好/风险胃口
2. 允许"软分歧"：立场相同但理由或路径不同，也算分歧点（positions 写不同理由，标 tradeoff）
3. 顾问真一致就诚实报告共识，不强行制造分歧
4. 每个分歧点 positions 至少 2 个
5. salience 表示该分歧对最终决策的影响大小
6. overall_consensus_score：0=完全分裂，1=完全一致"""


def extract_json(text: str) -> dict:
    """从模型输出提取 JSON：剥代码围栏，取首个 { 到末个 }。解析失败抛 ValueError。"""
    s = text.strip()
    if s.startswith("```"):
        lines = s.splitlines()
        lines = [ln for ln in lines if not ln.strip().startswith("```")]
        s = "\n".join(lines)
    start, end = s.find("{"), s.rfind("}")
    if start == -1 or end <= start:
        raise ValueError("输出中找不到 JSON 对象")
    return json.loads(s[start : end + 1])


def parse_map(data: dict) -> DivergenceMap:
    """校验 + 清洗：positions < 2 的分歧点丢弃；字段缺失给安全默认。"""
    points: list[DivergencePoint] = []
    for i, p in enumerate(data.get("divergence_points", [])):
        positions = [
            DivergencePosition(
                stance=str(pos.get("stance", "")).strip(),
                summary=str(pos.get("summary", "")).strip(),
                models=[str(m) for m in pos.get("models", [])],
            )
            for pos in p.get("positions", [])
            if str(pos.get("stance", "")).strip()
        ]
        if len(positions) < 2:
            continue
        ambiguity = p.get("decision_ambiguity", "tradeoff")
        if ambiguity not in ("factual", "tradeoff", "values"):
            ambiguity = "tradeoff"
        try:
            salience = min(1.0, max(0.0, float(p.get("salience", 0.5))))
        except (TypeError, ValueError):
            salience = 0.5
        points.append(
            DivergencePoint(
                id=str(p.get("id") or f"dp{i + 1}"),
                topic=str(p.get("topic", "")).strip() or "未命名分歧",
                description=str(p.get("description", "")).strip(),
                positions=positions,
                decision_ambiguity=ambiguity,
                salience=salience,
            )
        )
    try:
        score = min(1.0, max(0.0, float(data.get("overall_consensus_score", 0.5))))
    except (TypeError, ValueError):
        score = 0.5
    return DivergenceMap(
        consensus_points=[str(c) for c in data.get("consensus_points", [])],
        divergence_points=points,
        overall_consensus_score=score,
    )


def fallback_map(answers: list[ModelAnswer]) -> DivergenceMap:
    """分析失败的兜底：各顾问首句当立场，凑一张最小可用地图（标 fallback）。"""
    positions = []
    for i, a in enumerate(answers):
        first_line = next((ln.strip() for ln in a.text.splitlines() if ln.strip()), a.text[:80])
        positions.append(
            DivergencePosition(stance=first_line[:60], summary=first_line[:160], models=[f"顾问{chr(65 + i)}"])
        )
    point = DivergencePoint(
        id="dp1",
        topic="顾问们的建议差异",
        description="自动分析未成功，以下为各顾问的开头立场原文，供对练参考。",
        positions=positions,
        decision_ambiguity="tradeoff",
        salience=0.8,
    )
    return DivergenceMap(
        consensus_points=[],
        divergence_points=[point] if len(positions) >= 2 else [],
        overall_consensus_score=0.5,
        fallback=True,
    )


def _answers_block(question: str, answers: list[ModelAnswer]) -> str:
    parts = [f"# 决策问题\n{question}\n"]
    for i, a in enumerate(answers):
        parts.append(f"# 顾问{chr(65 + i)}（{a.model_name}）的建议\n{a.text}\n")
    return "\n".join(parts)


async def analyze(
    client: LLMClient,
    spec: ModelSpec,
    question: str,
    answers: list[ModelAnswer],
    *,
    session_id: str = "",
    user_id: str = "",
) -> DivergenceMap:
    try:
        result = await client.call(
            spec,
            DIVERGENCE_SYSTEM,
            _answers_block(question, answers),
            role="divergence",
            session_id=session_id,
            user_id=user_id,
            retryable=True,
        )
        dmap = parse_map(extract_json(result.text))
        if not dmap.divergence_points:
            # 全被清洗掉 = 等同失败，用兜底图保证对练有燃料
            logger.warning("分歧地图解析后为空，启用 fallback")
            return fallback_map(answers)
        return dmap
    except Exception as e:
        logger.warning("分歧分析失败（%s），启用 fallback", e)
        return fallback_map(answers)
