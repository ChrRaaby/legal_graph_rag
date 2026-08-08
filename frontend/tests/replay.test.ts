// E1 replay/scrub verification: exercise the pure (event_log, t) functions
// against a REAL captured gs-025 run. Because scrubbing is nothing more than
// evaluating these functions at different t, passing this proves the timeline
// replay behaves correctly — no browser required.
//
// Run:  node_modules/.bin/esbuild tests/replay.test.ts --bundle --format=esm \
//         --platform=node --outfile=/tmp/replay.mjs && node /tmp/replay.mjs
import {
  spansFromLog, runEndMs, captionAt, liveMaxMs,
  costKr, reconstructContext, scopeGate,
} from "../src/lib/events";
import type { AgentEvent } from "../src/lib/events";
import { buildCircuit, circuitOn } from "../src/lib/circuit";
import { refsFromLog } from "../src/lib/subgraph";
import type { ToolInfo } from "../src/lib/api";
import raw from "./gs25_events.json";

const log = raw as unknown as AgentEvent[];

let failures = 0;
function check(name: string, cond: boolean, detail = "") {
  if (cond) {
    console.log(`  ok   ${name}`);
  } else {
    failures++;
    console.log(`  FAIL ${name}${detail ? "  — " + detail : ""}`);
  }
}

// The real runtime tool order (from GET /api/architecture); first 7 get their
// own circuit nodes, so Contextual_Text_Retriever (#4) and
// Regulering_Table_Lookup (#5) are individually lit for gs-025.
const TOOLS: ToolInfo[] = [
  "Graph_Schema_Navigator", "Legislation_Title_Resolver", "Legislation_Finder",
  "Contextual_Text_Retriever", "Regulering_Table_Lookup", "Skattesats_Opslag",
  "Citation_Network_Explorer", "Supersedes_Network_Explorer", "Superseded_By_Network_Explorer",
  "Read_Only_Cypher", "Text2Cypher_Expert", "Semantic_Search", "Legislation_By_URI",
  "Hierarchy_Path_Resolver", "Citation_Counts",
].map((name) => ({ name, description: "" }));

console.log("replay.test — real gs-025 log, " + log.length + " events");

// ── spans ────────────────────────────────────────────────────────────────────
const nLlm = log.filter((e) => e.type === "llm").length;
const nTool = log.filter((e) => e.type === "tool_result").length;
const spans = spansFromLog(log);
const llmSpans = spans.filter((s) => s.kind === "llm");
const toolSpans = spans.filter((s) => s.kind === "tool");
check("one llm span per llm event", llmSpans.length === nLlm, `${llmSpans.length} vs ${nLlm}`);
check("one tool span per tool_result", toolSpans.length === nTool, `${toolSpans.length} vs ${nTool}`);
check("all spans ordered t0<=t1", spans.every((s) => s.t1 >= s.t0));
check("exactly one final llm span", llmSpans.filter((s) => s.final).length === 1);

// ── run length ───────────────────────────────────────────────────────────────
const doneMs = ((log.find((e) => e.type === "done") as Extract<AgentEvent, { type: "done" }>).latency_s) * 1000;
const end = runEndMs(log);
check("runEnd == done latency", Math.round(end) === Math.round(doneMs), `${end} vs ${doneMs}`);
check("liveMax <= runEnd", liveMaxMs(log) <= end + 1);

// ── circuit layout (runtime truth) ───────────────────────────────────────────
const circuit = buildCircuit(TOOLS);
const ids = new Set(circuit.nodes.map((n) => n.id));
check("has user/agent/db/emb", ["user", "agent", "db", "emb"].every((i) => ids.has(i)));
check("7 tools shown + __more__ collapse", ids.has("__more__") && circuit.shownTools.size === 7);
check("named tool node exists for the retriever", ids.has("Contextual_Text_Retriever"));

// ── scrub: circuit state at representative moments ───────────────────────────
// A thinking moment (inside the first llm window) lights the agent.
const firstLlm = llmSpans[0];
const tThink = (firstLlm.t0 + firstLlm.t1) / 2;
const onThink = circuitOn(log, tThink, circuit, null);
check("thinking moment lights agent", onThink.has("agent"));
check("thinking moment lights no tool node", ![...onThink].some((i) => circuit.shownTools.has(i)));

