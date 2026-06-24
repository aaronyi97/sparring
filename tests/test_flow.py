"""编排器全链路（FakeClient 按角色出戏，不打真实 API）：
start→respond(continue)→respond(settled)→stance→reveal→memo，外加立场卡硬门与越权。"""
import asyncio
import json
from dataclasses import dataclass, field

import pytest

from sparring.config import AppConfig
from sparring.costs import CostLedger
from sparring.fanout import FanoutError
from sparring.flow import NotFound, SparringFlow, StanceRequired, WrongState
from sparring.llm import LLMResult
from sparring.store import SessionStore

DMAP_JSON = json.dumps({
    "consensus_points": ["先小成本验证"],
    "divergence_points": [{
        "id": "dp1", "topic": "并行还是专注", "description": "精力分配之争",
        "positions": [
            {"stance": "并行", "summary": "互补", "models": ["顾问A"]},
            {"stance": "专注", "summary": "稀释", "models": ["顾问B"]},
        ],
        "decision_ambiguity": "tradeoff", "salience": 0.9,
    }],
    "overall_consensus_score": 0.3,
}, ensure_ascii=False)

COACH_JSON = json.dumps({"guide_message": "哪边的代价你更付得起？", "target_divergence_id": "dp1", "track": "A"}, ensure_ascii=False)
CONTINUE_JSON = json.dumps({"closure": "continue", "stuck_reason": "none", "stance_summary": "", "basis": "正常推进"}, ensure_ascii=False)
SETTLED_JSON = json.dumps({"closure": "settled", "stuck_reason": "none", "stance_summary": "专注内容", "basis": "用户给出了完整论证"}, ensure_ascii=False)
COMPARISON_JSON = json.dumps({
    "you_saw": [], "you_missed": ["维护成本"], "key_difference": "你比综合建议更保守",
    "blind_spot_pattern": "", "next_checks": ["先问10个目标用户"],
}, ensure_ascii=False)


@dataclass
class RoleFakeClient:
    settled_after: int = 99  # 第 N 次 closure 调用起返回 settled
    fail_contributors: bool = False
    closure_calls: int = 0
    calls: list = field(default_factory=list)

    async def call(self, spec, system, user, *, role, **kw):
        self.calls.append(role)
        if role == "contributor" and self.fail_contributors:
            raise RuntimeError("contributor down")
        text = {
            "contributor": "建议专注主线。理由：精力稀释。最大风险：错过窗口。",
            "divergence": DMAP_JSON,
            "coach": COACH_JSON,
            "synthesis": "## 综合建议\n更稳妥的是专注内容主线。",
            "comparison": COMPARISON_JSON,
            "gate": json.dumps({"classification": "decision", "confidence": 0.9}),
            "observe": json.dumps({"observations": [{"dimension": "决断力", "behavior_tag": "怕精力稀释", "evidence_quote": "我担心精力", "confidence": 0.7}]}, ensure_ascii=False),
        }.get(role, "ok")
        if role == "closure":
            self.closure_calls += 1
            text = SETTLED_JSON if self.closure_calls >= self.settled_after else CONTINUE_JSON
        return LLMResult(text=text, model_key=spec.key, model_name="fake", tokens_in=10, tokens_out=10, cost_usd=0.001, latency_ms=3)


def make_flow(tmp_path, monkeypatch, *, quota=None, auth=None, observe_store=None, **fake_kw):
    monkeypatch.setenv("FAKE_KEY", "k")
    monkeypatch.setenv("FAKE_BASE", "https://fake/v1")
    model = {
        "model_name": "fake", "api_key_env": "FAKE_KEY", "base_url_env": "FAKE_BASE",
        "timeout_seconds": 5, "max_tokens": 100, "cost_per_1m_input": 1, "cost_per_1m_output": 1,
    }
    raw = {
        "app": {"db_path": str(tmp_path / "x.db")},
        "roles": {
            "contributors": ["m1", "m2", "m3"], "contributor_substitutes": [],
            "gate": "m1", "divergence": "m1", "coach": "m1", "closure": "m1",
            "synthesis": "m1", "observe": "m1",
        },
        "fanout": {"n_of_m": 2, "per_model_timeout_seconds": 5, "total_timeout_seconds": 10},
        "coach": {"max_rounds": 3},
        "models": {"m1": dict(model), "m2": dict(model), "m3": dict(model)},
    }
    cfg = AppConfig(raw=raw, root=tmp_path)
    store = SessionStore(tmp_path / "sessions.db")
    ledger = CostLedger(tmp_path / "x.db")
    client = RoleFakeClient(**fake_kw)
    return SparringFlow(cfg, store, ledger, client, quota=quota, auth=auth, observe_store=observe_store), client


async def _start(flow):
    events = []

    async def emit(name, payload):
        events.append(name)

    s = await flow.start("u1", "并行做产品还是专注内容？", emit)
    return s, events


def test_start_emits_and_saves(tmp_path, monkeypatch):
    flow, client = make_flow(tmp_path, monkeypatch)
    s, events = asyncio.get_event_loop().run_until_complete(_start(flow))
    assert events == ["recruiting", "thinking", "analyzing", "coach_ready"]
    got = flow.get_state("u1", s.id)
    assert got["state"] == "coaching"
    assert got["turns"][0]["role"] == "coach"
    assert got["divergence_map"]["divergence_points"][0]["id"] == "dp1"


