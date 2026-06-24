"""统一 LLM 调用层（OpenAI 兼容 · 中转默认）。

must-keep 保命件（审查）：
- fail-closed 由 config.resolve 保证（缺 env 直接抛，不会走到这里）
- per-model timeout / 429 与 5xx 指数退避 / 永久性错误绝不重试（重试只烧钱）
- 角色级重试策略：retryable=False 的调用（教练轮）不盲重
- 全局并发上限 semaphore
- 每次成功调用写 cost_ledger（request_id 幂等）
"""
from __future__ import annotations

import asyncio
import logging
import random
import time
import uuid
from dataclasses import dataclass

import httpx
from openai import APIConnectionError, APIStatusError, APITimeoutError, AsyncOpenAI

from .config import ModelSpec
from .costs import CostLedger

logger = logging.getLogger(__name__)

MAX_RETRIES = 2
RETRY_BASE_DELAY = 1.0
RETRYABLE_STATUS = {429, 500, 502, 503, 504}
# 上游 实战清单：这些错误重试只会重复烧钱或重复失败
NON_RETRYABLE_KEYWORDS = {
    "insufficient_user_quota",
    "insufficient_quota",
    "billing",
    "account_deactivated",
    "invalid_api_key",
    "authentication",
    "no available accounts",
}


class LLMCallError(RuntimeError):
    pass


class NonRetryableError(LLMCallError):
    pass


@dataclass
class LLMResult:
    text: str
    model_key: str
    model_name: str
    tokens_in: int
    tokens_out: int
    cost_usd: float
    latency_ms: int


def _is_non_retryable(err: Exception) -> bool:
    msg = str(err).lower()
    return any(k in msg for k in NON_RETRYABLE_KEYWORDS)


class LLMClient:
    def __init__(self, ledger: CostLedger, max_concurrency: int = 8):
        self._ledger = ledger
        self._sem = asyncio.Semaphore(max_concurrency)

    async def call(
        self,
        spec: ModelSpec,
        system: str,
        user: str | list[dict],
        *,
        role: str,
        session_id: str = "",
        user_id: str = "",
        retryable: bool = True,
        max_tokens: int | None = None,
    ) -> LLMResult:
        messages: list[dict] = [{"role": "system", "content": system}]
        if isinstance(user, str):
            messages.append({"role": "user", "content": user})
        else:
            messages.extend(user)

        attempts = (MAX_RETRIES + 1) if retryable else 1
        last_err: Exception | None = None
        for attempt in range(attempts):
            try:
                return await self._once(spec, messages, role, session_id, user_id, max_tokens)
            except NonRetryableError:
                raise
            except (APITimeoutError, APIConnectionError) as e:
                last_err = e
            except APIStatusError as e:
                if _is_non_retryable(e):
                    raise NonRetryableError(f"{spec.model_name}: {e.status_code} 永久性错误") from e
                if e.status_code not in RETRYABLE_STATUS:
                    raise LLMCallError(f"{spec.model_name}: HTTP {e.status_code}") from e
                last_err = e
            except asyncio.CancelledError:
                raise
            except Exception as e:  # 兜底：未知错误按可重试处理，但保留原因
                if _is_non_retryable(e):
                    raise NonRetryableError(f"{spec.model_name}: 永久性错误") from e
                last_err = e
            if attempt < attempts - 1:
                # full jitter（审查）：多会话同时撞限流时避免同秒齐发的重试风暴
                await asyncio.sleep(RETRY_BASE_DELAY * (2**attempt) * (0.5 + random.random()))
        raise LLMCallError(f"{spec.model_name}: 重试 {attempts} 次后仍失败（{type(last_err).__name__}）") from last_err

    async def _once(
        self,
        spec: ModelSpec,
        messages: list[dict],
        role: str,
        session_id: str,
        user_id: str,
        max_tokens: int | None,
    ) -> LLMResult:
        async with self._sem:
            t0 = time.monotonic()
            http_client = httpx.AsyncClient(proxy=spec.proxy_url) if spec.proxy_url else None
            client = AsyncOpenAI(
                api_key=spec.api_key,
                base_url=spec.base_url,
                timeout=spec.timeout_seconds,
                max_retries=0,  # 重试由本层统一控制，不让 SDK 偷偷加倍
                http_client=http_client,
            )
            try:
                kwargs: dict = {
                    "model": spec.model_name,
                    "messages": messages,
                    "max_tokens": max_tokens or spec.max_tokens,
                }
                if spec.temperature is not None:
                    kwargs["temperature"] = spec.temperature
                if spec.reasoning_effort:
                    kwargs["extra_body"] = {"reasoning_effort": spec.reasoning_effort}
                resp = await client.chat.completions.create(**kwargs)
            finally:
                await client.close()
                if http_client is not None:
                    await http_client.aclose()

            latency_ms = int((time.monotonic() - t0) * 1000)
            text = (resp.choices[0].message.content or "") if resp.choices else ""
            usage = getattr(resp, "usage", None)
            tokens_in = int(getattr(usage, "prompt_tokens", 0) or 0)
            tokens_out = int(getattr(usage, "completion_tokens", 0) or 0)
            cost_usd = (
                tokens_in * spec.cost_per_1m_input + tokens_out * spec.cost_per_1m_output
            ) / 1_000_000
            try:
                # 账本写入与重试边界隔离（审查）：模型已成功返回后，本地记账失败
                # 绝不能触发重新付费调用——只记错误，欠账靠日志补。
                # request_id 语义说明（审查 采纳为注释）：每次真实 API 调用 = 真实花费 =
                # 独立一行；幂等键只防同一次调用的重放写入，不做业务级去重。
                self._ledger.record(
                    request_id=uuid.uuid4().hex,
                    role=role,
                    model=spec.model_name,
                    tokens_in=tokens_in,
                    tokens_out=tokens_out,
                    cost_usd=cost_usd,
                    session_id=session_id,
                    user_id=user_id,
                )
            except Exception:
                logger.exception("成本记账失败（模型调用已成功，不重试）：%s %s", role, spec.model_name)
            if not text.strip():
                raise LLMCallError(f"{spec.model_name}: 返回空内容")
            return LLMResult(
                text=text,
                model_key=spec.key,
                model_name=spec.model_name,
                tokens_in=tokens_in,
                tokens_out=tokens_out,
                cost_usd=cost_usd,
                latency_ms=latency_ms,
            )
