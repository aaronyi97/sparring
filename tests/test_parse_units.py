"""门卫/收口/教练/备忘录的解析与兜底路径（不打真实 API）。"""
import asyncio
import json
from dataclasses import dataclass, field

import pytest

from sparring import closure as closure_mod
from sparring import coach as coach_mod
from sparring import gate as gate_mod
from sparring.divergence import parse_map
from sparring.llm import LLMResult
from sparring.memo import build_memo
from sparring.store import Session, Turn

DMAP_DATA = {
    "consensus_points": ["都认为要先验证"],
    "divergence_points": [
        {
            "id": "dp1", "topic": "现在做还是等", "description": "时机之争",
            "positions": [
                {"stance": "现在做", "summary": "窗口", "models": ["A"]},
                {"stance": "等半年", "summary": "势能", "models": ["B"]},
            ],
            "decision_ambiguity": "tradeoff", "salience": 0.9,
        }
    ],
    "overall_consensus_score": 0.4,
}


@dataclass
class FakeSpec:
    key: str = "fake"
    model_name: str = "fake-model"


@dataclass
class FakeClient:
    reply: str = ""
    fail: bool = False
    calls: list = field(default_factory=list)

    async def call(self, spec, system, user, **kw):
        self.calls.append({"system": system, "user": user, **kw})
        if self.fail:
            raise RuntimeError("boom")
        return LLMResult(
            text=self.reply, model_key="fake", model_name="fake-model",
            tokens_in=10, tokens_out=10, cost_usd=0.001, latency_ms=5,
        )


def _run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


def test_gate_parse_and_strict_factual_blocked():
    c = FakeClient(reply=json.dumps({
        "classification": "factual", "decision_axis": "", "confidence": 0.9,
        "rewrite_suggestion": "改成：现在的预算下值不值得买X？",
    }, ensure_ascii=False))
    g = _run(gate_mod.precheck(c, FakeSpec(), "X的参数是多少"))
    assert g.passed is False and g.rewrite_suggestion


def test_gate_low_confidence_factual_passes():
    c = FakeClient(reply=json.dumps({"classification": "factual", "confidence": 0.5}))
    assert _run(gate_mod.precheck(c, FakeSpec(), "q")).passed is True  # 宽进


def test_gate_failure_fails_open():
    g = _run(gate_mod.precheck(FakeClient(fail=True), FakeSpec(), "q"))
    assert g.passed is True and g.classification == "decision"


def _session():
    s = Session(id="s1", owner_id="u1", question="要不要做独立产品")
    s.turns = [Turn(role="coach", text="第一问")]
    return s


def test_closure_parse_valid():
    c = FakeClient(reply=json.dumps({
        "closure": "stuck", "stuck_reason": "too_hard",
        "stance_summary": "", "basis": "用户说'不知道'",
    }, ensure_ascii=False))
    r = _run(closure_mod.classify(c, FakeSpec(), _session(), "不知道", "时机"))
    assert r.closure == "stuck" and r.stuck_reason == "too_hard"


def test_closure_invalid_and_failure_default_continue():
    r1 = _run(closure_mod.classify(FakeClient(reply="不是JSON"), FakeSpec(), _session(), "x", ""))
    r2 = _run(closure_mod.classify(FakeClient(fail=True), FakeSpec(), _session(), "x", ""))
    assert r1.closure == "continue" and r2.closure == "continue"


def test_coach_parse_and_track_clamp():
    c = FakeClient(reply=json.dumps({
        "guide_message": "你更怕哪种后悔？", "target_divergence_id": "dp1", "track": "Z",
    }, ensure_ascii=False))
    dmap = parse_map(json.loads(json.dumps(DMAP_DATA)))
    r = _run(coach_mod.guide(c, FakeSpec(), _session(), dmap))
    assert r.message and r.track == "A" and r.fallback is False


def test_coach_failure_template_fallback_uses_first_dp():
    dmap = parse_map(json.loads(json.dumps(DMAP_DATA)))
    r = _run(coach_mod.guide(FakeClient(fail=True), FakeSpec(), _session(), dmap))
    assert r.fallback is True and r.target_dp_id == "dp1" and "现在做还是等" in r.message


def test_fallback_never_repeats_last_coach_message():
    """M3.1 防复读：Owner 实测抓到模板把同一问题原样问两遍。"""
    dmap = parse_map(json.loads(json.dumps(DMAP_DATA)))
    first = coach_mod._fallback_reply(dmap, first_round=False)
    again = coach_mod._fallback_reply(
        dmap, first_round=False,
        recent_coach_texts=[first.message], last_user_text="更长试水期，我还没完全准备好",
    )
    assert again.message != first.message
    assert "更长试水期" in again.message  # 引用户原话，不是干巴巴换个问法
    assert again.track == "B"


def test_fallback_blocks_aba_alternating_repeat():
    """审查：A-B-A 隔轮复读也要拦——对比最近 3 条，三级变体链全不相同。"""
    dmap = parse_map(json.loads(json.dumps(DMAP_DATA)))
    r1 = coach_mod._fallback_reply(dmap, first_round=False)
    r2 = coach_mod._fallback_reply(dmap, False, recent_coach_texts=[r1.message], last_user_text="嗯")
    r3 = coach_mod._fallback_reply(dmap, False, recent_coach_texts=[r1.message, r2.message])
    r4 = coach_mod._fallback_reply(dmap, False, recent_coach_texts=[r1.message, r2.message, r3.message])
    msgs = {r1.message, r2.message, r3.message, r4.message}
    assert len(msgs) == 4  # 四连兜底无一重复
    r5 = coach_mod._fallback_reply(dmap, False, recent_coach_texts=list(msgs))
    assert "立场卡" in r5.message  # 变体链耗尽后邀请进立场卡，不空转


def test_closure_settled_without_stance_summary_downgrades():
    """审查：说不出立场的'想通'不可信，降级 continue 防过早收口。"""
    c = FakeClient(reply=json.dumps({
        "closure": "settled", "stuck_reason": "none", "stance_summary": "", "basis": "好像想通了",
    }, ensure_ascii=False))
    r = _run(closure_mod.classify(c, FakeSpec(), _session(), "就这样吧", "时机"))
    assert r.closure == "continue"


def test_fallback_no_double_period_in_consensus_prefix():
    dmap = parse_map(json.loads(json.dumps(DMAP_DATA)))
    r = coach_mod._fallback_reply(dmap, first_round=True)
    assert "。。" not in r.message


def test_memo_contains_core_sections():
    s = _session()
    s.user_stance = "先做内容，功能后置"
    s.stance_reason = "精力有限"
    s.comparison = {
        "you_saw": ["渠道协同"], "you_missed": ["维护成本"],
        "key_difference": "比综合建议更保守", "next_checks": ["问10个读者"],
        "blind_spot_pattern": "",
    }
    s.turns.append(Turn(role="user", text="我想先做内容"))
    md = build_memo(s, parse_map(json.loads(json.dumps(DMAP_DATA))))
    for section in ["决策备忘录", "我的立场", "先做内容，功能后置", "关键分歧", "我可能漏掉的", "落地前先验证"]:
        assert section in md
