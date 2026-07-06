import { useState } from "react";
import type { AgentEvent, ToolInfo } from "../lib/api";
import { captionAt } from "../lib/events";
import type { RunClock } from "../lib/useRunClock";
import Kredslob from "./Kredslob";
import Tidslinjen from "./Tidslinjen";

interface Props {
  tools: ToolInfo[];
  log: AgentEvent[];
  clock: RunClock;
}

type TabId = "kredslob" | "graflinse" | "tanker" | "evalx";

const PLACEHOLDERS: Record<Exclude<TabId, "kredslob">, { title: string; phase: string; body: string }> = {
  graflinse: {
    title: "Graflinsen",
    phase: "E2",
    body: "Den hentede delgraf — Lov → Kapitel → § → stk. — med krydshenvisninger og de noder, der endte i svaret. Kommer i næste fase.",
  },
  tanker: {
    title: "Tankestrømmen",
    phase: "E2",
    body: "Modellens ræsonnement som en levende monolog, med værktøjskald og fuldt input/output. Kommer i næste fase.",
  },
  evalx: {
    title: "Eval",
    phase: "E3",
    body: "Golden-set-instrumentbræt: beståelse pr. dimension og kørsler pr. model, sæt-version og app-commit. Kommer senere.",
  },
};

export default function Maskinrummet({ tools, log, clock }: Props) {
  const [tab, setTab] = useState<TabId>("kredslob");
  const caption = captionAt(log, clock.t, clock.live);

  return (
    <section className="panel" aria-label="Maskinrummet">
      <div className="panel-h mr-h">
        <div className="tabs" role="tablist">
          {(["kredslob", "graflinse", "tanker", "evalx"] as TabId[]).map((id) => {
            const labels: Record<TabId, string> = {
              kredslob: "Kredsløb",
              graflinse: "Graflinse",
              tanker: "Tankestrøm",
              evalx: "Eval",
            };
            return (
              <button
                key={id}
                className="tab"
                role="tab"
                aria-selected={tab === id}
                onClick={() => setTab(id)}
              >
                {labels[id]}
                {id !== "kredslob" && (
                  <span className="n">{PLACEHOLDERS[id as Exclude<TabId, "kredslob">].phase}</span>
                )}
              </button>
            );
          })}
        </div>
        <div className="mr-title">Maskinrummet</div>
      </div>

      <div className="stage">
        <div className={`layer${tab === "kredslob" ? " active" : ""}`} role="tabpanel">
          <Kredslob tools={tools} log={log} t={clock.t} live={clock.live} />
        </div>
        {(["graflinse", "tanker", "evalx"] as Exclude<TabId, "kredslob">[]).map((id) => (
          <div key={id} className={`layer${tab === id ? " active" : ""}`} role="tabpanel">
            <div className="placeholder">
              <span className="tag">{PLACEHOLDERS[id].phase}</span>
              <div className="big">{PLACEHOLDERS[id].title}</div>
              <div>{PLACEHOLDERS[id].body}</div>
            </div>
          </div>
        ))}
      </div>

      <div className="caption">{caption}</div>

      <Tidslinjen
        log={log}
        t={clock.t}
        end={clock.end}
        live={clock.live}
        playing={clock.playing}
        onPlay={clock.play}
        onScrub={clock.scrub}
      />
    </section>
  );
}
