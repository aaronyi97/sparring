"""分歧地图解析的高风险路径：围栏剥离 / 脏输出 / 半合法数据清洗 / 兜底图。"""
import json

import pytest

from sparring.divergence import extract_json, fallback_map, parse_map
from sparring.domain import ModelAnswer

GOOD = {
    "consensus_points": ["都认为要先验证需求"],
    "divergence_points": [
        {
            "id": "dp1",
            "topic": "时机",
            "description": "现在做还是半年后做",
            "positions": [
                {"stance": "现在做", "summary": "窗口期", "models": ["顾问A"]},
                {"stance": "半年后", "summary": "先攒势能", "models": ["顾问B"]},
            ],
            "decision_ambiguity": "tradeoff",
            "salience": 0.9,
        }
    ],
    "overall_consensus_score": 0.4,
}


def test_extract_json_plain():
    assert extract_json(json.dumps(GOOD, ensure_ascii=False))["overall_consensus_score"] == 0.4


def test_extract_json_fenced():
    text = "```json\n" + json.dumps(GOOD, ensure_ascii=False) + "\n```"
    assert extract_json(text)["divergence_points"][0]["id"] == "dp1"


def test_extract_json_with_prose_around():
    text = "好的，以下是分析：\n" + json.dumps(GOOD, ensure_ascii=False) + "\n以上。"
    assert len(extract_json(text)["divergence_points"]) == 1


def test_extract_json_garbage_raises():
    with pytest.raises(ValueError):
        extract_json("完全没有 JSON 的输出")


def test_parse_drops_single_position_points():
    data = json.loads(json.dumps(GOOD))
    data["divergence_points"].append(
        {
            "id": "dp2",
            "topic": "孤立场",
            "positions": [{"stance": "只有一方", "summary": "x", "models": []}],
        }
    )
    dmap = parse_map(data)
    assert [p.id for p in dmap.divergence_points] == ["dp1"]


def test_parse_clamps_and_defaults():
    data = json.loads(json.dumps(GOOD))
    data["divergence_points"][0]["decision_ambiguity"] = "weird"
    data["divergence_points"][0]["salience"] = 9
    data["overall_consensus_score"] = -3
    dmap = parse_map(data)
    assert dmap.divergence_points[0].decision_ambiguity == "tradeoff"
    assert dmap.divergence_points[0].salience == 1.0
    assert dmap.overall_consensus_score == 0.0


def test_sorted_points_factual_first():
    data = json.loads(json.dumps(GOOD))
    data["divergence_points"].append(
        {
            "id": "dp2",
            "topic": "事实可查",
            "positions": [
                {"stance": "甲", "summary": "a", "models": []},
                {"stance": "乙", "summary": "b", "models": []},
            ],
            "decision_ambiguity": "factual",
            "salience": 0.1,
        }
    )
    dmap = parse_map(data)
    assert dmap.sorted_points()[0].id == "dp2"  # factual 先于 tradeoff，salience 不翻档


def test_fallback_map_from_answers():
    answers = [
        ModelAnswer("a", "model-a", "建议现在做。\n理由……"),
        ModelAnswer("b", "model-b", "建议等一等。\n理由……"),
    ]
    dmap = fallback_map(answers)
    assert dmap.fallback is True
    assert len(dmap.divergence_points) == 1
    assert len(dmap.divergence_points[0].positions) == 2
