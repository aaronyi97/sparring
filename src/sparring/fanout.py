"""贡献者扇出：3 异构模型并行独立回答，n_of_m 先到先用、慢者取消。"""
from __future__ import annotations

import asyncio
import logging
import time

from .config import ModelSpec
from .domain import ModelAnswer
from .llm import LLMClient

logger = logging.getLogger(__name__)

# 决策顾问 prompt（新写 · 对照 上游 contributor_deep 的"独立作答"原则，场景换成决策）
CONTRIBUTOR_SYSTEM = """你是决策顾问团中的一位独立顾问。用户会给出一个真实的决策困境。

要求：
1. 开头一句话亮明立场：你建议怎么选
2. 给出 2-4 条关键理由，每条要具体到这个决策的情境，不要通用套话
3. 最后指出你这个立场的最大风险或例外情形（1 条）

不要骑墙。如果你认为"取决于 X"，必须说清 X 是什么，并给出按 X 分支的明确建议。
全文控制在 400 字以内。"""


class FanoutError(RuntimeError):
    pass


async def _one(
    client: LLMClient,
    spec: ModelSpec,
    question: str,
    per_model_timeout: float,
    session_id: str,
    user_id: str,
) -> ModelAnswer:
    t0 = time.monotonic()
    try:
        result = await asyncio.wait_for(
            client.call(
                spec,
                CONTRIBUTOR_SYSTEM,
                question,
                role="contributor",
                session_id=session_id,
                user_id=user_id,
                retryable=True,
            ),
            timeout=per_model_timeout,
        )
        return ModelAnswer(
            model_key=spec.key,
            model_name=spec.model_name,
            text=result.text,
            latency_ms=result.latency_ms,
            tokens_in=result.tokens_in,
            tokens_out=result.tokens_out,
            cost_usd=result.cost_usd,
        )
    except asyncio.CancelledError:
        raise
    except asyncio.TimeoutError:
        latency = int((time.monotonic() - t0) * 1000)
        logger.warning("contributor %s 超时（%sms）", spec.key, latency)
        return ModelAnswer(spec.key, spec.model_name, "", latency_ms=latency, error="timeout")
    except Exception as e:
        latency = int((time.monotonic() - t0) * 1000)
        logger.warning("contributor %s 失败: %s", spec.key, e)
        return ModelAnswer(spec.key, spec.model_name, "", latency_ms=latency, error=str(e)[:200])


async def fan_out(
    client: LLMClient,
    specs: list[ModelSpec],
    question: str,
    *,
    n_of_m: int = 2,
    per_model_timeout: float = 90,
    total_timeout: float = 120,
    session_id: str = "",
    user_id: str = "",
) -> list[ModelAnswer]:
    """并行问 len(specs) 位顾问；凑齐 n_of_m 个成功回答即取消其余（省时省钱）。

    成功 < 2 视为失败（分歧地图至少需要两方观点）。
    """
    tasks = [
        asyncio.create_task(_one(client, s, question, per_model_timeout, session_id, user_id))
        for s in specs
    ]
    answers: list[ModelAnswer] = []
    ok_count = 0
    try:
        for fut in asyncio.as_completed(tasks, timeout=total_timeout):
            ans = await fut
            answers.append(ans)
            if ans.ok:
                ok_count += 1
                if ok_count >= n_of_m:
                    break
    except asyncio.TimeoutError:
        logger.warning("fanout 总超时（%ss），按已到位回答继续", total_timeout)
    finally:
        for t in tasks:
            if not t.done():
                t.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)

    ok = [a for a in answers if a.ok]
    if len(ok) < 2:
        errs = "; ".join(f"{a.model_key}={a.error}" for a in answers if not a.ok)
        raise FanoutError(f"顾问到位不足（成功 {len(ok)}/{len(specs)}，至少需要 2 位）：{errs}")
    return ok
