// Backend API: runtime-truth architecture + the SSE run stream. /api/ask is a
// POST (it carries the conversation), so we read the event stream with fetch +
// a ReadableStream reader rather than EventSource (which is GET-only).

import type { AgentEvent, GraphRef } from "./events";
import { setPricing } from "./events";
export type { AgentEvent } from "./events";

export interface ToolInfo {
  name: string;
  description: string;
}

// ── Graflinsen subgraph ──────────────────────────────────────────────────────
export interface SubLaw {
  id: string;
  short: string;
  title: string;
  uri: string | null;
  is_current: boolean;
}
export interface SubSection {
  id: string;
  key: string;
  law_id: string;
  section_number: string;
  title: string | null;
  retrieved: boolean;
  used: boolean;
}
export interface SubCite {
  from: string;
  to: string;
  via: string | null;
}
export interface Subgraph {
  laws: SubLaw[];
  sections: SubSection[];
  paragraphs: { id: string; number: string; section_id: string }[];
  cites: SubCite[];
}

export interface NodeDetail {
  found: boolean;
  label?: string;
  lov?: string;
  short?: string;
  section_number?: string;
  section_title?: string | null;
  is_current?: boolean;
  uri?: string | null;
  paragraphs?: { number: string; text: string }[];
}

export interface TraceSummary {
  run_id: string;
  ts: string;
  question: string;
  provider: string;
  latency_s: number;
}

// ── E3: eval lens + tool health ──────────────────────────────────────────────
export interface EvalDimRow {
  value: string;
  pass: number;
  total: number;
}
export interface EvalRun {
  name: string;
  model: string;
  set_version: string;
  git_sha: string;
  ts: string;
  repeat: number;
  n_items: number;
  n_records: number;
  mean_pass: number;
  pass_pct: number;
  // E4 added pillar + tags; tags is list-valued so its rows count item-tag
  // pairs, not records.
  dims: {
    category: EvalDimRow[];
    difficulty: EvalDimRow[];
    behavior: EvalDimRow[];
    pillar?: EvalDimRow[];
    tags?: EvalDimRow[];
  };
  gated?: number; // records answered by the F1 scope gate
  usage?: Usage | null; // null for files written before usage was recorded
  tool_calls?: number;
}
export interface EvalItem {
  id: string;
  category: string;
  difficulty: string;
  expected_behavior: string;
  question: string;
  runs: number;
  passes: number;
  answer: string;
  gate_flag?: string | null; // "pii" | "illegal" | "non_tax" when gate-answered
  usage?: Usage | null;
  latency_s?: number;
  tool_sequence?: string[];
  run_id?: string | null;  // smoke records only — CLI runs store no event log
  source?: string | null;  // "smoke" for UI-triggered runs
  scores: {
    must_contain: boolean;
    must_not_contain: boolean;
    behavior: boolean;
    citation: boolean;
    detected_behavior: string;
  };
}

// ── E4: golden-set browser + smoke runner ────────────────────────────────────
export interface GoldenItem {
  id: string;
  category: string;
  difficulty: string;
  pillar: string;
  tags: string[];
  question: string;
  expected_behavior: string;
  expected_answer: string;
  must_contain: (string | string[])[];
  must_not_contain: (string | string[])[];
  expected_legislation: { lov?: string; paragraf?: string | string[] }[];
  expected_reasoning_steps?: string[];
  temporal_constraint?: string | null;
  notes?: string;
}
export interface GoldenFacet {
  value: string;
  count: number;
}
export interface GoldenSet {
  metadata: { name?: string; version?: string; description?: string };
  total: number;
  shown: number;
  facets: Record<string, GoldenFacet[]>;
  items: GoldenItem[];
}
export interface EvalRunVerdict {
  id: string;
  /** mr_runs id — its full event log makes the smoke replayable in the lenses. */
  run_id: string;
  latency_s: number;
  answer: string;
  gate_flag: string | null;
  usage?: Usage;
  tool_sequence?: string[];
  scores: {
    overall_pass: boolean;
    must_contain_pass: boolean;
    must_not_contain_pass: boolean;
    behavior_match: boolean;
    citation_pass: boolean;
    detected_behavior: string;
    must_contain_details?: Record<string, boolean>;
    must_not_contain_details?: Record<string, boolean>;
    tool_call_count?: number;
  };
}
export interface ToolHealthRow {
  tool: string;
  calls: number;
  empty_rate: number;
  mean_duration_s: number | null;
}
export interface Architecture {
  app_mode: "user" | "dev";
  provider: string;
  /** Concrete model behind `provider` (ollama's provider string omits it). */
  model?: string;
  /** Selectable substrates, from the allowlist the server actually enforces. */
  providers?: string[];
  graph_stats: { legislation: number; sections: number; error?: string };
  tools: ToolInfo[];
  // Server-supplied price table (app.py is the single source).
  pricing?: { usd_per_mtok: import("./events").PriceRow[]; usd_to_dkk: number };
}
export interface Usage {
  input_tokens: number;
  output_tokens: number;
  llm_calls: number;
  cost_dkk: number | null;
  coverage?: string;
}
export interface ChatMessage {
  role: "user" | "assistant";
  content: string;
}

