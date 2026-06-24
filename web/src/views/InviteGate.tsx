import { useState } from "react";
import { KeyRound, Loader2, Swords } from "lucide-react";
import { redeem, setToken } from "../api";

/** 邀请码门（M4）：无 token 时挡在最外层，换取服务端签发的登录态。 */
export default function InviteGate({ onAuthed }: { onAuthed: () => void }) {
  const [code, setCode] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");

  const submit = async () => {
    const c = code.trim();
    if (!c || busy) return;
    setBusy(true);
    setErr("");
    try {
      const r = await redeem(c);
      setToken(r.token);
      onAuthed();
    } catch (e: any) {
      setErr(e.message ?? String(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="flex min-h-screen items-center justify-center px-4">
      <div className="w-full max-w-sm space-y-6">
        <div className="space-y-2 text-center">
          <Swords className="mx-auto h-8 w-8" />
          <h1 className="text-2xl font-bold tracking-tight">对练场</h1>
          <p className="text-sm text-muted-foreground">重大决策前，先对练一场。当前为邀请制。</p>
        </div>
        <div className="rounded-xl border border-border bg-card p-4">
          <label className="mb-2 flex items-center gap-2 text-xs uppercase tracking-widest text-muted-foreground">
            <KeyRound className="h-3.5 w-3.5" /> 邀请码
          </label>
          <input
            value={code}
            onChange={(e) => setCode(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && submit()}
            placeholder="sp-xxxxxxxxxx"
            autoFocus
            className="w-full rounded-lg bg-input px-3 py-2 text-sm outline-none placeholder:text-muted-foreground"
          />
          {err && <p className="mt-2 text-xs text-destructive">{err}</p>}
          <button
            onClick={submit}
            disabled={busy || !code.trim()}
            className="mt-3 flex w-full items-center justify-center gap-2 rounded-lg bg-primary py-2 text-sm font-medium text-primary-foreground disabled:opacity-40"
          >
            {busy ? <Loader2 className="h-4 w-4 animate-spin" /> : null}
            进入
          </button>
        </div>
      </div>
    </div>
  );
}
