import { useEffect, useRef, useState } from "react";
import {
  Brain,
  Check,
  Copy,
  Download,
  Flag,
  Loader2,
  Network,
  Send,
  Sparkles,
  Users,
} from "lucide-react";
import Markdown from "react-markdown";
import { getSession, respond, reveal, startStream, submitStance, type SessionState } from "../api";

const TRACKS: Record<string, string> = {
  A: "分歧探索",
  B: "假设拆解",
  C: "反例挑战",
  D: "迁移检验",
  E: "证据追问",
};

const PHASES = [
  { key: "recruiting", label: "召集顾问团", icon: Users },
  { key: "thinking", label: "顾问独立作答", icon: Brain },
  { key: "analyzing", label: "提取分歧地图", icon: Network },
  { key: "coach_ready", label: "教练就位", icon: Sparkles },
];

type Props = { id: string; question?: string; onSessionId: (id: string) => void };

export default function Session({ id, question, onSessionId }: Props) {
  const [phaseIdx, setPhaseIdx] = useState(0);
  const [phaseInfo, setPhaseInfo] = useState<{ contributors?: string[]; arrived?: number; total?: number }>({});
  const [s, setS] = useState<SessionState | null>(null);
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");
  const [stanceOpen, setStanceOpen] = useState(false);
  const [stanceHint, setStanceHint] = useState("");
  const [stance, setStance] = useState("");
  const [reason, setReason] = useState("");
  const [copied, setCopied] = useState(false);
  const [showConsensus, setShowConsensus] = useState(false);
  const bottomRef = useRef<HTMLDivElement>(null);
  const startedRef = useRef(false);

  const load = (sid: string) => getSession(sid).then(setS);

  useEffect(() => {
    if (id) {
      load(id).catch((e) => setErr(e.message));
      return;
    }
    if (!question || startedRef.current) return;
    startedRef.current = true; // StrictMode 双跑防线：真实扇出只准跑一次
    let sid = "";
    startStream(question, (event, data) => {
      const idx = PHASES.findIndex((p) => p.key === event);
      if (idx >= 0) setPhaseIdx(idx);
      if (event === "recruiting") {
        // 拿到 session_id 立刻写 URL（审查）：phase1 期间刷新也不丢会话
        sid = data.session_id;
        onSessionId(sid);
        setPhaseInfo((p) => ({ ...p, contributors: data.contributors }));
      }
      if (event === "analyzing") setPhaseInfo((p) => ({ ...p, arrived: data.arrived, total: data.total }));
      if (event === "coach_ready") {
        sid = data.session_id;
      }
      if (event === "error") setErr(data.message);
    })
      .then(() => (sid ? load(sid) : undefined))
      .catch((e) => setErr(e.message));
  }, [id, question]);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [s?.turns.length, stanceOpen]);

  if (err)
    return (
      <div className="space-y-4 pt-8">
        <p className="text-sm text-destructive">{err}</p>
        <a href="#/" className="inline-block rounded-lg border border-border px-4 py-2 text-sm text-muted-foreground hover:text-foreground">
          返回重新开始
        </a>
      </div>
    );

  // ── Phase 1 进度屏 ──
  if (!s) {
    return (
      <div className="space-y-6 pt-10">
        <p className="text-center text-sm text-muted-foreground">{question}</p>
        <div className="mx-auto max-w-md space-y-3">
          {PHASES.map((p, i) => {
            const Icon = p.icon;
            const active = i === phaseIdx;
            const done = i < phaseIdx;
            return (
              <div
                key={p.key}
                className={`flex items-center gap-3 rounded-xl border p-3 ${
                  active ? "border-ring bg-card" : "border-border bg-card/40"
                } ${done ? "opacity-70" : ""}`}
              >
                {active ? (
                  <Loader2 className="h-4 w-4 animate-spin" />
                ) : done ? (
                  <Check className="h-4 w-4 text-muted-foreground" />
                ) : (
                  <Icon className="h-4 w-4 text-muted-foreground" />
                )}
                <span className={`text-sm ${active ? "" : "text-muted-foreground"}`}>{p.label}</span>
                {p.key === "recruiting" && phaseInfo.contributors && (
                  <span className="ml-auto truncate text-xs text-muted-foreground">
                    {phaseInfo.contributors.join(" · ")}
                  </span>
                )}
                {p.key === "analyzing" && phaseInfo.arrived != null && (
                  <span className="ml-auto text-xs text-muted-foreground">
                    {phaseInfo.arrived}/{phaseInfo.total} 位顾问已到
                  </span>
                )}
              </div>
            );
          })}
        </div>
        <p className="text-center text-xs text-muted-foreground">约需 20-40 秒：顾问们正在真实作答，不是转圈动画</p>
      </div>
    );
  }

  const dmap = s.divergence_map ?? { consensus_points: [], divergence_points: [] };
  const revealed = s.state === "revealed";

  // 启动中断后被刷新捞回的残局（审查）：明确告知，不留无声死页
  if (s.state === "phase1") {
    return (
      <div className="space-y-4 pt-8 text-center">
        <p className="text-sm text-muted-foreground">这一局的启动没有完成（可能已中断），无法继续。</p>
        <a href="#/" className="inline-block rounded-lg bg-primary px-4 py-2 text-sm font-medium text-primary-foreground">
          回首页重新开始
        </a>
      </div>
    );
  }

  const send = async () => {
    const text = input.trim();
    if (!text || busy) return;
    setBusy(true);
    setErr("");
    try {
      const r = await respond(s.session_id, text);
      if (r.type === "suggest_stance") {
        setStanceOpen(true);
        setStanceHint(r.stance_hint ?? "");
      }
      await load(s.session_id);
      if (r.type === "suggest_stance") setS((prev) => prev && { ...prev });
      setInput("");
    } catch (e: any) {
      setErr(e.message);
    } finally {
      setBusy(false);
    }
  };

  const doStance = async () => {
    if (!stance.trim() || busy) return;
    setBusy(true);
    setErr("");
    try {
      await submitStance(s.session_id, stance, reason);
      await reveal(s.session_id);
      await load(s.session_id);
      setStanceOpen(false);
    } catch (e: any) {
      setErr(e.message);
    } finally {
      setBusy(false);
    }
  };

  const copyMemo = async () => {
    await navigator.clipboard.writeText(s.memo_md);
    setCopied(true);
    setTimeout(() => setCopied(false), 1500);
  };

  return (
    <div className="space-y-6">
      {/* 题面 + 分歧地图概览 */}
      <div className="rounded-xl border border-border bg-card p-4">
        <p className="text-sm font-medium">{s.question}</p>
        {dmap.divergence_points?.length > 0 && (
          <div className="mt-3 flex flex-wrap gap-1.5">
            {dmap.divergence_points.map((p: any) => (
              <span key={p.id} className="rounded-full bg-secondary px-2.5 py-1 text-xs text-secondary-foreground">
                {p.topic}
              </span>
            ))}
          </div>
        )}
        {dmap.consensus_points?.length > 0 && (
          <div className="mt-3">
            <button
              onClick={() => setShowConsensus((v) => !v)}
              className="text-xs text-muted-foreground underline-offset-2 hover:text-foreground hover:underline"
            >
              顾问共识 {dmap.consensus_points.length} 条 · {showConsensus ? "收起" : "展开"}
            </button>
            {showConsensus && (
              <ul className="mt-2 space-y-1.5 border-l-2 border-border pl-3 text-xs leading-relaxed text-muted-foreground">
                {dmap.consensus_points.map((c: string, i: number) => (
                  <li key={i}>{c}</li>
                ))}
              </ul>
            )}
          </div>
        )}
      </div>

      {/* 对练时间线 */}
      <div className="space-y-3">
        {s.turns.map((t, i) =>
          t.role === "user" ? (
            <div key={i} className="flex justify-end">
              <div className="max-w-[85%] rounded-2xl rounded-br-sm bg-primary px-4 py-2.5 text-sm text-primary-foreground">
                {t.text}
              </div>
            </div>
          ) : (
            <div key={i} className="flex justify-start">
              <div className="max-w-[85%] rounded-2xl rounded-bl-sm border border-border bg-card px-4 py-2.5 text-sm">
                {t.meta?.track && (
                  <span className="mb-1 block text-[10px] uppercase tracking-widest text-muted-foreground">
                    教练 · 轨道{t.meta.track} {TRACKS[t.meta.track] ?? ""}
                  </span>
                )}
                {t.text}
              </div>
            </div>
          ),
        )}
      </div>

      {/* 输入区 / 立场卡 / 揭示区 */}
      {!revealed && s.state === "coaching" && !stanceOpen && (
        <div className="rounded-xl border border-border bg-card p-3">
          <textarea
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && !e.shiftKey) {
                e.preventDefault();
                send();
              }
            }}
            rows={2}
            placeholder="说出你的想法…（Enter 发送）"
            className="w-full resize-none bg-transparent text-sm outline-none placeholder:text-muted-foreground"
          />
          {busy && (
            <p className="mt-1 animate-pulse text-xs text-muted-foreground">
              教练正在想怎么接你这一手（约 10-30 秒）…
            </p>
          )}
          <div className="mt-2 flex items-center justify-between">
            <span className="text-xs text-muted-foreground">第 {s.rounds_used}/8 轮</span>
            <div className="flex gap-2">
              <button
                onClick={() => setStanceOpen(true)}
                className="flex items-center gap-1.5 rounded-lg border border-border px-3 py-1.5 text-xs text-muted-foreground hover:text-foreground"
              >
                <Flag className="h-3.5 w-3.5" /> 我想清楚了，写立场卡
              </button>
              <button
                onClick={send}
                disabled={busy || !input.trim()}
                className="flex items-center gap-1.5 rounded-lg bg-primary px-3 py-1.5 text-xs font-medium text-primary-foreground disabled:opacity-40"
              >
                {busy ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Send className="h-3.5 w-3.5" />}
                发送
              </button>
            </div>
          </div>
        </div>
      )}

      {!revealed && (stanceOpen || s.state === "stance") && (
        <div className="rounded-xl border-2 border-ring bg-card p-4">
          <div className="mb-3 flex items-center gap-2 text-sm font-semibold">
            <Flag className="h-4 w-4" /> 立场卡 — 揭示前先表态
          </div>
          {stanceHint && <p className="mb-2 text-xs text-muted-foreground">教练听到的你的立场：{stanceHint}</p>}
          <input
            value={stance}
            onChange={(e) => setStance(e.target.value)}
            placeholder="我的结论是…（「我还没想清楚 + 卡在哪」也是合法立场）"
            className="mb-2 w-full rounded-lg bg-input px-3 py-2 text-sm outline-none"
          />
          <textarea
            value={reason}
            onChange={(e) => setReason(e.target.value)}
            rows={2}
            placeholder="我的理由是…"
            className="w-full resize-none rounded-lg bg-input px-3 py-2 text-sm outline-none"
          />
          <div className="mt-3 flex justify-end gap-2">
            {s.state === "coaching" && (
              <button
                onClick={() => setStanceOpen(false)}
                className="rounded-lg border border-border px-3 py-1.5 text-xs text-muted-foreground"
              >
                再聊聊
              </button>
            )}
            <button
              onClick={doStance}
              disabled={busy || !stance.trim()}
              className="flex items-center gap-1.5 rounded-lg bg-primary px-4 py-1.5 text-xs font-medium text-primary-foreground disabled:opacity-40"
            >
              {busy ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Sparkles className="h-3.5 w-3.5" />}
              提交并揭示
            </button>
          </div>
        </div>
      )}

      {revealed && (
        <div className="space-y-4">
          <div className="rounded-xl border border-border bg-card p-4">
            <p className="mb-1 text-xs uppercase tracking-widest text-muted-foreground">你的立场</p>
            <p className="text-sm font-medium">{s.user_stance}</p>
            {s.stance_reason && <p className="mt-1 text-xs text-muted-foreground">{s.stance_reason}</p>}
          </div>

          <div className="rounded-xl border border-border bg-card p-4">
            <p className="mb-2 text-xs uppercase tracking-widest text-muted-foreground">顾问综合建议</p>
            <div className="prose-md text-sm">
              <Markdown>{s.synthesis}</Markdown>
            </div>
          </div>

          {s.comparison && (
            <div className="grid gap-3 sm:grid-cols-2">
              {s.comparison.you_saw?.length > 0 && (
                <CompareCard title="你看到了顾问没看到的" items={s.comparison.you_saw} />
              )}
              {s.comparison.you_missed?.length > 0 && (
                <CompareCard title="你可能漏掉的" items={s.comparison.you_missed} />
              )}
              {s.comparison.next_checks?.length > 0 && (
                <CompareCard title="落地前先验证" items={s.comparison.next_checks} />
              )}
              {s.comparison.key_difference && (
                <CompareCard title="与综合建议的关键差异" items={[s.comparison.key_difference]} />
              )}
            </div>
          )}

          <div className="rounded-xl border border-border bg-card p-4">
            <div className="mb-2 flex items-center justify-between">
              <p className="text-xs uppercase tracking-widest text-muted-foreground">决策备忘录</p>
              <div className="flex gap-2">
                <button
                  onClick={copyMemo}
                  className="flex items-center gap-1 rounded-md border border-border px-2.5 py-1 text-xs text-muted-foreground hover:text-foreground"
                >
                  {copied ? <Check className="h-3 w-3" /> : <Copy className="h-3 w-3" />}
                  {copied ? "已复制" : "复制"}
                </button>
                <button
                  onClick={() => {
                    // 即用即建即收（审查）：不在 render 里建 Blob URL，用完 revoke
                    const url = URL.createObjectURL(new Blob([s.memo_md], { type: "text/markdown" }));
                    const a = document.createElement("a");
                    a.href = url;
                    a.download = "决策备忘录.md";
                    a.click();
                    setTimeout(() => URL.revokeObjectURL(url), 1000);
                  }}
                  className="flex items-center gap-1 rounded-md border border-border px-2.5 py-1 text-xs text-muted-foreground hover:text-foreground"
                >
                  <Download className="h-3 w-3" /> 下载
                </button>
              </div>
            </div>
            <div className="prose-md max-h-96 overflow-y-auto text-sm">
              <Markdown>{s.memo_md}</Markdown>
            </div>
          </div>
        </div>
      )}

      <div ref={bottomRef} />
    </div>
  );
}

function CompareCard({ title, items }: { title: string; items: string[] }) {
  return (
    <div className="rounded-xl border border-border bg-card p-4">
      <p className="mb-2 text-xs uppercase tracking-widest text-muted-foreground">{title}</p>
      <ul className="space-y-1.5 text-sm">
        {items.map((x, i) => (
          <li key={i} className="leading-relaxed">
            {x}
          </li>
        ))}
      </ul>
    </div>
  );
}