/** G3 system map: the whole solution, including the GCP substrate. Each node
 *  says whether the running process observed it or merely declares it. */
export interface SystemNode {
  id: string;
  layer: "klient" | "tjeneste" | "model" | "data" | "platform";
  label: string;
  detail: string;
  observed: boolean;
  /** Only present for nodes whose connection the server actually probes. */
  healthy?: boolean;
}
export interface SystemMap {
  generated_at: string;
  nodes: SystemNode[];
  edges: [string, string][];
}

export async function fetchSystemMap(): Promise<SystemMap> {
  const res = await fetch("/api/system");
  if (!res.ok) throw new Error(`system ${res.status}`);
  return (await res.json()) as SystemMap;
}

export async function fetchArchitecture(): Promise<Architecture> {
  const res = await fetch("/api/architecture");
  if (!res.ok) throw new Error(`architecture ${res.status}`);
  const arch = (await res.json()) as Architecture;
  // Install the server's price table before anything renders a cost.
  setPricing(arch.pricing?.usd_per_mtok, arch.pricing?.usd_to_dkk);
  return arch;
}

export async function setProvider(provider: string): Promise<{ provider: string; model?: string }> {
  const res = await fetch("/api/provider", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ provider }),
  });
  const data = await res.json().catch(() => ({}));
  // The server refuses a model outside its allowlist rather than silently
  // running a different one, so a 400 here must propagate — the caller reverts
  // the label instead of leaving the UI claiming an unused substrate.
  if (!res.ok) throw new Error(data?.error || `provider ${res.status}`);
  return data;
}

export async function setAppMode(appMode: "dev" | "user"): Promise<string> {
  const res = await fetch("/api/app_mode", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ app_mode: appMode }),
  });
  if (!res.ok) throw new Error(`app_mode ${res.status}`);
  const data = await res.json();
  return data.app_mode;
}

export async function fetchSubgraph(refs: GraphRef[], answer: string): Promise<Subgraph> {
  const res = await fetch("/api/graph/subgraph", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ refs, answer }),
  });
  if (!res.ok) throw new Error(`subgraph ${res.status}`);
  return (await res.json()) as Subgraph;
}

export async function fetchNodeDetail(elementId: string): Promise<NodeDetail> {
  const res = await fetch(`/api/graph/node/${encodeURIComponent(elementId)}`);
  if (!res.ok) throw new Error(`node ${res.status}`);
  return (await res.json()) as NodeDetail;
}

export async function fetchTraces(): Promise<TraceSummary[]> {
  const res = await fetch("/api/traces");
  if (!res.ok) throw new Error(`traces ${res.status}`);
  return (await res.json()) as TraceSummary[];
}

export interface SavedRun {
  run_id: string;
  question: string;
  answer: string;
  latency_s: number;
  events: AgentEvent[];
  citations: import("./events").Citation[];
}
export async function fetchTrace(runId: string): Promise<SavedRun> {
  const res = await fetch(`/api/traces/${runId}`);
  if (!res.ok) throw new Error(`trace ${res.status}`);
  return (await res.json()) as SavedRun;
}

export async function postFeedback(runId: string | null, verdict: "up" | "down", comment = ""): Promise<void> {
  await fetch("/api/feedback", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ run_id: runId, verdict, comment }),
  });
}

export async function fetchEvalRuns(): Promise<EvalRun[]> {
  const res = await fetch("/api/eval/runs");
  if (!res.ok) throw new Error(`eval/runs ${res.status}`);
  return (await res.json()) as EvalRun[];
}

