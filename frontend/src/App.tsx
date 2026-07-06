import { useEffect, useState } from "react";
import { fetchArchitecture, type Architecture } from "./lib/api";
import { useAgentRun } from "./lib/useAgentRun";
import { useRunClock } from "./lib/useRunClock";
import Chat from "./components/Chat";
import Maskinrummet from "./components/Maskinrummet";

type Theme = "auto" | "dark" | "light";
const nf = new Intl.NumberFormat("da-DK");

export default function App() {
  const [arch, setArch] = useState<Architecture | null>(null);
  const [archError, setArchError] = useState<string | null>(null);
  const [theme, setTheme] = useState<Theme>("auto");
  const [revealed, setRevealed] = useState(false);

  const run = useAgentRun();
  const clock = useRunClock(run.phase, run.log);

  useEffect(() => {
    fetchArchitecture().then(setArch).catch((e) => setArchError(String(e)));
  }, []);

  useEffect(() => {
    const root = document.documentElement;
    if (theme === "auto") root.removeAttribute("data-theme");
    else root.setAttribute("data-theme", theme);
  }, [theme]);

  const cycleTheme = () =>
    setTheme((t) => (t === "auto" ? "dark" : t === "dark" ? "light" : "auto"));

  if (archError) {
    return (
      <div className="app">
        <header>
          <div className="brand">
            <span className="par">§</span>Skattegraf
          </div>
        </header>
        <div className="err">Kunne ikke indlæse systemet: {archError}</div>
      </div>
    );
  }
  if (!arch) {
    return (
      <div className="app">
        <div className="thinking-line" style={{ marginTop: 40 }}>
          <span className="dot" />
          Indlæser Maskinrummet …
        </div>
      </div>
    );
  }

  const isDev = arch.app_mode === "dev";
  const showMaskinrum = isDev || revealed;
  const themeLabel = theme === "auto" ? "◑ auto" : theme === "dark" ? "☾ mørk" : "☀ lys";

  return (
    <div className="app">
      <header>
        <div className="brand">
          <span className="par">§</span>Skattegraf
          {isDev && <small>MASKINRUMMET</small>}
        </div>
        <div className="badges">
          {isDev && (
            <span className="badge">
              LLM <b>{arch.provider}</b>
            </span>
          )}
          <span className="badge">
            Graf{" "}
            <b>
              {nf.format(arch.graph_stats.legislation)} love · {nf.format(arch.graph_stats.sections)} §§
            </b>
          </span>
          <button className="theme-toggle" onClick={cycleTheme} aria-label="Skift tema">
            {themeLabel}
          </button>
          {isDev && <span className="badge mode">dev-tilstand</span>}
        </div>
      </header>

      <div className={`split${showMaskinrum ? "" : " user"}`}>
        <Chat
          messages={run.messages}
          phase={run.phase}
          error={run.error}
          onSend={run.ask}
          showReveal={!isDev && !revealed && run.phase === "replay" && run.log.length > 0}
          onReveal={() => setRevealed(true)}
        />
        {showMaskinrum && <Maskinrummet tools={arch.tools} log={run.log} clock={clock} />}
      </div>

      <footer>
        Maskinrummet — kredsløbet genereres fra runtime (værktøjsliste, LLM-udbyder) og kan aldrig
        vise forældet arkitektur.
      </footer>
    </div>
  );
}
