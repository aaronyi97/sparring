"""逐模型探活诊断：env 变量齐不齐（只报名字）+ 1 次 8 token ping（只报错误类型）。

用法：.venv/bin/python tools/diag.py [--candidates]
--candidates 额外探测同 base_url 下的备选模型名（中转货架变更排查）。
"""
from __future__ import annotations

import asyncio
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from sparring.config import ConfigError, load_config  # noqa: E402
from sparring.costs import CostLedger  # noqa: E402
from sparring.llm import LLMClient  # noqa: E402

# 中转货架备选名（按 上游 价格表与命名惯例）
CANDIDATES = {
    "gemini_pro": ["gemini-3.1-pro", "gemini-3-pro", "gemini-3.1-pro-preview-thinking-high", "gemini-3.5-flash"],
    "gpt54_low": ["gpt-5.4", "gpt-5.2"],
    "gemini_flash_lite": ["gemini-3.1-flash-lite", "gemini-3-flash"],
}


def _cause_chain(e: BaseException) -> str:
    parts = []
    cur: BaseException | None = e
    while cur is not None and len(parts) < 4:
        status = getattr(cur, "status_code", None)
        parts.append(f"{type(cur).__name__}{f'({status})' if status else ''}")
        cur = cur.__cause__
    return " <- ".join(parts)


async def ping(client, spec) -> str:
    try:
        r = await client.call(
            spec, "你是探活探针。", "只回复一个字：通", role="diag", retryable=False, max_tokens=8
        )
        return f"OK ({r.latency_ms}ms, '{r.text.strip()[:10]}')"
    except Exception as e:
        return f"FAIL {_cause_chain(e)}: {str(e)[:100]}"


async def main() -> None:
    cfg = load_config()
    ledger = CostLedger(cfg.db_path)
    client = LLMClient(ledger)
    print(f"CHINA_PROXY 已设置: {'是' if os.environ.get('CHINA_PROXY') else '否'}")
    print(f"OVERSEAS_PROXY 已设置: {'是' if os.environ.get('OVERSEAS_PROXY') else '否'}\n")

    with_candidates = "--candidates" in sys.argv
    for key in cfg.raw["models"]:
        try:
            spec = cfg.resolve(key)
        except ConfigError as e:
            print(f"[{key}] ENV 缺失 → {e}")
            continue
        result = await ping(client, spec)
        print(f"[{key}] {spec.model_name} → {result}")
        if with_candidates and result.startswith("FAIL") and key in CANDIDATES:
            for alt in CANDIDATES[key]:
                alt_spec = type(spec)(**{**spec.__dict__, "model_name": alt})
                print(f"    ↳ 备选 {alt} → {await ping(client, alt_spec)}")

    if "--cross" in sys.argv:
        # 用活着的 key+base 交叉探其他家族模型名（OpenAI 兼容中转常见一 key 全货架）
        print("\n=== 交叉探活：OPENAI 线（gpt52 key）===")
        base = cfg.resolve("gpt54_low")
        for name in [
            "gemini-3.1-pro-preview", "gemini-3.1-pro", "gemini-3-flash",
            "kimi-k2.5", "deepseek-v3.2", "deepseek-chat",
            "claude-haiku-4-5", "gpt-5.5",
        ]:
            alt = type(base)(**{**base.__dict__, "model_name": name})
            print(f"  {name} → {await ping(client, alt)}")
        print("\n=== 交叉探活：CLAUDE 线（sonnet key）===")
        cbase = cfg.resolve("claude_sonnet")
        for name in ["claude-haiku-4-5", "claude-opus-4-6", "gemini-3.1-pro", "kimi-k2.5"]:
            alt = type(cbase)(**{**cbase.__dict__, "model_name": name})
            print(f"  {name} → {await ping(client, alt)}")


if __name__ == "__main__":
    asyncio.run(main())
