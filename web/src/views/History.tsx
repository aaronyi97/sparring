import { useEffect, useState } from "react";
import { Brain, ChevronRight, Loader2, Trash2 } from "lucide-react";
import { deleteObservations, getProfile, history, setConsent, type ProfileData } from "../api";

const STATE_LABEL: Record<string, string> = {
  phase1: "准备中",
  coaching: "对练中",
  stance: "已表态",
  revealed: "已揭示",
};

export default function History({ onOpen }: { onOpen: (id: string) => void }) {
  const [items, setItems] = useState<any[] | null>(null);
  const [profile, setProfile] = useState<ProfileData | null>(null);
  const [err, setErr] = useState("");

  const loadProfile = () => getProfile().then(setProfile).catch(() => {});

  useEffect(() => {
    history()
      .then((r) => setItems(r.sessions))
      .catch((e) => setErr(e.message));
    loadProfile();
  }, []);

  const toggleConsent = async (v: boolean) => {
    await setConsent(v);
    loadProfile();
  };
  const clearObs = async () => {
    await deleteObservations();
    loadProfile();
  };

  if (err) return <p className="text-sm text-destructive">{err}</p>;
  if (!items)
    return (
      <div className="flex justify-center pt-16">
        <Loader2 className="h-5 w-5 animate-spin text-muted-foreground" />
      </div>
    );

  return (
    <div className="space-y-8">
      {/* 思维观察画像（M4 · 跨会话聚合 · 定性无评分）*/}
      <section className="space-y-3">
        <div className="flex items-center justify-between">
          <h2 className="flex items-center gap-2 text-lg font-semibold">
            <Brain className="h-5 w-5" /> 我的思维观察
          </h2>
          {profile && (
            <label className="flex items-center gap-1.5 text-xs text-muted-foreground">
              <input
                type="checkbox"
                checked={profile.consent}
                onChange={(e) => toggleConsent(e.target.checked)}
                className="accent-primary"
              />
              开启跨会话观察
            </label>
          )}
        </div>
        {profile && !profile.consent && (
          <p className="text-sm text-muted-foreground">
            默认关闭。开启后，每次对练会沉淀你的思维倾向（只描述行为，不打分），并保存你的原话片段作为依据——可随时一键清空。
          </p>
        )}
        {profile && profile.consent && profile.observations.length === 0 && (
          <p className="text-sm text-muted-foreground">还没有累积观察。多对练几局，这里会浮现你反复出现的思维模式。</p>
        )}
        {profile && profile.observations.length > 0 && (
          <div className="space-y-2">
            {profile.observations.map((o) => (
              <div key={o.behavior_tag} className="rounded-xl border border-border bg-card p-3">
                <div className="flex items-center justify-between">
                  <span className="text-sm font-medium">{o.behavior_tag}</span>
                  <span className="text-xs text-muted-foreground">
                    {o.dimension} · 出现 {o.repeat_count} 次
                  </span>
                </div>
                {o.recent_evidence && <p className="mt-1 text-xs text-muted-foreground">最近：“{o.recent_evidence}”</p>}
              </div>
            ))}
            <button
              onClick={clearObs}
              className="flex items-center gap-1.5 text-xs text-muted-foreground hover:text-destructive"
            >
              <Trash2 className="h-3.5 w-3.5" /> 清空我的观察数据
            </button>
          </div>
        )}
      </section>

      {/* 对练历史 */}
      <section className="space-y-3">
        <h2 className="text-lg font-semibold">对练历史</h2>
        {items.length === 0 ? (
          <p className="text-sm text-muted-foreground">还没有对练记录。</p>
        ) : (
          items.map((it) => (
            <button
              key={it.id}
              onClick={() => onOpen(it.id)}
              className="flex w-full items-center justify-between gap-3 rounded-xl border border-border bg-card p-4 text-left hover:bg-secondary/50"
            >
              <div className="min-w-0">
                <p className="truncate text-sm">{it.question}</p>
                <p className="mt-1 text-xs text-muted-foreground">
                  {STATE_LABEL[it.state] ?? it.state} · {it.rounds_used} 轮
                  {it.user_stance ? ` · ${it.user_stance.slice(0, 30)}` : ""}
                </p>
              </div>
              <ChevronRight className="h-4 w-4 shrink-0 text-muted-foreground" />
            </button>
          ))
        )}
      </section>
    </div>
  );
}
