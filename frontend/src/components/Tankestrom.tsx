import { useMemo, useState } from "react";
import type { AgentEvent } from "../lib/api";
import { postAnalyze } from "../lib/api";
import { toolLabel, costKr, formatKr, reconstructContext, type ContextBlock } from "../lib/events";

interface Props {
  log: AgentEvent[];
  t: number;
  live: boolean;
  question: string;
  provider: string;
  runId: string | null;
}

interface LlmCard {
  kind: "llm";
  reveal: number;
  logIndex: number;
  thinking: string;
  inTok: number;
  outTok: number;
  dur: number;
  final: boolean;
}
interface ToolCard {
  kind: "tool";
  reveal: number;
  name: string;
  args: unknown;
  preview: string;
  full: string;
  dur: number | null;
  count: number | null;
}
type Card = LlmCard | ToolCard;

function previewStr(v: unknown): string {
  if (typeof v === "string") return v;
  return JSON.stringify(v ?? "", null, 1);
}
function rowCount(v: unknown): number | null {
  if (typeof v === "string" && v.trim().startsWith("[")) {
    const m = v.match(/\{/g);
    return m ? m.length : null;
  }
  return null;
}

function buildCards(log: AgentEvent[]): Card[] {
  const cards: Card[] = [];
  const pendingCall: Record<string, AgentEvent[]> = {};
  let llmCount = 0;
  log.forEach((ev, i) => {
    if (ev.type === "llm") {
      llmCount++;
      cards.push({
        kind: "llm", reveal: ev.start_s * 1000, logIndex: i, thinking: ev.thinking,
        inTok: ev.input_tokens, outTok: ev.output_tokens, dur: ev.duration_s, final: ev.is_final,
      });
    } else if (ev.type === "tool_call") {
      (pendingCall[ev.tool_name] ??= []).push(ev);
    } else if (ev.type === "tool_result") {
      const call = pendingCall[ev.tool_name]?.shift();
      const args = call && call.type === "tool_call" ? call.args : {};
      const reveal = (call && call.type === "tool_call" ? call.elapsed_s : ev.elapsed_s) * 1000;
      const full = ev.content_full ?? previewStr(ev.content_preview);
      cards.push({
        kind: "tool", reveal, name: ev.tool_name, args,
        preview: previewStr(ev.content_preview), full, dur: ev.duration_s,
        count: rowCount(ev.content_full ?? ev.content_preview),
      });
    }
  });
  llmCount; // (kept for potential numbering; label uses running index below)
  return cards;
}

function ContextTools({ blocks, runId }: { blocks: ContextBlock[]; runId: string | null }) {
  const [q, setQ] = useState("");
  const [verdict, setVerdict] = useState<string | null>(null);
  const [aiBusy, setAiBusy] = useState(false);

  const joined = useMemo(() => blocks.map((b) => `── ${b.name} ──\n${b.text}`).join("\n\n"), [blocks]);

  const search = () => {
    const term = q.trim();
    if (!term) return;
    const rx = new RegExp(term.replace(/[.*+?^${}()|[\]\\]/g, "\\$&"), "i");
    for (const b of blocks) {
      const m = rx.exec(b.text);
      if (m) {
        const i = m.index;
        const snip = b.text.slice(Math.max(0, i - 45), i + m[0].length + 55);
        setVerdict(`✓ Fundet i «${b.name}»: …${snip}…`);
        return;
      }
    }
    setVerdict(`✗ «${term}» findes IKKE i konteksten (${blocks.length} blokke søgt deterministisk).`);
  };

  const ask = async () => {
    const term = q.trim();
    if (!term) return;
    setAiBusy(true);
    setVerdict("Analyserer …");
    try {
      const ans = await postAnalyze(runId ?? "adhoc", term, joined);
      setVerdict(`🅰 ${ans}`);
    } catch (e) {
      setVerdict(`Analysefejl: ${String(e)}`);
    } finally {
      setAiBusy(false);
    }
  };

  return (
    <>
      <details>
        <summary>Vis kontekst ({blocks.length} blokke · {joined.length.toLocaleString("da-DK")} tegn)</summary>
        <pre>{joined.length > 6000 ? joined.slice(0, 6000) + "\n…[afkortet]" : joined}</pre>
      </details>
      <div className="ctx-ask">
        <input
          value={q}
          onChange={(e) => setQ(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && search()}
          placeholder="Søg i konteksten: fx «§ 8 a» eller «67.500» — Enter"
          aria-label="Søg i kontekst"
        />
        <button className="ctx-ai" onClick={ask} disabled={aiBusy || !q.trim()}>Spørg AI</button>
      </div>
      {verdict && <div className="ctx-verdict show">{verdict}</div>}
      <div className="ctx-hint">Deterministisk søgning er gratis og øjeblikkelig; «Spørg AI» sender konteksten til en billig model for semantiske spørgsmål.</div>
    </>
  );
}

export default function Tankestrom({ log, t, live, question, provider, runId }: Props) {
  const cards = useMemo(() => buildCards(log), [log]);
  const visible = (reveal: number) => t >= reveal - 1 || live;

  let llmN = 0;
  const shown = cards.filter((c) => visible(c.reveal));

  return (
    <div className="thoughts">
      {shown.length === 0 && (
        <div className="placeholder"><div>Modellens ræsonnement og værktøjskald vises her, mens den arbejder.</div></div>
      )}
      {cards.map((c, idx) => {
        if (!visible(c.reveal)) return null;
        if (c.kind === "llm") {
          llmN++;
          const kr = costKr(provider, c.inTok, c.outTok);
          const blocks = reconstructContext(log, c.logIndex, question);
          return (
            <div className="th" key={idx}>
              <div className="who">🤖 LLM · {c.final ? "svar" : `tanke ${llmN}`}</div>
              <span>{c.thinking || <em style={{ color: "var(--ink-3)" }}>(ingen synlig ræsonnering)</em>}</span>
              <div className="meta">
                <span>ind {c.inTok.toLocaleString("da-DK")} tok</span>
                <span>ud {c.outTok.toLocaleString("da-DK")} tok</span>
                <span>{kr == null ? "lokal" : formatKr(kr)}</span>
                <span>{c.dur.toFixed(1).replace(".", ",")} s</span>
              </div>
              {blocks.length > 0 && <ContextTools blocks={blocks} runId={runId} />}
            </div>
          );
        }
        return (
          <div className="th toolio" key={idx}>
            <div className="who">🔧 {toolLabel(c.name)} · kald</div>
            <code>{previewStr(c.args)}</code>
            <div className="meta">
              {c.count != null && <span>{c.count} rækker</span>}
              <span>{c.full.length.toLocaleString("da-DK")} tegn output</span>
              {c.dur != null && <span>{c.dur.toFixed(1).replace(".", ",")} s</span>}
            </div>
            <details>
              <summary>Vis fuldt output</summary>
              <pre>{c.full.length > 6000 ? c.full.slice(0, 6000) + "\n…[afkortet]" : c.full}</pre>
            </details>
          </div>
        );
      })}
    </div>
  );
}
