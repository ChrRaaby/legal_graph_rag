// Event log types + derivations. Every visual layer is a pure function of
// (event_log, t) — this module owns the log shape and the time-derived views
// (timeline spans, run end, caption steps) the components read.

export interface GraphRef {
  uri: string;
  title: string;
  num: string;
}
export interface Citation {
  label: string;
  lov: string;
  section_number: string;
  verified: boolean;
  uri: string | null;
  element_id: string | null;
}

export type AgentEvent =
  | { type: "run_start"; run_id?: string; provider: string; question: string; ts: string }
  | {
      type: "llm";
      elapsed_s: number;
      node?: string;
      start_s: number;
      duration_s: number;
      input_tokens: number;
      output_tokens: number;
      thinking: string;
      is_final: boolean;
    }
  | { type: "tool_call"; elapsed_s: number; node?: string; tool_name: string; args: unknown }
  | {
      type: "tool_result";
      elapsed_s: number;
      node?: string;
      tool_name: string;
      duration_s: number | null;
      content_preview: unknown;
      content_full?: string;
      graph_refs?: GraphRef[];
    }
  // F1 scope gate: the request was blocked before the agent ran. A gated run
  // has no llm/tool events at all — this is the only event between run_start
  // and the answer.
  | { type: "scope_gate"; elapsed_s: number; node?: string; flag: "pii" | "illegal" | "non_tax"; reason: string; duration_s?: number }
  | { type: "scope_gate_error"; elapsed_s: number; node?: string; reason: string }
  | { type: "citations"; items: Citation[] }
  | { type: "answer"; text: string }
  | { type: "done"; run_id?: string; latency_s: number; input_tokens?: number; output_tokens?: number }
  | { type: "error"; message: string };

// ── Token cost (Feedback-round-1 #4) ─────────────────────────────────────────
// USD per 1M tokens; local Ollama has no marginal cost. Rough public list
// prices — for a "hvad koster det"-signal in the UI, not billing.
const PRICE_USD: { match: string; in: number; out: number }[] = [
  { match: "gemini-2.5-flash", in: 0.3, out: 2.5 },
  { match: "gemini-2.5-pro", in: 1.25, out: 10 },
  { match: "gemini-3", in: 0.5, out: 4 },
  { match: "gpt-4o-mini", in: 0.15, out: 0.6 },
];
const USD_TO_DKK = 6.9;

export function costKr(provider: string, inTok: number, outTok: number): number | null {
  if (!provider || provider.startsWith("ollama")) return null; // local = free
  const row = PRICE_USD.find((p) => provider.includes(p.match)) ?? PRICE_USD[0];
  return ((inTok * row.in + outTok * row.out) / 1e6) * USD_TO_DKK;
}

export function formatKr(kr: number): string {
  if (kr < 0.01) return "<0,01 kr.";
  return `~${kr.toFixed(2).replace(".", ",")} kr.`;
}

export const SCOPE_FLAG_LABELS: Record<string, string> = {
  pii: "Personoplysninger",
  illegal: "Ulovlig anmodning",
  non_tax: "Uden for skatteområdet",
};

const TOOL_LABELS: Record<string, string> = {
  Contextual_Text_Retriever: "Kontekst-søgning",
  Legislation_Finder: "Lov-finder",
  Legislation_Title_Resolver: "Titel-opslag",
  Regulering_Table_Lookup: "Reguleringstabel",
  Skattesats_Opslag: "Skattesats-opslag",
  Citation_Network_Explorer: "Citationsnet",
  Supersedes_Network_Explorer: "Supersessions-net",
  Superseded_By_Network_Explorer: "Ophævet-af-net",
  Read_Only_Cypher: "Cypher (læs)",
  Text2Cypher_Expert: "Tekst→Cypher",
  Semantic_Search: "Semantisk søgning",
  Legislation_By_URI: "Lov via URI",
  Hierarchy_Path_Resolver: "Hierarki-opslag",
  Citation_Counts: "Citationstal",
  Graph_Schema_Navigator: "Skema-navigator",
};

export const toolLabel = (name: string): string =>
  TOOL_LABELS[name] ?? name.replace(/_/g, " ");

export interface Span {
  kind: "llm" | "tool";
  t0: number; // ms from run start
  t1: number;
  label: string;
  final: boolean;
}

/** LLM windows (start_s→+duration) and tool windows (call→result), in ms. */
export function spansFromLog(log: AgentEvent[]): Span[] {
  const spans: Span[] = [];
  const pending: Record<string, AgentEvent[]> = {};
  for (const ev of log) {
    if (ev.type === "llm") {
      const t0 = ev.start_s * 1000;
      spans.push({
        kind: "llm",
        t0,
        t1: t0 + ev.duration_s * 1000,
        label: ev.is_final ? "svar" : "tænker",
        final: ev.is_final,
      });
    } else if (ev.type === "tool_call") {
      (pending[ev.tool_name] ??= []).push(ev);
    } else if (ev.type === "tool_result") {
      const call = pending[ev.tool_name]?.shift();
      const t0 = (call && call.type === "tool_call" ? call.elapsed_s : ev.elapsed_s) * 1000;
      spans.push({
        kind: "tool",
        t0,
        t1: ev.elapsed_s * 1000,
        label: toolLabel(ev.tool_name),
        final: false,
      });
    }
  }
  return spans;
}