def test_full_loop_settled_then_stance_then_reveal(tmp_path, monkeypatch):
    flow, client = make_flow(tmp_path, monkeypatch, settled_after=2)

    async def scenario():
        s, _ = await _start(flow)
        r1 = await flow.respond("u1", s.id, "我担心精力不够")
        assert r1["type"] == "coach" and r1["closure"] == "continue"
        r2 = await flow.respond("u1", s.id, "想清楚了：专注内容，因为A和B")
        assert r2["type"] == "suggest_stance"
        # 立场卡硬门：没写立场不给揭示
        with pytest.raises(StanceRequired):
            await flow.reveal("u1", s.id)
        await flow.stance("u1", s.id, "专注内容主线", "精力有限且未验证")
        out = await flow.reveal("u1", s.id)
        assert "综合建议" in out["synthesis"]
        assert out["comparison"]["you_missed"] == ["维护成本"]
        assert "决策备忘录" in out["memo_md"] and "专注内容主线" in out["memo_md"]
        # 二次 reveal 幂等
        again = await flow.reveal("u1", s.id)
        assert again["memo_md"] == out["memo_md"]

    asyncio.get_event_loop().run_until_complete(scenario())


def test_max_rounds_hard_gate_flips_state(tmp_path, monkeypatch):
    """审查：满轮是状态机硬门，不是可绕过的软提示。"""
    flow, client = make_flow(tmp_path, monkeypatch)  # max_rounds=3

    async def scenario():
        s, _ = await _start(flow)
        for i in range(3):
            r = await flow.respond("u1", s.id, f"继续想第{i}轮")
        assert r["suggest_stance"] is True
        r4 = await flow.respond("u1", s.id, "再聊一轮")
        assert r4["type"] == "suggest_stance"
        assert flow.get_state("u1", s.id)["state"] == "stance"  # 状态已硬切
        with pytest.raises(WrongState):
            await flow.respond("u1", s.id, "我还想聊")  # 第五次直接被状态机拒绝
        await flow.stance("u1", s.id, "立场", "理由")  # stance 态下写立场卡仍可用

    asyncio.get_event_loop().run_until_complete(scenario())


def test_start_failure_leaves_no_zombie_session(tmp_path, monkeypatch):
    """审查：启动失败不留 phase1 卡死尸体。"""
    flow, client = make_flow(tmp_path, monkeypatch, fail_contributors=True)

    async def scenario():
        with pytest.raises(FanoutError):
            await _start(flow)
        assert flow.history("u1") == []  # 会话已被清场

    asyncio.get_event_loop().run_until_complete(scenario())


def test_quota_blocks_after_daily_limit(tmp_path, monkeypatch):
    """M4：每日配额是开局硬门（默认 5 次）。"""
    from sparring.flow import QuotaExceeded
    from sparring.quota import QuotaStore

    q = QuotaStore(tmp_path / "q.db")
    flow, _ = make_flow(tmp_path, monkeypatch, quota=q)  # raw 无 quota 段 → 默认 5

    async def scenario():
        async def emit(n, p):
            pass

        for _ in range(5):
            await flow.start("u1", "q", emit)
        with pytest.raises(QuotaExceeded):
            await flow.start("u1", "q", emit)
        # 配额按 owner 隔离：换个人仍可开
        await flow.start("u2", "q", emit)

    asyncio.get_event_loop().run_until_complete(scenario())


def test_observe_records_on_reveal_with_consent(tmp_path, monkeypatch):
    """M4：揭示后在 consent 开启时沉淀跨会话画像观察。"""
    from sparring.auth import AuthStore
    from sparring.observe import ObservationStore

    auth = AuthStore(tmp_path / "auth.db")
    obs = ObservationStore(tmp_path / "obs.db")
    acc = auth.redeem(auth.create_invite())
    auth.set_consent(acc.user_id, True)  # M4 默认关，显式开启才观察（审查）
    flow, _ = make_flow(tmp_path, monkeypatch, settled_after=2, auth=auth, observe_store=obs)

    async def scenario():
        async def emit(n, p):
            pass

        s = await flow.start(acc.user_id, "并行还是专注？", emit)
        await flow.respond(acc.user_id, s.id, "我担心精力")
        await flow.respond(acc.user_id, s.id, "想清楚了，专注")  # settled
        await flow.stance(acc.user_id, s.id, "专注内容主线", "精力有限")
        await flow.reveal(acc.user_id, s.id)
        agg = obs.aggregate(acc.user_id)
        assert agg and agg[0]["behavior_tag"] == "怕精力稀释"

    asyncio.get_event_loop().run_until_complete(scenario())


def test_observe_skipped_without_consent(tmp_path, monkeypatch):
    """M4：consent 关闭时不写观察（隐私门）。"""
    from sparring.auth import AuthStore
    from sparring.observe import ObservationStore

    auth = AuthStore(tmp_path / "auth.db")
    obs = ObservationStore(tmp_path / "obs.db")
    acc = auth.redeem(auth.create_invite())
    auth.set_consent(acc.user_id, False)
    flow, _ = make_flow(tmp_path, monkeypatch, settled_after=2, auth=auth, observe_store=obs)

    async def scenario():
        async def emit(n, p):
            pass

        s = await flow.start(acc.user_id, "并行还是专注？", emit)
        await flow.respond(acc.user_id, s.id, "我担心精力")
        await flow.respond(acc.user_id, s.id, "想清楚了，专注")
        await flow.stance(acc.user_id, s.id, "专注内容主线", "精力有限")
        await flow.reveal(acc.user_id, s.id)
        assert obs.aggregate(acc.user_id) == []  # consent 关 → 无观察

    asyncio.get_event_loop().run_until_complete(scenario())


def test_owner_isolation_on_flow(tmp_path, monkeypatch):
    flow, client = make_flow(tmp_path, monkeypatch)

    async def scenario():
        s, _ = await _start(flow)
        with pytest.raises(NotFound):
            await flow.respond("u2", s.id, "我是别人")
        with pytest.raises(NotFound):
            flow.get_state("", s.id)

    asyncio.get_event_loop().run_until_complete(scenario())
