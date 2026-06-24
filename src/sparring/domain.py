"""领域数据结构：顾问回答 / 分歧地图。"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field

# 决策歧义度阶梯（替代 上游 的"有明确对错"语义）
# factual=查证事实即可分高下 · tradeoff=利弊权衡 · values=价值观/风险胃口取舍
AMBIGUITY_ORDER = {"factual": 0, "tradeoff": 1, "values": 2}


@dataclass
class ModelAnswer:
    model_key: str
    model_name: str
    text: str
    latency_ms: int = 0
    tokens_in: int = 0
    tokens_out: int = 0
    cost_usd: float = 0.0
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.error is None and bool(self.text.strip())


@dataclass
class DivergencePosition:
    stance: str
    summary: str
    models: list[str] = field(default_factory=list)


@dataclass
class DivergencePoint:
    id: str
    topic: str
    description: str
    positions: list[DivergencePosition]
    decision_ambiguity: str = "tradeoff"
    salience: float = 0.5


@dataclass
class DivergenceMap:
    consensus_points: list[str]
    divergence_points: list[DivergencePoint]
    overall_consensus_score: float
    fallback: bool = False

    def to_dict(self) -> dict:
        return asdict(self)

    @staticmethod
    def from_dict(d: dict) -> "DivergenceMap":
        points = [
            DivergencePoint(
                id=p["id"],
                topic=p["topic"],
                description=p.get("description", ""),
                positions=[DivergencePosition(**pos) for pos in p.get("positions", [])],
                decision_ambiguity=p.get("decision_ambiguity", "tradeoff"),
                salience=float(p.get("salience", 0.5)),
            )
            for p in d.get("divergence_points", [])
        ]
        return DivergenceMap(
            consensus_points=list(d.get("consensus_points", [])),
            divergence_points=points,
            overall_consensus_score=float(d.get("overall_consensus_score", 0.5)),
            fallback=bool(d.get("fallback", False)),
        )

    def sorted_points(self) -> list[DivergencePoint]:
        """脚手架顺序：先聊事实可查的，再聊权衡，最后聊价值观（易→难）；同档按影响力降序。"""
        return sorted(
            self.divergence_points,
            key=lambda p: (AMBIGUITY_ORDER.get(p.decision_ambiguity, 1), -p.salience),
        )