// A retrieval moment (inside a Contextual_Text_Retriever window) lights that
// tool node + the graph path.
const retr = toolSpans.find((s) => s.label === "Kontekst-søgning")!;
const tRetr = (retr.t0 + retr.t1) / 2;
const onRetr = circuitOn(log, tRetr, circuit, null);
check("retrieval lights the tool node", onRetr.has("Contextual_Text_Retriever"));
check("retrieval lights agent→tool edge", onRetr.has("a-Contextual_Text_Retriever"));
check("retrieval lights graph (db + t-db + emb)", ["db", "t-db", "emb", "emb-db"].every((i) => onRetr.has(i)));

// t=0 and t=end are stable (no throw, sane).
check("t=0 produces a set", circuitOn(log, 0, circuit, null) instanceof Set);
check("t=end produces a set", circuitOn(log, end, circuit, null) instanceof Set);

// ── captions narrate the run ─────────────────────────────────────────────────
const cap0 = captionAt(log, 0, false);
check("caption@0 mentions planning/thinking", /tænker|planl/i.test(cap0), cap0);
const capRetr = captionAt(log, retr.t0 + 1, false);
check("caption@retrieval names the tool", /Kontekst-søgning|resultat/i.test(capRetr), capRetr);
const capEnd = captionAt(log, end, false);
check("caption@end says Færdig", /Færdig/.test(capEnd), capEnd);
// live heuristic: past the last event, agent reads as thinking
const capLive = captionAt(log.slice(0, 4), liveMaxMs(log.slice(0, 4)) + 500, true);
check("live caption past last event = thinking", /tænker/i.test(capLive), capLive);

// ── E2: new event shapes + derivations ──────────────────────────────────────
const toolResults = log.filter((e) => e.type === "tool_result") as Extract<AgentEvent, { type: "tool_result" }>[];
check("tool_results carry graph_refs", toolResults.every((e) => Array.isArray(e.graph_refs)));
check("tool_results carry content_full", toolResults.every((e) => typeof e.content_full === "string" && e.content_full.length > 0));
const refs = refsFromLog(log);
check("refsFromLog extracts section refs", refs.length > 0, `got ${refs.length}`);
check("refs have num + (uri or title)", refs.every((r) => r.num && (r.uri || r.title)));

const citEv = log.find((e) => e.type === "citations") as Extract<AgentEvent, { type: "citations" }> | undefined;
check("citations event present", !!citEv && citEv.items.length > 0);
check("citations have label + verified flag", !!citEv && citEv.items.every((c) => c.label && typeof c.verified === "boolean"));

const runStart = log.find((e) => e.type === "run_start") as Extract<AgentEvent, { type: "run_start" }>;
check("run_start carries run_id", !!runStart.run_id);

// cost: hosted provider yields a positive kr; local is null
check("costKr hosted > 0", (costKr("gemini:gemini-2.5-flash", 10000, 1000) ?? 0) > 0);
check("costKr local is null", costKr("ollama", 10000, 1000) === null);

// context reconstruction: the final llm call sees the preceding tool outputs
const lastLlmIdx = log.map((e, i) => (e.type === "llm" ? i : -1)).filter((i) => i >= 0).at(-1)!;
const ctx = reconstructContext(log, lastLlmIdx, "gs-025 spørgsmål");
check("context has question + >=1 tool block", ctx.length >= 2 && ctx.some((b) => b.name.includes("output")));

// ── F1 scope gate ────────────────────────────────────────────────────────────
// A gated run is the degenerate log: run_start → scope_gate → answer → done,
// with no llm/tool events at all. The layers must stay coherent on it.
const gatedLog = [
  { type: "run_start", run_id: "g1", provider: "gemini:gemini-3.5-flash",
    question: "Fortæl mig en joke", ts: "2026-08-02T10:00:00Z" },
  { type: "scope_gate", elapsed_s: 0.42, node: "scope_gate", flag: "non_tax",
    reason: "Spørgsmålet handler om en joke og har intet med dansk skatteret at gøre.",
    duration_s: 0.42 },
  { type: "answer", text: "Det ligger uden for mit område — jeg svarer kun på spørgsmål om dansk skattelovgivning." },
  { type: "done", run_id: "g1", latency_s: 0.51 },
] as unknown as AgentEvent[];

const gateOn = circuitOn(gatedLog, 500, circuit, null);
check("gated run lights the shield node", gateOn.has("gate"));
check("gated run lights user→gate edge", gateOn.has("u-g"));
check("gated run does NOT light the agent", !gateOn.has("agent"));
check("gated run lights no tool/db nodes",
  !gateOn.has("db") && !gateOn.has("emb") && ![...gateOn].some((id) => id.startsWith("a-")));
