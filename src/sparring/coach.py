"""决策对练教练：5 轨道引导（迁移自 上游 socratic_guide 核心资产，决策化改写）。

迁移说明：5 轨道 / 难度适配 / 共识前置 / 语气五字诀 原样保留骨架；
"思维教练"→"决策对练教练"；hint 机制改为消费收口判定器的 stuck_reason（换轨道优先）。
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

from .closure import ClosureResult
from .config import ModelSpec
from .divergence import extract_json
from .domain import DivergenceMap
from .llm import LLMClient
from .store import Session, Turn

logger = logging.getLogger(__name__)

COACH_SYSTEM = """你是一位决策对练教练。你的目标不是替用户做决定，而是通过提问引导用户自己把这个决策想清楚。

当前对练背景：
- 用户带来一个真实决策，多位AI顾问给出了不同建议
- 你拿到顾问们的分歧地图，要用真实分歧逼用户把关键权衡想透

## 引导轨道（每轮选最合适的一种）
**轨道A: 分歧探索**（顾问有分歧时优先）— 聚焦一个分歧点，让用户分析不同立场在他的处境里各自的分量
**轨道B: 假设拆解**（复杂决策或顾问一致时）— 拆隐含前提："这个选择成立的前提是什么？如果前提不成立呢？"
**轨道C: 反例挑战**（用户过于笃定时）— 给假想情形："如果出现X，你的选择还站得住吗？"
**轨道D: 迁移/What-if**（当前分歧聊透时）— 换框架检验："如果是你最佩服的人面对这个局面，他会怎么权衡？"
**轨道E: 证据追问**（用户有立场没论证时）— 追依据："支撑你这个判断的最硬的一条事实是什么？有没有反面证据？"

## 通用规则
1. 不替用户做决定，不宣布哪个顾问对
2. 每轮只用一个轨道，聚焦一个点
3. 用开放式问题引导（"你觉得…？"、"如果…会怎样？"、"有没有可能…？"）
4. 用户推理有漏洞时，用反例或追问引导，不直接纠正
5. 回复控制在 2-3 句话内，简洁有力

## 难度适配（按分歧点的 decision_ambiguity）
- factual：用轨道A或E，问题简单直接，必要时点明"这条其实可以去查证"
- tradeoff：用轨道A/B/E，帮用户把两边的代价都摆上桌
- values：先用轨道B拆前提降难度，再用A/C深入；先点破"这层没有客观对错，是你要什么"

## 共识前置（第一轮必须执行）
- 第一个引导问题必须先简要介绍顾问们的共识（"几位顾问都认为XX"）
- 然后引入最容易入手的分歧点（factual 优先，values 最后）
- 不要上来就抛最难的

## 卡住时（系统信号会告诉你用户卡住及原因）
- 优先换轨道，不原地加压
- too_hard：把当前分歧拆成更小的问题，或给一个贴近用户处境的具体例子
- no_progress：换一个分歧点，或用轨道D跳出当前框架
- 给提示要克制：提示是脚手架，不是答案

## 语气原则（挑战不是批判）
- 先肯定再挑战（"这个角度很实在"、"你抓到了关键"）
- 语气五字诀：真诚、坦率、温暖、积极、好奇
- 把困难归因于决策本身复杂，而非用户能力（"这个权衡确实容易让人犹豫"而非"你想错了"）
- 用好奇心引导而非权威纠正（"我很好奇，如果…"而非"其实正确的是…"）

