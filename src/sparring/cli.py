"""M1 验收入口：python -m sparring.cli "你的决策问题"

跑通：3 异构顾问并行 → 分歧地图 JSON → 成本入账（data/sparring.db::cost_ledger）。
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
import uuid

from .config import ConfigError, load_config
from .costs import CostLedger
from .divergence import analyze
from .fanout import FanoutError, fan_out
from .llm import LLMClient


async def run(question: str) -> int:
    cfg = load_config()
    ledger = CostLedger(cfg.db_path)
    client = LLMClient(ledger)

    specs = []
    for key in cfg.role_model_keys("contributors"):
        try:
            specs.append(cfg.resolve(key))
        except ConfigError as e:
            print(f"[跳过] {e}", file=sys.stderr)
    if len(specs) < 2:
        print("可用顾问不足 2 位：检查 .env（可整体从 上游/.env 复制）", file=sys.stderr)
        return 2

    session_id = uuid.uuid4().hex[:12]
    print(f"顾问团：{', '.join(s.model_name for s in specs)}")
    print("各顾问独立作答中…\n")
    try:
        answers = await fan_out(
            client, specs, question, session_id=session_id, **cfg.fanout_params()
        )
    except FanoutError as e:
        print(f"扇出失败：{e}", file=sys.stderr)
        return 3

    for i, a in enumerate(answers):
        head = a.text[:300].replace("\n", " ")
        print(f"── 顾问{chr(65 + i)} {a.model_name}（{a.latency_ms}ms · ${a.cost_usd:.4f}）──")
        print(f"{head}…\n")

    div_spec = cfg.resolve(cfg.role_model_keys("divergence")[0])
    dmap = await analyze(client, div_spec, question, answers, session_id=session_id)

    print("===== 分歧地图 =====")
    print(json.dumps(dmap.to_dict(), ensure_ascii=False, indent=2))

    total, n = ledger.session_total(session_id)
    print(f"\n本次成本：${total:.4f}（{n} 次调用已入账 {cfg.db_path}::cost_ledger）")
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(description="对练场 M1 CLI：分歧地图生成")
    parser.add_argument("question", help="你的真实决策问题（建议是取舍题，不是查事实）")
    raise SystemExit(asyncio.run(run(parser.parse_args().question)))


if __name__ == "__main__":
    main()