/** Latest known moment in the log (ms) — the live clock ceiling before `done`. */
export function liveMaxMs(log: AgentEvent[]): number {
  let m = 0;
  for (const ev of log) {
    if (ev.type === "llm") m = Math.max(m, (ev.start_s + ev.duration_s) * 1000);
    else if (ev.type === "tool_call" || ev.type === "tool_result" || ev.type === "scope_gate")
      m = Math.max(m, ev.elapsed_s * 1000);
  }
  return m;
}

/** The gate verdict for this run, if it was blocked. */
export function scopeGate(log: AgentEvent[]): Extract<AgentEvent, { type: "scope_gate" }> | null {
  for (const ev of log) if (ev.type === "scope_gate") return ev;
  return null;
}

/** Total run length (ms): the authoritative `done` latency, else latest event. */
export function runEndMs(log: AgentEvent[]): number {
  for (const ev of log) if (ev.type === "done") return ev.latency_s * 1000;
  return liveMaxMs(log);
}

export function doneEvent(log: AgentEvent[]): Extract<AgentEvent, { type: "done" }> | null {
  for (const ev of log) if (ev.type === "done") return ev;
  return null;
}

function argPreview(args: unknown): string {
  if (args && typeof args === "object") {
    for (const key of ["q", "emne", "question", "uri", "query"]) {
      const v = (args as Record<string, unknown>)[key];
      if (typeof v === "string" && v) return v.length > 46 ? v.slice(0, 46) + "…" : v;
    }
  }
  const s = JSON.stringify(args ?? "");
  return s.length > 46 ? s.slice(0, 46) + "…" : s;
}

function resultCount(preview: unknown): number | null {
  if (Array.isArray(preview)) return preview.length;
  if (typeof preview === "string") {
    const t = preview.trim();
    if (t.startsWith("[")) {
      // content_preview is a truncated JSON array string — count top-level rows
      // by opening braces, good enough for a caption.
      const m = t.match(/\{/g);
      return m ? m.length : null;
    }
    if (t === "" || t === "[]") return 0;
  }
  return null;
}

export interface CaptionStep {
  t: number; // ms
  text: string;
}

/** Timed Danish narration of the run, one step per meaningful event. */
export function captionSteps(log: AgentEvent[]): CaptionStep[] {
  const steps: CaptionStep[] = [];
  let seenFirstLlm = false;
  for (const ev of log) {
    if (ev.type === "run_start") {
      steps.push({ t: 0, text: "LLM tænker — planlægger fremgangsmåden …" });
    } else if (ev.type === "llm") {
      if (ev.is_final) {
        steps.push({ t: ev.start_s * 1000, text: "Formulerer svar med de fundne kilder …" });
      } else if (!seenFirstLlm) {
        seenFirstLlm = true;
      }
    } else if (ev.type === "tool_call") {
      steps.push({
        t: ev.elapsed_s * 1000,
        text: `Kalder ${toolLabel(ev.tool_name)}: «${argPreview(ev.args)}»`,
      });
    } else if (ev.type === "tool_result") {
      const n = resultCount(ev.content_preview);
      const dur = ev.duration_s != null ? ` · ${ev.duration_s.toFixed(1).replace(".", ",")} s` : "";
      steps.push({
        t: ev.elapsed_s * 1000,
        text: n != null ? `${n} resultat${n === 1 ? "" : "er"} hentet fra grafen${dur}` : `Resultat modtaget${dur}`,
      });
    } else if (ev.type === "scope_gate") {
      steps.push({
        t: ev.elapsed_s * 1000,
        text: `Skjoldet blokerede spørgsmålet — ${SCOPE_FLAG_LABELS[ev.flag] ?? ev.flag}`,
      });
    } else if (ev.type === "done") {
      steps.push({ t: ev.latency_s * 1000, text: `Færdig · ${ev.latency_s.toFixed(1).replace(".", ",")} s` });
    }
  }
  steps.sort((a, b) => a.t - b.t);
  return steps;
}

export interface ContextBlock {
  name: string;
  text: string;
}

/** Reconstruct the context an LLM call saw, deterministically from the log:
 *  the question plus every tool output that preceded this call. An approximation
 *  of the true prompt (system prompt aside) — enough to search "is § X / this
 *  amount in what the model was given?". */
export function reconstructContext(
  log: AgentEvent[],
  beforeIndex: number,
  question: string,
): ContextBlock[] {
  const blocks: ContextBlock[] = [];
  if (question) blocks.push({ name: "Brugerens spørgsmål", text: question });
  for (let i = 0; i < beforeIndex && i < log.length; i++) {
    const ev = log[i];
    if (ev.type === "tool_result" && ev.content_full) {
      blocks.push({ name: `${toolLabel(ev.tool_name)} · output`, text: ev.content_full });
    }
  }
  return blocks;
}

export function captionAt(log: AgentEvent[], tMs: number, live: boolean): string {
  const steps = captionSteps(log);
  if (steps.length === 0)
    return "Systemkort — stil et spørgsmål, og kortet vågner.";
  let text = steps[0].text;
  for (const s of steps) if (tMs >= s.t) text = s.text;
  if (live && tMs > liveMaxMs(log) + 200) return "LLM tænker …";
  return text;
}