输出格式（严格 JSON）：
{
  "guide_message": "你的引导问题（2-3句话）",
  "target_divergence_id": "当前聚焦的分歧点ID（无则空字符串）",
  "track": "A/B/C/D/E 之一"
}
注意：guide_message 文本内禁止出现英文双引号字符，引用一律用「」。"""


@dataclass
class CoachReply:
    message: str
    target_dp_id: str = ""
    track: str = "A"
    fallback: bool = False


def _salvage_guide_json(text: str) -> dict:
    """已知 schema 的宽容解析：模型在 guide_message 里塞未转义引号时，severe JSON
    解析会炸（M3.1 实测 Expecting ',' delimiter），用下一个键名当锚点把正文捞回来。"""
    import re

    # 锚点要求后接冒号（审查）：防 guide_message 正文里出现 `", "track"` 字样时误截断
    m = re.search(r'"guide_message"\s*:\s*"(.+?)"\s*,\s*"(?:target_divergence_id|track)"\s*:', text, re.S)
    if not m:
        raise ValueError("salvage 失败：找不到 guide_message")
    dp = re.search(r'"target_divergence_id"\s*:\s*"([^"\n]*)"', text)
    tr = re.search(r'"track"\s*:\s*"([A-Ea-e])"', text)
    return {
        "guide_message": m.group(1).strip(),
        "target_divergence_id": dp.group(1) if dp else "",
        "track": tr.group(1).upper() if tr else "A",
    }


def _map_block(dmap: DivergenceMap) -> str:
    lines = ["## 顾问共识"]
    lines += [f"- {c}" for c in dmap.consensus_points] or ["- （无明确共识）"]
    lines.append("\n## 分歧点（已按入手难度排序）")
    for p in dmap.sorted_points():
        lines.append(f"### [{p.id}] {p.topic}（{p.decision_ambiguity}）")
        lines.append(p.description)
        for pos in p.positions:
            who = "、".join(pos.models) if pos.models else "某顾问"
            lines.append(f"- 立场「{pos.stance}」（{who}）：{pos.summary}")
    return "\n".join(lines)


def _dialogue_block(session: Session, max_turns: int = 16) -> str:
    if not session.turns:
        return "（尚未开始）"
    lines = []
    for t in session.turns[-max_turns:]:
        role = t.role if isinstance(t, Turn) else t.get("role")
        text = t.text if isinstance(t, Turn) else t.get("text", "")
        lines.append(f"{'用户' if role == 'user' else '教练'}：{text}")
    return "\n".join(lines)


def _fallback_reply(
    dmap: DivergenceMap,
    first_round: bool,
    recent_coach_texts: tuple[str, ...] | list[str] = (),
    last_user_text: str = "",
) -> CoachReply:
    """JSON 解析失败的模板兜底（上游 同款防线）：对练不能因教练一次失手而死。

    防复读（M3.1 + 审查 收紧）：对比最近 3 条教练消息（不只上一条，
    拦住 A-B-A 隔轮复读），三级变体链：模板 → 引原话深挖 → 预演失败视角。
    """
    points = dmap.sorted_points()
    if not points:
        return CoachReply(message="顾问们的看法已经摆在桌上了。你第一直觉更倾向哪边？是什么让你倾向它？", fallback=True)
    p = points[0]
    sides = "和".join(f"「{pos.stance}」" for pos in p.positions[:2])
    if first_round and dmap.consensus_points:
        prefix = f"几位顾问都认同：{dmap.consensus_points[0].rstrip('。')}。但在"
    else:
        prefix = "我们聚焦在"

    seen = {t.strip() for t in recent_coach_texts if t.strip()}
    quoted = f"你刚说「{last_user_text.strip()[:40]}」——" if last_user_text.strip() else ""
    candidates = [
        (f"{prefix}「{p.topic}」上他们分成了{sides}。放到你的实际处境里，哪一边的代价你更付得起？为什么？", "A"),
        (f"{quoted}把这个判断往下拆一层：最让你下不了手的，具体是哪一件事？如果它三个月内不会变，你会怎么选？", "B"),
        ("换个角度预演一下：想象一年后这个决定的结果不理想，复盘时最可能发现是哪里出了问题？", "C"),
    ]
    for msg, track in candidates:
        if msg.strip() not in seen:
            return CoachReply(message=msg, target_dp_id=p.id, track=track, fallback=True)
    # 三级都用过（极端情形）：直接邀请进立场卡，不再空转
    return CoachReply(
        message="我们在这个点上已经绕了几圈。把你现在的立场和理由写进立场卡吧，看对照能不能把卡住的地方照出来。",
        target_dp_id=p.id,
        track="E",
        fallback=True,
    )


async def guide(
    client: LLMClient,
    spec: ModelSpec,
    session: Session,
    dmap: DivergenceMap,
    *,
    closure: ClosureResult | None = None,
    user_id: str = "",
) -> CoachReply:
    first_round = session.rounds_used == 0
    signals = [f"当前是第 {session.rounds_used + 1} 轮（上限 8 轮）"]
    if first_round:
        signals.append("这是第一轮：必须执行共识前置规则")
    if closure is not None:
        signals.append(f"收口判定器对用户上一条回复的判定：{closure.closure}（依据：{closure.basis or '无'}）")
        if closure.closure == "stuck":
            signals.append(f"用户卡住了，原因={closure.stuck_reason}。按'卡住时'规则处理：优先换轨道")
        if closure.closure == "shifted":
            signals.append(f"用户立场发生了变化（{closure.stance_summary}）。先确认这个新立场，再推进")

    user_block = (
        f"# 决策问题\n{session.question}\n\n"
        f"# 分歧地图\n{_map_block(dmap)}\n\n"
        f"# 对话至今\n{_dialogue_block(session)}\n\n"
        f"# 本轮系统信号\n" + "\n".join(f"- {s}" for s in signals)
    )
    # M3.1（Owner 实测抓到掉模板复读后修）：
    # ① thinking 变体先烧思考预算，原 500 token 上限把正文 JSON 拦腰截断 → 预算放开到 spec 默认
    # ② 重试覆盖到"解析失败"（原来只盖 API 错误）：生成→解析整体最多两次
    # ③ severe JSON 失败时先用已知 schema 宽容捞取（_salvage_guide_json），再算失败
    last_err: Exception | None = None
    for attempt in range(2):
        try:
            result = await client.call(
                spec,
                COACH_SYSTEM,
                user_block,
                role="coach",
                session_id=session.id,
                user_id=user_id,
                retryable=True,
            )
            try:
                data = extract_json(result.text)
            except ValueError:
                data = _salvage_guide_json(result.text)
            msg = str(data.get("guide_message", "")).strip()
            if not msg:
                raise ValueError("guide_message 为空")
            track = str(data.get("track", "A")).strip().upper()[:1]
            if track not in "ABCDE":
                track = "A"
            return CoachReply(
                message=msg,
                target_dp_id=str(data.get("target_divergence_id", "")).strip(),
                track=track,
            )
        except Exception as e:
            last_err = e
            logger.warning("教练生成第 %d/2 次失败（%s）", attempt + 1, e)

    logger.warning("教练两次生成均失败（%s），模板兜底", last_err)
    recent_coach = [
        (t.text if isinstance(t, Turn) else t.get("text", ""))
        for t in session.turns
        if (t.role if isinstance(t, Turn) else t.get("role")) == "coach"
    ][-3:]
    last_user = next(
        (
            (t.text if isinstance(t, Turn) else t.get("text", ""))
            for t in reversed(session.turns)
            if (t.role if isinstance(t, Turn) else t.get("role")) == "user"
        ),
        "",
    )
    return _fallback_reply(dmap, first_round, recent_coach, last_user)
