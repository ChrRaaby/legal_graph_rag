import { useMemo } from "react";
import type { AgentEvent, ToolInfo } from "../lib/api";
import { captionAt } from "../lib/events";
import { lawShort } from "../lib/subgraph";
import type { RunClock } from "../lib/useRunClock";
import Kredslob from "./Kredslob";
import Graflinse from "./Graflinse";
import Tankestrom from "./Tankestrom";
import Eval from "./Eval";
import Tidslinjen from "./Tidslinjen";

export type TabId = "kredslob" | "graflinse" | "tanker" | "evalx";

interface Props {
  tools: ToolInfo[];
  log: AgentEvent[];
  clock: RunClock;
  question: string;
  provider: string;
  answer: string;
  runId: string | null;
  tab: TabId;
  onTab: (t: TabId) => void;
  selectedNodeId: string | null;
  onSelectNode: (id: string | null) => void;
}

const LABELS: Record<TabId, string> = {
  kredslob: "Kredsløb",
  graflinse: "Graflinse",
  tanker: "Tankestrøm",
  evalx: "Eval",
};

export default function Maskinrummet({
  tools, log, clock, question, provider, answer, runId, tab, onTab, selectedNodeId, onSelectNode,
}: Props) {
  const caption = captionAt(log, clock.t, clock.live);
  const t = clock.t;

  // Tab counters, gated by the scrub clock (or live).
  const { nGraf, nTanker } = useMemo(() => {
    const seen = new Set<string>();
    let cards = 0;
    for (const ev of log) {
      const reveal =
        ev.type === "llm" ? ev.start_s * 1000 :
        ev.type === "tool_call" ? ev.elapsed_s * 1000 : null;
      if ((ev.type === "llm" || ev.type === "tool_call") && (clock.live || t >= (reveal ?? 0) - 1)) cards++;
      if (ev.type === "tool_result" && (clock.live || t >= ev.elapsed_s * 1000 - 1)) {
        for (const r of ev.graph_refs ?? []) seen.add(`${lawShort(r.title)}|${r.num.toUpperCase()}`);
      }
    }
    return { nGraf: seen.size, nTanker: cards };
  }, [log, t, clock.live]);

  const counts: Record<TabId, string> = {
    kredslob: "", graflinse: nGraf ? String(nGraf) : "", tanker: nTanker ? String(nTanker) : "", evalx: "",
  };

  return (
    <section className="panel" aria-label="Maskinrummet">
      <div className="panel-h mr-h">
        <div className="tabs" role="tablist">
          {(["kredslob", "graflinse", "tanker", "evalx"] as TabId[]).map((id) => (
            <button key={id} className="tab" role="tab" aria-selected={tab === id} onClick={() => onTab(id)}>
              {LABELS[id]}
              {counts[id] && <span className="n">{counts[id]}</span>}
            </button>
          ))}
        </div>
        <div className="mr-title">Maskinrummet</div>
      </div>

      <div className="stage">
        <div className={`layer${tab === "kredslob" ? " active" : ""}`} role="tabpanel">
          <Kredslob tools={tools} log={log} t={t} live={clock.live} />
        </div>
        <div className={`layer${tab === "graflinse" ? " active" : ""}`} role="tabpanel">
          <Graflinse log={log} answer={answer} t={t} live={clock.live} selectedId={selectedNodeId} onSelect={onSelectNode} />
        </div>
        <div className={`layer${tab === "tanker" ? " active" : ""}`} role="tabpanel">
          <Tankestrom log={log} t={t} live={clock.live} question={question} provider={provider} runId={runId} />
        </div>
        <div className={`layer${tab === "evalx" ? " active" : ""}`} role="tabpanel">
          {tab === "evalx" && <Eval />}
        </div>
      </div>

      <div className="caption">{caption}</div>

      <Tidslinjen
        log={log} t={t} end={clock.end} live={clock.live} playing={clock.playing}
        onPlay={clock.play} onScrub={clock.scrub}
      />
    </section>
  );
}
