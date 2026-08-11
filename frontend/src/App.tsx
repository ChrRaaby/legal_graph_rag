import { useCallback, useEffect, useState } from "react";
import {
  fetchArchitecture, fetchTraces, fetchTrace, postFeedback, setProvider, setAppMode,
  type Architecture, type TraceSummary,
} from "./lib/api";
import type { Citation } from "./lib/events";
import { useAgentRun } from "./lib/useAgentRun";
import { useRunClock } from "./lib/useRunClock";
import Chat from "./components/Chat";
import Maskinrummet, { type TabId } from "./components/Maskinrummet";

type Theme = "auto" | "dark" | "light";
const nf = new Intl.NumberFormat("da-DK");

export default function App() {
  const [arch, setArch] = useState<Architecture | null>(null);
  const [archError, setArchError] = useState<string | null>(null);
  const [theme, setTheme] = useState<Theme>("auto");
  const [revealed, setRevealed] = useState(false);
  const [tab, setTab] = useState<TabId>("kredslob");
  const [selectedNodeId, setSelectedNodeId] = useState<string | null>(null);
  const [traces, setTraces] = useState<TraceSummary[]>([]);

  const run = useAgentRun();
  const clock = useRunClock(run.phase, run.log, run.runId);

  useEffect(() => {
    fetchArchitecture().then(setArch).catch((e) => setArchError(String(e)));
  }, []);

  // Refresh the history list whenever a run settles.
  useEffect(() => {
    if (run.phase === "replay") fetchTraces().then(setTraces).catch(() => {});
  }, [run.phase, run.runId]);

  useEffect(() => {
    const root = document.documentElement;
    if (theme === "auto") root.removeAttribute("data-theme");
    else root.setAttribute("data-theme", theme);
  }, [theme]);

  const cycleTheme = () =>
    setTheme((t) => (t === "auto" ? "dark" : t === "dark" ? "light" : "auto"));

  const onCiteClick = useCallback((c: Citation) => {
    if (!c.element_id) return;
    setRevealed(true);
    setTab("graflinse");
    setSelectedNodeId(c.element_id);
  }, []);

  const onSelectNode = useCallback((id: string | null) => setSelectedNodeId(id), []);
  const onFeedback = useCallback((v: "up" | "down") => {
    postFeedback(run.runId, v).catch(() => {});
  }, [run.runId]);

  const onSend = useCallback((q: string) => {
    setSelectedNodeId(null);
    run.ask(q);
  }, [run]);

  const loadHistory = useCallback((runId: string) => {
    if (!runId) return;
    setSelectedNodeId(null);
    fetchTrace(runId).then(run.loadTrace).catch(() => {});
  }, [run]);

  /** Load an eval run into the lenses and jump to Kredsløbet — the same
   *  inspection a real chat turn gets. Only smoke runs carry a run_id (their
   *  events are persisted); CLI runs have no event log to replay. */
  const inspectRun = useCallback((runId: string) => {
    loadHistory(runId);
    setTab("kredslob");
  }, [loadHistory]);

  if (archError) {
    return (
      <div className="app">
        <header><div className="brand"><span className="par">§</span>Skattegraf</div></header>
        <div className="err">Kunne ikke indlæse systemet: {archError}</div>
      </div>
    );
  }
  if (!arch) {
    return (
      <div className="app">
        <div className="thinking-line" style={{ marginTop: 40 }}>
          <span className="dot" />Indlæser Maskinrummet …
        </div>
      </div>
    );
  }

  const isDev = arch.app_mode === "dev";
  const showMaskinrum = isDev || revealed;
  const themeLabel = theme === "auto" ? "◑ auto" : theme === "dark" ? "☾ mørk" : "☀ lys";
  const question = [...run.messages].reverse().find((m) => m.role === "user")?.content ?? "";
  const answer = [...run.messages].reverse().find((m) => m.role === "assistant")?.content ?? "";

  return (
    <div className="app">
      <header>
        <div className="brand">
          <span className="par">§</span>Skattegraf
          {isDev && <small>MASKINRUMMET</small>}
        </div>
        <div className="badges">
          {isDev && traces.length > 0 && (
            <select
              className="history"
              value=""
              onChange={(e) => loadHistory(e.target.value)}
              aria-label="Tidligere kørsler"
            >
              <option value="">Historik ({traces.length})…</option>
              {traces.map((tr) => (
                <option key={tr.run_id} value={tr.run_id}>
                  {tr.question.slice(0, 46)} · {tr.latency_s.toFixed(0)}s
                </option>
              ))}
            </select>
          )}
          {isDev && (
            <span className="badge">
              LLM{" "}
              <select
                style={{
                  background: "transparent",
                  color: "inherit",
                  border: "none",
                  outline: "none",
                  fontWeight: "bold",
                  cursor: "pointer",
                }}
                value={arch.provider}
                onChange={(e) => {
                  const newProvider = e.target.value;
                  setArch({ ...arch, provider: newProvider });
                  setProvider(newProvider).catch(() => {
                    // Revert on error
                    setArch({ ...arch });
                  });
                }}
                aria-label="Vælg LLM model"
              >
                <option value="gemini:gemini-3.5-flash-lite">gemini-3.5-flash-lite</option>
                <option value="gemini:gemini-3.6-flash">gemini-3.6-flash</option>
                <option value="gemini:gemini-3.1-pro">gemini-3.1-pro</option>
                <option value="gemini:gemma-4-31b-it">gemma-4-31b-it (Gemini API)</option>
                <option value="gemini:gemma-4-26b-a4b-it">gemma-4-26b-a4b-it (Gemini API)</option>
                <option value="ollama">gemma4:26b (Ollama)</option>
              </select>
            </span>
          )}
          <span className="badge">
            Graf <b>{nf.format(arch.graph_stats.legislation)} love · {nf.format(arch.graph_stats.sections)} §§</b>
          </span>
          <button className="theme-toggle" onClick={cycleTheme} aria-label="Skift tema">{themeLabel}</button>
          <button 
            className="badge mode" 
            onClick={() => {
              const newMode = arch.app_mode === "dev" ? "user" : "dev";
              setArch({ ...arch, app_mode: newMode });
              setAppMode(newMode).catch(() => setArch({ ...arch }));
            }}
            style={{ cursor: "pointer", border: "none", outline: "none", background: "transparent" }}
            aria-label="Skift tilstand"
          >
            {arch.app_mode === "dev" ? "dev-tilstand" : "user-tilstand"}
          </button>
        </div>
      </header>

      <div className={`split${showMaskinrum ? "" : " user"}`}>
        <Chat
          messages={run.messages}
          phase={run.phase}
          error={run.error}
          citations={run.citations}
          onSend={onSend}
          onCiteClick={onCiteClick}
          onFeedback={onFeedback}
          showReveal={!isDev && !revealed && run.phase === "replay" && run.log.length > 0}
          onReveal={() => setRevealed(true)}
        />
        {showMaskinrum && (
          <Maskinrummet
            tools={arch.tools}
            log={run.log}
            clock={clock}
            question={question}
            provider={arch.provider}
            answer={answer}
            runId={run.runId}
            tab={tab}
            onTab={setTab}
            selectedNodeId={selectedNodeId}
            onSelectNode={onSelectNode}
            onInspectRun={inspectRun}
          />
        )}
      </div>

      <footer>
        Maskinrummet — kredsløbet genereres fra runtime (værktøjsliste, LLM-udbyder) og kan aldrig
        vise forældet arkitektur.
      </footer>
    </div>
  );
}
