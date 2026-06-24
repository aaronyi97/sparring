import { useEffect, useState } from "react";
import { History as HistoryIcon, Plus, Swords } from "lucide-react";
import { hasToken } from "./api";
import NewSparring from "./views/NewSparring";
import Session from "./views/Session";
import History from "./views/History";
import InviteGate from "./views/InviteGate";

type View = { name: "new" } | { name: "session"; id: string; question?: string } | { name: "history" };

function parseHash(): View {
  const h = window.location.hash;
  if (h.startsWith("#/s/")) return { name: "session", id: h.slice(4) };
  if (h === "#/history") return { name: "history" };
  return { name: "new" };
}

export default function App() {
  const [view, setView] = useState<View>(parseHash);
  const [authed, setAuthed] = useState(hasToken());

  useEffect(() => {
    const onHash = () => setView(parseHash());
    window.addEventListener("hashchange", onHash);
    return () => window.removeEventListener("hashchange", onHash);
  }, []);

  if (!authed) return <InviteGate onAuthed={() => setAuthed(true)} />;

  const go = (hash: string) => {
    if (window.location.hash === hash) setView(parseHash());
    else window.location.hash = hash;
  };

  return (
    <div className="min-h-screen">
      <header className="sticky top-0 z-10 border-b border-border bg-background/80 backdrop-blur">
        <div className="mx-auto flex h-14 max-w-3xl items-center justify-between px-4">
          <button onClick={() => go("#/")} className="flex items-center gap-2 font-semibold tracking-wide">
            <Swords className="h-5 w-5" />
            对练场
          </button>
          <nav className="flex items-center gap-1 text-sm">
            <button
              onClick={() => go("#/")}
              className="flex items-center gap-1.5 rounded-md px-3 py-1.5 text-muted-foreground hover:bg-secondary hover:text-foreground"
            >
              <Plus className="h-4 w-4" /> 新对练
            </button>
            <button
              onClick={() => go("#/history")}
              className="flex items-center gap-1.5 rounded-md px-3 py-1.5 text-muted-foreground hover:bg-secondary hover:text-foreground"
            >
              <HistoryIcon className="h-4 w-4" /> 历史
            </button>
          </nav>
        </div>
      </header>
      <main className="mx-auto max-w-3xl px-4 pb-24 pt-8">
        {view.name === "new" && (
          <NewSparring
            onStart={(question) => setView({ name: "session", id: "", question })}
          />
        )}
        {view.name === "session" && (
          <Session
            id={view.id}
            question={view.question}
            onSessionId={(id) => {
              window.history.replaceState(null, "", `#/s/${id}`);
            }}
          />
        )}
        {view.name === "history" && <History onOpen={(id) => go(`#/s/${id}`)} />}
      </main>
    </div>
  );
}
