# Dansk Skattelovgivning — Graph Agent

A Neo4j knowledge graph of Danish tax legislation sourced from [retsinformation.dk](https://www.retsinformation.dk/), with **Maskinrummet** — a FastAPI + React front-end — exposing a LangGraph ReAct agent for natural-language querying of the graph.

The primary objective is **GraphRAG** (Graph Retrieval-Augmented Generation) applied to Danish tax law — making legislation machine-readable and conversationally queryable, with full traceability of sources.

## Laws in the Graph

| Abbreviation | Name | Versions |
|---|---|---|
| PSL | Personskatteloven | 2021/1284 |
| LL | Ligningsloven | 2025/1500, 2023/42, 2021/1735, 2019/806 |
| SEL | Selskabsskatteloven | 2022/1241, 2021/251 |
| KSL | Kildeskatteloven | 2024/460, 2023/1330 |
| ML | Momsloven | 2024/209 |
| ABL | Aktieavancebeskatningsloven | 2021/172 |
| KGL | Kursgevinstloven | 2022/1390 |
| AL | Afskrivningsloven | 2021/242 |
| FBL | Fondsbeskatningsloven | 2021/700 |
| ASKL | Aktiesparekontoloven | 2025/281 |
| BAL | Boafgiftsloven | 2023/11 |
| PBL | Pensionsbeskatningsloven | 2024/1243 |
| — | LOV 482/2024 (PSL §§ 7/7a/8 reform) | 2024/482 |
| — | PSL § 20 reguleringstabel 2025–2026 | — |

## Architecture

```
User → Maskinrummet (React) → server.py (FastAPI, SSE)
           ↓
    Scope gate (classifier: pii / illegal / non_tax)
           ↓
    LangGraph ReAct Agent
           ↓
    12 StructuredTools (Python)
           ↓
    Neo4j Aura (graph DB)  +  Vector Index (intfloat/multilingual-e5-large, 1024 dims)
           ↑
    retsinformation.dk XML → danish_crawler.ipynb → loader.ipynb → vectorize_danish.py → indices.ipynb
```

`app.py` is the single source of the agent runtime and contains no UI.

### Graph Schema

Core hierarchy: `Legislation → Part → Chapter → Section → Paragraph`

Additional nodes: `Schedule`, `ScheduleParagraph`, `ExplanatoryNotes`, `Commentary`, `Citation`, `Text` (embedding carrier).

Key relationships: `HAS_PART`, `HAS_SECTION`, `HAS_PARAGRAPH`, `CITES`, `SUPERSEDES`, `SUPERSEDED_BY`, `LINKED_TO`.

Temporal fields on nodes: `restrict_start_date`, `restrict_end_date` — enable point-in-time queries.

### Agent Tools (12)

| Tool | Strategy | Purpose |
|---|---|---|
| `Legislation_Title_Resolver` | Lexical | Exact law title lookup |
| `Legislation_Finder` | Hybrid (lexical + vector) | Primary law discovery |
| `Contextual_Text_Retriever` | Vector | Evidence passages with full hierarchy context |
| `Citation_Network_Explorer` | Graph | § → § citation edges, across laws |
| `Supersedes_Network_Explorer` | Graph | Outgoing replacement lineage |
| `Superseded_By_Network_Explorer` | Graph | Incoming replacement lineage |
| `Legislation_By_URI` | Graph | Exact URI lookup |
| `Hierarchy_Path_Resolver` | Graph | Reconstruct node context |
| `Section_Exists` | Graph | Does this § exist in this law? |
| `Skattesats_Opslag` | Graph | Rate lookup |
| `Regulering_Table_Lookup` | Graph | PSL § 20 regulated amounts by year |
| `Graph_Schema_Navigator` | Graph | Live schema via apoc.meta.data() |
| `Read_Only_Cypher` | Cypher | Analyst-provided read-only queries |

`Semantic_Search`, `Citation_Counts` and `Text2Cypher_Expert` were pruned (backlog C3):
0 useful calls across 2,301 saved item-runs. Their definitions survive behind
`C3_TOOL_PRUNE=off`.

## Running the App

```bash
.venv/bin/python3 -m uvicorn server:app --port 8000
```

Then open http://127.0.0.1:8000. For frontend development, `npm --prefix frontend run dev`
starts Vite on :5173 proxying `/api` to the server.

Requires `.env`:
```
NEO4J_URI=bolt://...
NEO4J_USER=...
NEO4J_PASSWORD=...
GOOGLE_API_KEY=...    # or OPENAI_API_KEY / OLLAMA_MODEL
```

## Pipeline (build the graph from scratch)

1. `danish_crawler.ipynb` — crawl retsinformation.dk from `danish_tax_legislation.txt`
2. `loader.ipynb` — load JSON into Neo4j
3. `python /tmp/vectorize_danish.py` — embed all `Text` nodes with `intfloat/multilingual-e5-large`
4. `indices.ipynb` — create vector + text indexes

## Observability

Maskinrummet's lenses are all pure functions of `(event_log, t)`, so the same views
serve a live run and a scrubbable replay:
- **Kredsløbet** — the circuit, generated from the real tool list and lit as the run proceeds (including the scope gate, "Skjoldet")
- **Graflinsen** — the retrieved subgraph with a node inspector (full stk text, validity, ELI link)
- **Tankestrømmen** — reasoning and tool cards with per-call token/cost badges and I/O drill-down
- **Tidslinjen** — scrub to replay any moment of the run
- **Eval** — *Testsuite* (browse `eval_golden_set.json`, 69 cases; run a capped smoke) and *Historik* (past runs, pass-% per dimension, item drill-down, scope-fixture baselines, tool health)

Token usage and its estimated cost are shown together wherever either appears.
Run artefacts land in `eval_history/`.
