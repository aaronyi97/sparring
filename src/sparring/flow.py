"""对练编排器：七步循环的胶水层（核心循环）。

时序：precheck → start（扇出+分歧+首问 · 综合扔后台）→ respond×N（收口判定→教练）
     → stance（立场卡，不可跳过）→ reveal（等综合→对照→备忘录）。
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import asdict
from typing import Awaitable, Callable

from . import closure as closure_mod
from . import coach as coach_mod
from . import reveal as reveal_mod
from .config import AppConfig, ConfigError, ModelSpec
from .costs import CostLedger
from .divergence import analyze
from .domain import DivergenceMap
from .fanout import fan_out
from .gate import GateResult, precheck as gate_precheck
from .llm import LLMClient
from .memo import build_memo
from .store import Session, SessionStore, Turn

logger = logging.getLogger(__name__)

Emit = Callable[[str, dict], Awaitable[None]]

SETTLED_MESSAGE = "听起来你已经形成了能站得住的立场。把它写进立场卡吧——写下你的结论和理由，然后看顾问们的综合建议和你的差在哪。"
MAX_ROUNDS_MESSAGE = "这一题我们已经对练了 8 轮，再聊容易原地打转。把你现在的立场写进立场卡，去看对照揭示吧。"


class FlowError(RuntimeError):
    status = 400


class NotFound(FlowError):
    status = 404


class WrongState(FlowError):
    status = 409


class StanceRequired(FlowError):
    status = 412


class QuotaExceeded(FlowError):
    status = 429


class SparringFlow:
    def __init__(
        self,
        cfg: AppConfig,
        store: SessionStore,
        ledger: CostLedger,
        client: LLMClient,
        *,
        quota=None,
        auth=None,
        observe_store=None,
    ):
        self.cfg = cfg
        self.store = store
        self.ledger = ledger
        self.client = client
        # M4 可选依赖：测试不传则配额不限、观察不生成（保持 M1-M3 测试无改动可过）
        self.quota = quota
        self.auth = auth
        self.observe_store = observe_store
        self._synth_tasks: dict[str, asyncio.Task] = {}

    # ── 角色模型解析 ──
    def _spec(self, role: str) -> ModelSpec:
        return self.cfg.resolve(self.cfg.role_model_keys(role)[0])

    def _contributor_specs(self) -> list[ModelSpec]:
        specs = []
        for key in self.cfg.role_model_keys("contributors"):
            try:
                specs.append(self.cfg.resolve(key))
            except ConfigError as e:
                logger.warning("贡献者跳过: %s", e)
        for key in self.cfg.role_model_keys("contributor_substitutes"):
            if len(specs) >= 3:
                break
            try:
                specs.append(self.cfg.resolve(key))
            except ConfigError:
                pass
        return specs

    # ── 七步循环 ──
    async def precheck(self, question: str, user_id: str) -> GateResult:
        return await gate_precheck(self.client, self._spec("gate"), question, user_id=user_id)

    async def start(self, owner_id: str, question: str, emit: Emit) -> Session:
        specs = self._contributor_specs()
        if len(specs) < 2:
            raise FlowError("可用顾问不足 2 位（检查 .env 凭证）")
        # M4 配额：开一局即计数（超限不建会话）。失败会话虽已扣次数，但 fanout 失败少见，v1 取简
        if self.quota is not None:
            limit = int(self.cfg.raw.get("quota", {}).get("daily_sessions_per_user", 5))
            if not self.quota.check_and_consume(owner_id, limit):
                raise QuotaExceeded(f"今天的对练次数已用完（每日 {limit} 次），明天再来")
        s = self.store.create(owner_id, question)
        try:
            await emit("recruiting", {"session_id": s.id, "contributors": [sp.model_name for sp in specs]})
            await emit("thinking", {"session_id": s.id, "total": len(specs)})

            fp = self.cfg.fanout_params()
            answers = await fan_out(
                self.client, specs, question,
                n_of_m=fp["n_of_m"], per_model_timeout=fp["per_model_timeout"],
                total_timeout=fp["total_timeout"], session_id=s.id, user_id=owner_id,
            )
            await emit("analyzing", {"session_id": s.id, "arrived": len(answers), "total": len(specs)})

            dmap = await analyze(
                self.client, self._spec("divergence"), question, answers,
                session_id=s.id, user_id=owner_id,
            )
            s.contributors = [asdict(a) for a in answers]
            s.divergence_map = dmap.to_dict()

            first = await coach_mod.guide(self.client, self._spec("coach"), s, dmap, user_id=owner_id)
            s.turns.append(
                Turn(role="coach", text=first.message, meta={"track": first.track, "dp": first.target_dp_id, "fb": first.fallback})
            )
            s.state = "coaching"
            self.store.save(s)
        except Exception:
            # 审查：启动中途失败不留 phase1 卡死尸体，删掉让用户干净重来
            self.store.delete(s.id, owner_id)
            raise

        # 综合扔后台：用户开聊期间悄悄准备揭示材料（上游 两阶段架构资产）
        task = asyncio.create_task(self._run_synthesis(s.id, owner_id, question, s.contributors, dmap))
        self._synth_tasks[s.id] = task
        # 审查：完成即自摘，防注册表泄漏（reveal 先 pop 到的话这里 pop 空，无妨）
        task.add_done_callback(lambda t, sid=s.id: self._synth_tasks.pop(sid, None))
        await emit("coach_ready", {
            "session_id": s.id,
            "guide_message": first.message,
            "arrived": len(answers),
            "total": len(specs),
            "consensus_points": dmap.consensus_points,
            "divergence_topics": [p.topic for p in dmap.sorted_points()],
        })
        return s

    async def respond(self, owner_id: str, session_id: str, text: str) -> dict:
        s = self._load(owner_id, session_id)
        if s.state != "coaching":
            raise WrongState(f"当前状态 {s.state} 不能继续对练")
        text = (text or "").strip()
        if not text:
            raise FlowError("回复不能为空")

        max_rounds = int(self.cfg.raw.get("coach", {}).get("max_rounds", 8))
        if s.rounds_used >= max_rounds:
            # 审查：满轮不是软提示是硬门——状态机直接进立场卡，respond 不再可用
            s.state = "stance"
            self.store.save(s)
            return {"type": "suggest_stance", "message": MAX_ROUNDS_MESSAGE, "rounds_used": s.rounds_used}

        dmap = DivergenceMap.from_dict(s.divergence_map or {})
        current_topic = ""
        for t in reversed(s.turns):
            meta = t.meta if isinstance(t, Turn) else t.get("meta", {})
            if meta.get("dp"):
                match = [p for p in dmap.divergence_points if p.id == meta["dp"]]
                if match:
                    current_topic = match[0].topic
                break

        cl = await closure_mod.classify(
            self.client, self._spec("closure"), s, text, current_topic, user_id=owner_id
        )
        # 先持久化用户轮，再生成教练回复（上游 防丢数据经验：教练失败不丢用户输入）
        s.turns.append(Turn(role="user", text=text, meta={"closure": cl.closure}))
        s.rounds_used += 1
        if cl.closure == "shifted":
            s.closure_reason = f"shifted@round{s.rounds_used}: {cl.stance_summary}"
        self.store.save(s)

        if cl.closure == "settled":
            s.closure_reason = f"settled@round{s.rounds_used}: {cl.stance_summary}"
            self.store.save(s)
            return {
                "type": "suggest_stance", "message": SETTLED_MESSAGE,
                "rounds_used": s.rounds_used, "closure": cl.closure,
                "stance_hint": cl.stance_summary,
            }

        reply = await coach_mod.guide(
            self.client, self._spec("coach"), s, dmap, closure=cl, user_id=owner_id
        )
        s.turns.append(
            Turn(role="coach", text=reply.message, meta={"track": reply.track, "dp": reply.target_dp_id, "fb": reply.fallback})
        )
        self.store.save(s)
        return {
            "type": "coach", "message": reply.message, "track": reply.track,
            "rounds_used": s.rounds_used, "closure": cl.closure,
            "suggest_stance": s.rounds_used >= max_rounds,
        }

    async def stance(self, owner_id: str, session_id: str, stance: str, reason: str) -> dict:
        s = self._load(owner_id, session_id)
        if s.state not in ("coaching", "stance"):
            raise WrongState(f"当前状态 {s.state} 不能写立场卡")
        stance = (stance or "").strip()
        if not stance:
            raise FlowError("立场不能为空——「我还没想清楚 + 卡在哪」也是合法立场")
        s.user_stance = stance
        s.stance_reason = (reason or "").strip()
        s.state = "stance"
        self.store.save(s)
        return {"ok": True, "state": s.state}

    async def reveal(self, owner_id: str, session_id: str) -> dict:
        s = self._load(owner_id, session_id)
        if not s.user_stance:
            raise StanceRequired("揭示前必须先写立场卡（这是对练和普通问答的分水岭）")
        if s.state == "revealed":
            return self._reveal_payload(s)

        # 等后台综合；没有/失败则现场跑
        task = self._synth_tasks.pop(s.id, None)
        if task is not None:
            try:
                await asyncio.wait_for(task, timeout=60)
            except (asyncio.TimeoutError, Exception):
                pass
            s = self._load(owner_id, session_id)  # 重读拿后台写入的综合
        dmap = DivergenceMap.from_dict(s.divergence_map or {})
        if not s.synthesis:
            s.synthesis = await reveal_mod.synthesize(
                self.client, self._spec("synthesis"), s.question, s.contributors, dmap,
                session_id=s.id, user_id=owner_id,
            )

        digest = "\n".join(
            f"{'用户' if (t.role if isinstance(t, Turn) else t.get('role')) == 'user' else '教练'}："
            f"{(t.text if isinstance(t, Turn) else t.get('text', ''))[:120]}"
            for t in s.turns[-10:]
        )
        s.comparison = await reveal_mod.compare(
            self.client, self._spec("synthesis"), s.question, s.user_stance, s.stance_reason,
            s.synthesis, digest, session_id=s.id, user_id=owner_id,
        )
        s.memo_md = build_memo(s, dmap)
        s.state = "revealed"
        self.store.save(s)
        await self._maybe_observe(s, owner_id)
        return self._reveal_payload(s)

    async def _maybe_observe(self, s: Session, owner_id: str) -> None:
        """揭示后生成跨会话画像观察（consent 门 + fail-soft，画像缺一局无伤大雅）。"""
        if self.observe_store is None or self.auth is None:
            return
        if not self.auth.get_consent(owner_id):
            return
        try:
            from . import observe as observe_mod

            obs = await observe_mod.generate(self.client, self._spec("observe"), s, owner_id=owner_id)
            if obs:
                self.observe_store.add_many(owner_id, s.id, obs)
        except Exception as e:  # 观察永不阻断揭示
            logger.warning("画像观察生成/写入失败（%s），跳过", e)

    def observations(self, owner_id: str) -> list[dict]:
        return self.observe_store.aggregate(owner_id) if self.observe_store else []

    def delete_observations(self, owner_id: str) -> int:
        return self.observe_store.delete_all(owner_id) if self.observe_store else 0

    def get_state(self, owner_id: str, session_id: str) -> dict:
        """断点恢复（审查）：前端刷新后从这里重建。"""
        s = self._load(owner_id, session_id)
        return {
            "session_id": s.id, "question": s.question, "state": s.state,
            "rounds_used": s.rounds_used,
            "divergence_map": s.divergence_map,
            "turns": [asdict(t) if isinstance(t, Turn) else t for t in s.turns],
            "user_stance": s.user_stance, "stance_reason": s.stance_reason,
            "revealed": s.state == "revealed",
            "synthesis": s.synthesis if s.state == "revealed" else "",
            "comparison": s.comparison if s.state == "revealed" else None,
            "memo_md": s.memo_md if s.state == "revealed" else "",
        }

    def history(self, owner_id: str) -> list[dict]:
        return self.store.list_for_owner(owner_id)

    # ── 内部 ──
    def _load(self, owner_id: str, session_id: str) -> Session:
        s = self.store.get(session_id, owner_id)
        if s is None:
            raise NotFound("会话不存在或无权访问")
        return s

    def _reveal_payload(self, s: Session) -> dict:
        return {
            "synthesis": s.synthesis,
            "comparison": s.comparison,
            "memo_md": s.memo_md,
            "user_stance": s.user_stance,
            "state": s.state,
        }

    async def _run_synthesis(self, session_id: str, owner_id: str, question: str, answers: list, dmap: DivergenceMap) -> None:
        try:
            md = await reveal_mod.synthesize(
                self.client, self._spec("synthesis"), question, answers, dmap,
                session_id=session_id, user_id=owner_id,
            )
            s = self.store.get(session_id, owner_id)
            if s is not None:
                s.synthesis = md
                self.store.save(s)
        except Exception as e:
            logger.warning("后台综合失败（%s），reveal 时现场重跑", e)
