import { useState } from "react";
import { ArrowRight, Lightbulb, Loader2 } from "lucide-react";
import { precheck } from "../api";

const EXAMPLES = [
  "要不要接受这个降薪 30% 但股权更多的 offer？",
  "我的副业月入已到主业一半，该不该辞职全职做？",
  "产品该按席位收费还是按用量收费？",
];

export default function NewSparring({ onStart }: { onStart: (question: string) => void }) {
  const [q, setQ] = useState("");
  const [busy, setBusy] = useState(false);
  const [gateMsg, setGateMsg] = useState<{ suggestion: string } | null>(null);
  const [err, setErr] = useState("");

  const submit = async () => {
    const question = q.trim();
    if (!question || busy) return;
    setBusy(true);
    setErr("");
    setGateMsg(null);
    try {
      const g = await precheck(question);
      if (!g.passed) {
        setGateMsg({ suggestion: g.rewrite_suggestion || "试着把它改成一个取舍问题。" });
      } else {
        onStart(question);
      }
    } catch (e: any) {
      setErr(e.message ?? String(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="space-y-8">
      <div className="space-y-3 pt-8 text-center">
        <h1 className="text-3xl font-bold tracking-tight">重大决策前，先对练一场</h1>
        <p className="mx-auto max-w-xl text-muted-foreground">
          三个不同家族的 AI 顾问先独立给出建议并亮出分歧，再由教练拿着真实吵点逼你想清楚——最后你带走一份决策备忘录。
        </p>
      </div>

      <div className="rounded-xl border border-border bg-card p-4">
        <textarea
          value={q}
          onChange={(e) => setQ(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) submit();
          }}
          rows={4}
          placeholder="写下你正在纠结的真实决策（取舍题，不是查事实）…"
          className="w-full resize-none bg-transparent text-base outline-none placeholder:text-muted-foreground"
        />
        <div className="mt-3 flex items-center justify-between">
          <span className="text-xs text-muted-foreground">⌘+Enter 开始</span>
          <button
            onClick={submit}
            disabled={busy || !q.trim()}
            className="flex items-center gap-2 rounded-lg bg-primary px-4 py-2 text-sm font-medium text-primary-foreground disabled:opacity-40"
          >
            {busy ? <Loader2 className="h-4 w-4 animate-spin" /> : <ArrowRight className="h-4 w-4" />}
            开始对练
          </button>
        </div>
      </div>

      {gateMsg && (
        <div className="rounded-xl border border-border bg-secondary/60 p-4 text-sm">
          <div className="mb-2 flex items-center gap-2 font-medium">
            <Lightbulb className="h-4 w-4" /> 这更像一道查事实题，对练不出东西
          </div>
          <p className="text-muted-foreground">{gateMsg.suggestion}</p>
          <button
            onClick={() => {
              setQ(gateMsg.suggestion);
              setGateMsg(null);
            }}
            className="mt-3 rounded-md bg-primary px-3 py-1.5 text-xs font-medium text-primary-foreground"
          >
            用这个改写
          </button>
        </div>
      )}

      {err && <p className="text-sm text-destructive">{err}</p>}

      <div className="space-y-2">
        <p className="text-xs uppercase tracking-widest text-muted-foreground">试试这些</p>
        <div className="flex flex-wrap gap-2">
          {EXAMPLES.map((ex) => (
            <button
              key={ex}
              onClick={() => setQ(ex)}
              className="rounded-full border border-border px-3 py-1.5 text-xs text-muted-foreground hover:bg-secondary hover:text-foreground"
            >
              {ex}
            </button>
          ))}
        </div>
      </div>
    </div>
  );
}
