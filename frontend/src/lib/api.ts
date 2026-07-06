// Backend API: runtime-truth architecture + the SSE run stream. /api/ask is a
// POST (it carries the conversation), so we read the event stream with fetch +
// a ReadableStream reader rather than EventSource (which is GET-only).

import type { AgentEvent, GraphRef } from "./events";
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
export interface Architecture {
  app_mode: "user" | "dev";
  provider: string;
  graph_stats: { legislation: number; sections: number; error?: string };
  tools: ToolInfo[];
}
export interface ChatMessage {
  role: "user" | "assistant";
  content: string;
}

export async function fetchArchitecture(): Promise<Architecture> {
  const res = await fetch("/api/architecture");
  if (!res.ok) throw new Error(`architecture ${res.status}`);
  return (await res.json()) as Architecture;
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