check("before the gate fires nothing is lit", circuitOn(gatedLog, 0, circuit, null).size === 0);

check("gated run has no spans", spansFromLog(gatedLog).length === 0);
check("gated run end comes from done", runEndMs(gatedLog) === 510);
check("liveMaxMs counts the gate event", liveMaxMs(gatedLog) === 420);
check("caption names the shield and the flag",
  /Skjoldet/.test(captionAt(gatedLog, 500, false)) &&
  /skatteområdet/i.test(captionAt(gatedLog, 500, false)),
  captionAt(gatedLog, 500, false));

const gateEv = scopeGate(gatedLog);
check("scopeGate() finds the verdict", !!gateEv && gateEv.flag === "non_tax");
check("scopeGate() is null on a normal run", scopeGate(log) === null);
check("the shield exists in the idle circuit too",
  circuit.nodes.some((n) => n.id === "gate" && n.cls === "gate"));

// ── E4: a smoke-run event log replays exactly like a chat run ───────────────
// The runner reuses /api/ask's event shape, so the same pure functions must
// work on it — that is what makes an eval item scrubbable in Kredsløbet.
const evalRunLog = [
  { type: "eval_item_start", index: 1, total: 2, id: "gs-001", question: "Kan jeg få kørselsfradrag?" },
  { type: "run_start", run_id: "e1", provider: "gemini:gemini-3.5-flash",
    question: "Kan jeg få kørselsfradrag?", ts: "2026-08-08T10:00:00Z" },
  { type: "tool_call", elapsed_s: 1.2, tool_name: "Contextual_Text_Retriever", args: { q: "kørselsfradrag" } },
  { type: "tool_result", elapsed_s: 3.4, tool_name: "Contextual_Text_Retriever",
    duration_s: 2.2, content_preview: "[{}]", content_full: "[{}]" },
  { type: "llm", elapsed_s: 4.0, start_s: 3.4, duration_s: 0.6, input_tokens: 900,
    output_tokens: 120, thinking: "", is_final: true },
  { type: "answer", text: "Ja, kørselsfradrag efter LL § 9 C." },
  { type: "done", run_id: "e1", latency_s: 4.2 },
] as unknown as AgentEvent[];

check("eval-run log yields spans like a chat run", spansFromLog(evalRunLog).length === 2);
check("eval-run log has a coherent end time", runEndMs(evalRunLog) === 4200);
check("eval-run log lights the tool node mid-run",
  circuitOn(evalRunLog, 2000, circuit, null).has("Contextual_Text_Retriever"));
check("eval-run log is not treated as gated", scopeGate(evalRunLog) === null);
check("eval-run caption names the tool",
  /Kontekst-søgning/.test(captionAt(evalRunLog, 1500, false)), captionAt(evalRunLog, 1500, false));
check("unknown eval_* event types do not break derivations",
  spansFromLog(evalRunLog).length === spansFromLog(evalRunLog.slice(1)).length);

// A gated eval item: the shield answers, nothing downstream lights.
const gatedEvalLog = [
  { type: "eval_item_start", index: 2, total: 2, id: "gs-051", question: "Fortæl mig en joke" },
  { type: "run_start", run_id: "e2", provider: "gemini:gemini-3.5-flash",
    question: "Fortæl mig en joke", ts: "2026-08-08T10:01:00Z" },
  { type: "scope_gate", elapsed_s: 0.5, flag: "non_tax", reason: "ikke skat", duration_s: 0.5 },
  { type: "answer", text: "Det ligger uden for mit område — jeg svarer kun på spørgsmål om dansk skattelovgivning." },
  { type: "done", run_id: "e2", latency_s: 0.6 },
] as unknown as AgentEvent[];

check("gated eval item is detected as gated", scopeGate(gatedEvalLog)?.flag === "non_tax");
check("gated eval item lights the shield, not the agent",
  circuitOn(gatedEvalLog, 600, circuit, null).has("gate") &&
  !circuitOn(gatedEvalLog, 600, circuit, null).has("agent"));
check("gated eval item produced no spans", spansFromLog(gatedEvalLog).length === 0);

console.log(failures === 0 ? "\nALL PASS" : `\n${failures} FAILURE(S)`);
if (failures > 0) process.exit(1);
