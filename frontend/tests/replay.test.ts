// E1 replay/scrub verification: exercise the pure (event_log, t) functions
// against a REAL captured gs-025 run. Because scrubbing is nothing more than
// evaluating these functions at different t, passing this proves the timeline
// replay behaves correctly — no browser required.
//
// Run:  node_modules/.bin/esbuild tests/replay.test.ts --bundle --format=esm \
//         --platform=node --outfile=/tmp/replay.mjs && node /tmp/replay.mjs
import { spansFromLog, runEndMs, captionAt, liveMaxMs } from "../src/lib/events";
import type { AgentEvent } from "../src/lib/events";
import { buildCircuit, circuitOn } from "../src/lib/circuit";
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
const spans = spansFromLog(log);
const llmSpans = spans.filter((s) => s.kind === "llm");
const toolSpans = spans.filter((s) => s.kind === "tool");
check("5 llm spans", llmSpans.length === 5, `got ${llmSpans.length}`);
check("4 tool spans", toolSpans.length === 4, `got ${toolSpans.length}`);
check("all spans ordered t0<=t1", spans.every((s) => s.t1 >= s.t0));
check("one final llm span", llmSpans.filter((s) => s.final).length === 1);

// ── run length ───────────────────────────────────────────────────────────────
const end = runEndMs(log);
check("runEnd == done latency (26131 ms)", Math.round(end) === 26131, `got ${end}`);
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

console.log(failures === 0 ? "\nALL PASS" : `\n${failures} FAILURE(S)`);
if (failures > 0) process.exit(1);
