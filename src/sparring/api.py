"""HTTP API：FastAPI + SSE 端点。

鉴权（M4 · 关 CRITICAL）：邀请码 redeem 换服务端签发的 Bearer token；所有业务端点
从 token 反查真实 user_id，客户端无法再自报身份冒充他人。
"""
from __future__ import annotations

import asyncio
import contextlib
import json
import logging

from fastapi import FastAPI, Header, HTTPException
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel
from sse_starlette.sse import EventSourceResponse

from .auth import AuthError, AuthStore
from .config import load_config
from .costs import CostLedger
from .flow import FlowError, SparringFlow
from .llm import LLMClient
from .observe import ObservationStore
from .quota import QuotaStore
from .store import SessionStore

logger = logging.getLogger(__name__)


class StartBody(BaseModel):
    question: str


class RespondBody(BaseModel):
    text: str


class StanceBody(BaseModel):
    stance: str
    reason: str = ""


class RedeemBody(BaseModel):
    code: str


class ConsentBody(BaseModel):
    consent: bool


def create_app() -> FastAPI:
    cfg = load_config()
    ledger = CostLedger(cfg.db_path)
    store = SessionStore(cfg.db_path.parent / "sessions.db")
    auth = AuthStore(cfg.db_path)
    quota = QuotaStore(cfg.db_path)
    observe_store = ObservationStore(cfg.db_path)
    client = LLMClient(ledger)
    flow = SparringFlow(cfg, store, ledger, client, quota=quota, auth=auth, observe_store=observe_store)

    app = FastAPI(title="对练场", version="0.1.0")

    def require_user(authorization: str | None) -> str:
        """Bearer token → 真实 user_id（M4 fail-closed，不再信任客户端自报）。"""
        if not authorization or not authorization.startswith("Bearer "):
            raise HTTPException(status_code=401, detail="未登录")
        uid = auth.resolve_token(authorization[7:])
        if not uid:
            raise HTTPException(status_code=401, detail="登录已失效，请重新输入邀请码")
        return uid

    def to_http(e: Exception) -> HTTPException:
        if isinstance(e, FlowError):
            return HTTPException(status_code=e.status, detail=str(e))
        logger.exception("未知错误")
        return HTTPException(status_code=500, detail="内部错误")

    # ── 鉴权 ──
    @app.post("/api/auth/redeem")
    async def redeem(body: RedeemBody):
        try:
            acc = auth.redeem(body.code)
        except AuthError as e:
            raise HTTPException(status_code=400, detail=str(e))
        return {"token": acc.token, "user_id": acc.user_id}

    # ── 七步循环 ──
    @app.post("/api/sparring/precheck")
    async def precheck(body: StartBody, authorization: str | None = Header(None)):
        uid = require_user(authorization)
        g = await flow.precheck(body.question, uid)
        return {
            "passed": g.passed,
            "classification": g.classification,
            "decision_axis": g.decision_axis,
            "confidence": g.confidence,
            "rewrite_suggestion": g.rewrite_suggestion,
        }

    @app.post("/api/sparring/start/stream")
    async def start_stream(body: StartBody, authorization: str | None = Header(None)):
        uid = require_user(authorization)
        queue: asyncio.Queue = asyncio.Queue()

        async def emit(name: str, payload: dict) -> None:
            await queue.put((name, payload))

        async def runner() -> None:
            try:
                # Phase1 硬超时兜底（120s 扇出 + 分析/首问余量）
                await asyncio.wait_for(flow.start(uid, body.question, emit), timeout=150)
            except asyncio.TimeoutError:
                await queue.put(("error", {"message": "启动超时，请稍后重试"}))
            except Exception as e:
                logger.warning("start 失败: %s", e)
                await queue.put(("error", {"message": str(e)[:200]}))
            finally:
                await queue.put(("__done__", {}))

        task = asyncio.create_task(runner())

        async def gen():
            try:
                while True:
                    try:
                        # 15s 心跳：防 Cloudflare Tunnel 等中间层掐空闲连接（上游 实战坑资产）
                        name, payload = await asyncio.wait_for(queue.get(), timeout=15)
                    except asyncio.TimeoutError:
                        yield {"event": "heartbeat", "data": "{}"}
                        continue
                    if name == "__done__":
                        break
                    yield {"event": name, "data": json.dumps(payload, ensure_ascii=False)}
            finally:
                if not task.done():
                    task.cancel()
                    # 审查：cancel 后 await 收尸，确认 runner 真正结束
                    with contextlib.suppress(asyncio.CancelledError):
                        await task

        return EventSourceResponse(gen())

    @app.post("/api/sparring/{session_id}/respond")
    async def respond(session_id: str, body: RespondBody, authorization: str | None = Header(None)):
        uid = require_user(authorization)
        try:
            return await flow.respond(uid, session_id, body.text)
        except FlowError as e:
            raise to_http(e)

    @app.post("/api/sparring/{session_id}/stance")
    async def stance(session_id: str, body: StanceBody, authorization: str | None = Header(None)):
        uid = require_user(authorization)
        try:
            return await flow.stance(uid, session_id, body.stance, body.reason)
        except FlowError as e:
            raise to_http(e)

    @app.post("/api/sparring/{session_id}/reveal")
    async def reveal(session_id: str, authorization: str | None = Header(None)):
        uid = require_user(authorization)
        try:
            return await flow.reveal(uid, session_id)
        except FlowError as e:
            raise to_http(e)

    @app.get("/api/sparring/{session_id}")
    async def get_session(session_id: str, authorization: str | None = Header(None)):
        uid = require_user(authorization)
        try:
            return flow.get_state(uid, session_id)
        except FlowError as e:
            raise to_http(e)

    @app.get("/api/sparring/{session_id}/memo", response_class=PlainTextResponse)
    async def memo(session_id: str, authorization: str | None = Header(None)):
        uid = require_user(authorization)
        try:
            state = flow.get_state(uid, session_id)
        except FlowError as e:
            raise to_http(e)
        if not state["memo_md"]:
            raise HTTPException(status_code=404, detail="备忘录尚未生成（先揭示）")
        return state["memo_md"]

    @app.get("/api/history")
    async def history(authorization: str | None = Header(None)):
        uid = require_user(authorization)
        return {"sessions": flow.history(uid)}

    # ── 画像观察（M4 · consent + 删除权）──
    @app.get("/api/profile")
    async def profile(authorization: str | None = Header(None)):
        uid = require_user(authorization)
        return {
            "consent": auth.get_consent(uid),
            "observations": flow.observations(uid),
            "quota_remaining": quota.remaining(uid, int(cfg.raw.get("quota", {}).get("daily_sessions_per_user", 5))),
        }

    @app.post("/api/profile/consent")
    async def set_consent(body: ConsentBody, authorization: str | None = Header(None)):
        uid = require_user(authorization)
        auth.set_consent(uid, body.consent)
        return {"consent": body.consent}

    @app.delete("/api/profile/observations")
    async def delete_observations(authorization: str | None = Header(None)):
        uid = require_user(authorization)
        return {"deleted": flow.delete_observations(uid)}

    @app.get("/api/health")
    async def health():
        return {"ok": True}

    return app


app = create_app()