export async function fetchEvalItems(name: string): Promise<EvalItem[]> {
  const res = await fetch(`/api/eval/runs/${encodeURIComponent(name)}`);
  if (!res.ok) throw new Error(`eval items ${res.status}`);
  return ((await res.json()).items ?? []) as EvalItem[];
}

export async function fetchToolHealth(): Promise<{ tools: ToolHealthRow[]; n_runs: number }> {
  const res = await fetch("/api/tools/health");
  if (!res.ok) throw new Error(`tools/health ${res.status}`);
  return (await res.json()) as { tools: ToolHealthRow[]; n_runs: number };
}

export async function postAnalyze(runId: string, question: string, context: string): Promise<string> {
  const res = await fetch(`/api/run/${runId || "adhoc"}/analyze`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ question, context }),
  });
  const data = await res.json();
  if (data.error) throw new Error(data.error);
  return data.answer as string;
}

export interface ScopeFixture {
  name: string;
  classifier_model: string;
  git_sha: string;
  set_version: string;
  ts: string;
  n: number;
  passed: number;
  errors: number;
  false_positives: number;
  in_scope: number;
  by_flag: EvalDimRow[];
}

export async function fetchScopeFixtures(): Promise<ScopeFixture[]> {
  const res = await fetch("/api/eval/fixtures");
  if (!res.ok) throw new Error(`eval/fixtures ${res.status}`);
  return (await res.json()) as ScopeFixture[];
}

export async function fetchGolden(params: { q?: string; dim?: string; value?: string } = {}): Promise<GoldenSet> {
  const qs = new URLSearchParams();
  if (params.q) qs.set("q", params.q);
  if (params.dim && params.value) {
    qs.set("dim", params.dim);
    qs.set("value", params.value);
  }
  const res = await fetch(`/api/eval/golden${qs.toString() ? "?" + qs : ""}`);
  if (!res.ok) throw new Error(`eval/golden ${res.status}`);
  return (await res.json()) as GoldenSet;
}

/** Smoke-tier eval run. Streams the same events as /api/ask (so Kredsløbet
 *  lights up per item) plus eval_item_start / eval_item / eval_done. */
export async function streamEvalRun(
  itemIds: string[],
  onEvent: (ev: AgentEvent | Record<string, unknown>) => void,
  signal?: AbortSignal,
): Promise<void> {
  const res = await fetch("/api/eval/run", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ item_ids: itemIds }),
    signal,
  });
  if (!res.ok) {
    const msg = await res.json().catch(() => ({}));
    throw new Error((msg as { error?: string }).error ?? `eval/run ${res.status}`);
  }
  if (!res.body) throw new Error("eval/run: no body");
  await pumpSse(res.body.getReader(), onEvent);
}

async function pumpSse(
  reader: ReadableStreamDefaultReader<Uint8Array>,
  onEvent: (ev: never) => void,
): Promise<void> {
  const decoder = new TextDecoder();
  let buffer = "";
  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    let sep: number;
    while ((sep = buffer.indexOf("\n\n")) !== -1) {
      const chunk = buffer.slice(0, sep);
      buffer = buffer.slice(sep + 2);
      for (const line of chunk.split("\n")) {
        if (!line.startsWith("data:")) continue;
        const json = line.slice(5).trim();
        if (!json) continue;
        try {
          onEvent(JSON.parse(json) as never);
        } catch {
          // ignore a malformed frame rather than kill the stream
        }
      }
    }
  }
}

/** POST a conversation and invoke `onEvent` for each SSE event as it arrives. */
export async function streamAsk(
  messages: ChatMessage[],
  onEvent: (ev: AgentEvent) => void,
  signal?: AbortSignal,
): Promise<void> {
  const res = await fetch("/api/ask", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ messages }),
    signal,
  });
  if (!res.ok || !res.body) throw new Error(`ask ${res.status}`);

  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    // SSE messages are separated by a blank line.
    let sep: number;
    while ((sep = buffer.indexOf("\n\n")) !== -1) {
      const chunk = buffer.slice(0, sep);
      buffer = buffer.slice(sep + 2);
      for (const line of chunk.split("\n")) {
        if (!line.startsWith("data:")) continue;
        const json = line.slice(5).trim();
        if (!json) continue;
        try {
          onEvent(JSON.parse(json) as AgentEvent);
        } catch {
          // ignore a malformed frame rather than kill the stream
        }
      }
    }
  }
}
