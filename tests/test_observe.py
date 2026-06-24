"""画像观察（M4）：结构化存储 / 跨会话聚合 / 删除权 / 生成解析与 fail-soft。"""
import asyncio
import json
from dataclasses import dataclass, field

from sparring import observe as observe_mod
from sparring.llm import LLMResult
from sparring.observe import ObservationStore
from sparring.store import Session, Turn


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
        self.calls.append(kw.get("role"))
        if self.fail:
            raise RuntimeError("boom")
        return LLMResult(text=self.reply, model_key="f", model_name="f", tokens_in=1, tokens_out=1, cost_usd=0.0, latency_ms=1)


def _run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


def test_store_add_aggregate_repeat_count(tmp_path):
    store = ObservationStore(tmp_path / "o.db")
    store.add_many("u1", "s1", [{"dimension": "推理深度", "behavior_tag": "回避成本", "evidence_quote": "不想算钱", "confidence": 0.8}])
    store.add_many("u1", "s2", [{"dimension": "推理深度", "behavior_tag": "回避成本", "evidence_quote": "还是没算", "confidence": 0.7}])
    agg = store.aggregate("u1")
    assert len(agg) == 1
    assert agg[0]["behavior_tag"] == "回避成本" and agg[0]["repeat_count"] == 2
    assert agg[0]["recent_evidence"] == "还是没算"  # 取最近一条证据


def test_store_skips_empty_tag(tmp_path):
    store = ObservationStore(tmp_path / "o.db")
    n = store.add_many("u1", "s1", [{"dimension": "x", "behavior_tag": "", "evidence_quote": "y"}])
    assert n == 0 and store.aggregate("u1") == []


def test_delete_all_scoped(tmp_path):
    store = ObservationStore(tmp_path / "o.db")
    store.add_many("u1", "s1", [{"behavior_tag": "a"}])
    store.add_many("u2", "s1", [{"behavior_tag": "b"}])
    store.delete_all("u1")
    assert store.aggregate("u1") == []
    assert len(store.aggregate("u2")) == 1  # 只删自己的


def _session():
    s = Session(id="s1", owner_id="u1", question="要不要换城市", user_stance="先不换", stance_reason="成本高")
    s.turns = [Turn(role="coach", text="为什么"), Turn(role="user", text="我怕折腾")]
    s.comparison = {"you_missed": ["没算机会成本"]}
    return s


def test_generate_parses_observations():
    reply = json.dumps({"observations": [{"dimension": "决断力", "behavior_tag": "怕折腾", "evidence_quote": "我怕折腾", "confidence": 0.6}]}, ensure_ascii=False)
    obs = _run(observe_mod.generate(FakeClient(reply=reply), FakeSpec(), _session(), owner_id="u1"))
    assert len(obs) == 1 and obs[0]["behavior_tag"] == "怕折腾"


def test_generate_fail_soft_returns_empty():
    assert _run(observe_mod.generate(FakeClient(fail=True), FakeSpec(), _session(), owner_id="u1")) == []


def test_generate_garbage_returns_empty():
    assert _run(observe_mod.generate(FakeClient(reply="不是JSON"), FakeSpec(), _session(), owner_id="u1")) == []
