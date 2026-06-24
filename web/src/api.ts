/** API 客户端：SSE 用 fetch 流解析（上游 同款模式的精简版）。 */

export type GateResult = {
  passed: boolean;
  classification: string;
  decision_axis: string;
  confidence: number;
  rewrite_suggestion: string;
};

export type RespondResult = {
  type: "coach" | "suggest_stance";
  message: string;
  track?: string;
  rounds_used: number;
  closure?: string;
  suggest_stance?: boolean;
  stance_hint?: string;
};

export type Turn = { role: string; text: string; meta?: Record<string, any>; ts?: number };

export type SessionState = {
  session_id: string;
  question: string;
  state: string;
  rounds_used: number;
  divergence_map: any;
  turns: Turn[];
  user_stance: string;
  stance_reason: string;
  revealed: boolean;
  synthesis: string;
  comparison: any;
  memo_md: string;
};

const TOKEN_KEY = "sparring_token";

export const getToken = (): string | null => localStorage.getItem(TOKEN_KEY);
export const setToken = (t: string) => localStorage.setItem(TOKEN_KEY, t);
export const clearToken = () => localStorage.removeItem(TOKEN_KEY);
export const hasToken = (): boolean => !!getToken();

function headers(): Record<string, string> {
  const h: Record<string, string> = { "Content-Type": "application/json" };
  const t = getToken();
  if (t) h["Authorization"] = `Bearer ${t}`;
  return h;
}

async function jfetch<T>(url: string, init?: RequestInit): Promise<T> {
  const res = await fetch(url, { headers: headers(), ...init });
  if (!res.ok) {
    if (res.status === 401) {
      // 登录失效：清 token 回邀请码门（M4）
      clearToken();
      location.reload();
    }
    let detail = `HTTP ${res.status}`;
    try {
      detail = (await res.json()).detail ?? detail;
    } catch {
      /* keep default */
    }
    throw new Error(detail);
  }
  return res.json() as Promise<T>;
}

export const precheck = (question: string) =>
  jfetch<GateResult>("/api/sparring/precheck", { method: "POST", body: JSON.stringify({ question }) });

export const respond = (id: string, text: string) =>
  jfetch<RespondResult>(`/api/sparring/${id}/respond`, { method: "POST", body: JSON.stringify({ text }) });

export const submitStance = (id: string, stance: string, reason: string) =>
  jfetch<{ ok: boolean }>(`/api/sparring/${id}/stance`, { method: "POST", body: JSON.stringify({ stance, reason }) });

export const reveal = (id: string) =>
  jfetch<{ synthesis: string; comparison: any; memo_md: string; user_stance: string; state: string }>(
    `/api/sparring/${id}/reveal`,
    { method: "POST" },
  );

export const getSession = (id: string) => jfetch<SessionState>(`/api/sparring/${id}`);

export const history = () => jfetch<{ sessions: any[] }>("/api/history");

export const redeem = (code: string) =>
  jfetch<{ token: string; user_id: string }>("/api/auth/redeem", {
    method: "POST",
    body: JSON.stringify({ code }),
  });

export type ProfileData = {
  consent: boolean;
  observations: { behavior_tag: string; dimension: string; repeat_count: number; recent_evidence: string }[];
  quota_remaining: number;
};

export const getProfile = () => jfetch<ProfileData>("/api/profile");

export const setConsent = (consent: boolean) =>
  jfetch<{ consent: boolean }>("/api/profile/consent", { method: "POST", body: JSON.stringify({ consent }) });

export const deleteObservations = () =>
  jfetch<{ deleted: number }>("/api/profile/observations", { method: "DELETE" });

export type SSEHandler = (event: string, data: any) => void;

/** POST + ReadableStream 解析 SSE（event:/data: 行协议）。
 * 终局校验（审查）：流结束时若没收到 coach_ready 或 error，视为中断并抛错，
 * 不让用户无声卡在进度屏。 */
export async function startStream(question: string, onEvent: SSEHandler): Promise<void> {
  const res = await fetch("/api/sparring/start/stream", {
    method: "POST",
    headers: headers(),
    body: JSON.stringify({ question }),
  });
  if (!res.ok || !res.body) {
    let detail = `HTTP ${res.status}`;
    try {
      detail = (await res.json()).detail ?? detail;
    } catch {
      /* keep default */
    }
    throw new Error(detail);
  }
  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buf = "";
  let event = "";
  let terminal = false;
  const dispatch = (name: string, data: any) => {
    if (name === "coach_ready" || name === "error") terminal = true;
    onEvent(name, data);
  };
  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    buf += decoder.decode(value, { stream: true });
    const lines = buf.split("\n");
    buf = lines.pop() ?? "";
    for (const line of lines) {
      if (line.startsWith("event:")) {
        event = line.slice(6).trim();
      } else if (line.startsWith("data:") && event) {
        try {
          dispatch(event, JSON.parse(line.slice(5).trim()));
        } catch {
          /* 忽略坏帧 */
        }
      }
    }
  }
  if (!terminal) throw new Error("启动流中断（未收到教练就位），请返回重试");
}
