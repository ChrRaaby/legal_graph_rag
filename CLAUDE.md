# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Current work queue

**`IMPROVEMENT_BACKLOG.md` is the active execution plan** (phases A–D, ranked, with ground rules and the measurement protocol). Read it before starting any improvement work — its ground rules encode measured experimental evidence and override intuition (e.g. never force-inject extra data into retrieval output for the current 26B model).

**`TODO.md` is the consolidated index** — every open item across all phases, one line each, including items found outside the backlog's phase structure. Start there to see what's open; go to the backlog for the evidence and reasoning behind any of it. New work graduates from TODO.md into the backlog once it has a design.

## Project Overview

A Neo4j knowledge graph of Danish tax legislation sourced from [retsinformation.dk](https://www.retsinformation.dk/) (XML format via the ELI URI scheme). The primary objective is GraphRAG (Graph Retrieval-Augmented Generation) for Danish tax law.

**The only UI is the Maskinrummet frontend** (`server.py` FastAPI + `frontend/` React; run `uvicorn server:app`, or `npm --prefix frontend run dev` for the Vite dev server proxying to it). `app.py` is the **single source of the agent runtime** (`build_runtime`, `stream_agent_answer`, tools, guards, scoring) and is now **pure runtime** — importing it executes no UI code and builds nothing at import time. The Streamlit app was **deleted on 2026-08-08** (1,390 lines: sidebar views, chat, eval panel, its own SQLite trace tables), together with the `sys.modules` Streamlit stubs that server.py/eval_run.py used to need. `streamlit`, `altair`, `pandas` and `neo4j_viz` are no longer dependencies.

Laws in the graph: Personskatteloven (PSL, 2 versions), Ligningsloven (LL, 4 versions incl. 2025/1500), Selskabsskatteloven (SEL, 2 versions), Kildeskatteloven (KSL, 2 versions), Momsloven (ML), Aktieavancebeskatningsloven (ABL), Kursgevinstloven (KGL), Afskrivningsloven (AL), Fondsbeskatningsloven (FBL), Aktiesparekontoloven (ASKL), Boafgiftsloven (BAL, 2023/11 — D1), LOV 482/2024, PSL § 20 reguleringstabel 2025–2026.

## Environment Setup

Uses a local `.venv`. Run the app with:

```bash
.venv/bin/python3 -m uvicorn server:app --port 8000
```

Eval artefacts (run outputs, fixture baselines, run logs) live in **`eval_history/`** —
`eval_run.py --output foo.jsonl` resolves a relative name into that folder, while an
absolute path passes through unchanged. See `eval_history/README.md`.

Requires a `.env` file with:
```
NEO4J_URI=bolt://...
NEO4J_USER=...
NEO4J_PASSWORD=...
NEO4J_DATABASE=neo4j       # optional, defaults to "neo4j"
GOOGLE_API_KEY=...         # use this OR OPENAI_API_KEY
OPENAI_API_KEY=...
AGENT_RETRIEVAL_K=10       # optional
AGENT_HISTORY_MESSAGES=20  # optional
DEBUG_TOOL_CALLS=1         # optional, enables tool call debug logging
```

## Pipeline Notebooks (run in order for Danish graph)

1. **`danish_crawler.ipynb`** — Crawls retsinformation.dk from seed URLs in `danish_tax_legislation.txt`, parses XML, and outputs JSON.
2. **`loader.ipynb`** — Loads parsed JSON into Neo4j using PySpark. Builds the graph nodes and relationships.
3. Vectorization — run `vectorize_danish.py` (in-repo; `intfloat/multilingual-e5-large`, 1024 dims, CUDA, `passage: ` prefix, L2-normalized; labels new structural nodes `:Text` — a secondary label the loader does NOT apply — then embeds whatever lacks `text_embedding`). **GPU — ask user.** Note: load new acts via a single-file staging dir (whole-corpus Spark reads break on cross-file schema drift; see backlog D1).
4. **`indices.ipynb`** — Creates Neo4j vector and text indexes (including `text_embeddings_index` used by the app).

Seed file: `danish_tax_legislation.txt` — lists all loaded laws as ELI year/number pairs (e.g. `2021/1284` → `https://www.retsinformation.dk/eli/lta/2021/1284/xml`).

Note: `_uk_archive/` contains the original UK pipeline notebooks (`crawler.ipynb`, `vectorize.ipynb`, `examples.ipynb`) and seed list (`legislation_list.txt`) from when this project targeted legislation.gov.uk. They are kept for reference only and are not part of the active pipeline.

## Architecture

### Graph Schema

The core node hierarchy is: `Legislation → Part → Chapter → Section → Paragraph`, with additional node types:
- `Schedule`, `ScheduleParagraph`, `ScheduleSubparagraph`
- `ExplanatoryNotes`, `ExplanatoryNotesParagraph`
- `Commentary` (annotations linked to provisions)
- `Citation`, `CitationSubRef` (cross-references between acts)
- `Text` (virtual node holding embedded text; not shown in schema visualizations)

Key relationships: `HAS_PART`, `HAS_CHAPTER`, `HAS_SECTION`, `HAS_PARAGRAPH`, `HAS_SCHEDULE`, `HAS_SUBPARAGRAPH`, `HAS_EXPLANATORY_NOTES`, `HAS_COMMENTARY`, `HAS_CITATION`, `HAS_SUBREF`, `CITES`, `LINKED_TO`, `SUPERSEDES`, `SUPERSEDED_BY`.

Temporal fields on nodes: `restrict_start_date`, `restrict_end_date`, `restrict_extent`, `status`. These enable point-in-time queries.

Embedding model: `intfloat/multilingual-e5-large` (1024 dims). Uses `passage: ` prefix for indexing and `query: ` prefix for search queries, per the model's convention.

### `app.py` — the agent runtime (no UI)

`build_runtime()` (memoised via `functools.lru_cache`) initializes everything on first call:
- **`Neo4jAnalysis`** (`neo4j_analysis.py`) — thin wrapper around the Neo4j driver; `run_query` → list of dicts, `run_query_df` → DataFrame, `run_query_viz` → graph object for `neo4j-viz`.
- **LLM** — priority: Ollama (if `OLLAMA_MODEL` set) → Gemini (`gemini-2.5-flash` if `GOOGLE_API_KEY` set) → OpenAI (`gpt-4o-mini` if `OPENAI_API_KEY` set).
- **Embeddings** — `intfloat/multilingual-e5-large` via HuggingFace (1024 dims).
- **`GraphCypherQAChain`** — NL-to-Cypher fallback using a detailed prompt with 14 rules.
- **`Neo4jVector`** — reads from `text_embeddings_index` on `Text` nodes.
- **Agent tools** — 12 LangChain `StructuredTool`s wrapping Cypher queries and hybrid search (C3 pruned `Semantic_Search`, `Citation_Counts`, `Text2Cypher_Expert` — 0 useful calls in 2,301 saved item-runs; definitions remain in app.py behind `C3_TOOL_PRUNE=off`):
  - `Legislation_Title_Resolver` — lexical title matching with ranked scoring.
  - `Legislation_Finder` — hybrid (lexical + vector) legislation discovery.
  - `Contextual_Text_Retriever` — vector search returning full Legislation → Paragraph hierarchy context (C2 law-narrowed direct-§ lookup; C7b byte-identical row dedup).
  - `Citation_Network_Explorer` (C1: Section-level CITES), `Supersedes_Network_Explorer`, `Superseded_By_Network_Explorer` — relationship traversal.
  - `Graph_Schema_Navigator` — returns live schema via `apoc.meta.data()`.
  - `Read_Only_Cypher` — executes analyst-provided Cypher (blocks write operations).
  - `Legislation_By_URI`, `Hierarchy_Path_Resolver`, `Skattesats_Opslag`, `Regulering_Table_Lookup`, `Section_Exists`.

`stream_agent_answer()` streams the LangGraph agent, collects tool trace events, and returns `(answer, tool_events)` — this 2-tuple must be preserved as `eval_run.py` unpacks it.

Also in `app.py`: the F1 scope gate (`classify_request`, `SCOPE_TEMPLATES`), the
scoring single-source (`score_item`, `detect_behavior`, `BEHAVIOR_SIGNALS` — A1),
and token pricing (`cost_dkk`, `token_usage`, `PRICE_USD_PER_MTOK`), which
server.py serves to the frontend so the UI never carries its own price table.

### UI

The Maskinrummet frontend (`frontend/`, served by `server.py`): chat + Kredsløbet,
Graflinsen, Tankestrømmen, Tidslinjen, and the Eval lens (Testsuite / Historik
sub-tabs — golden-set browser, smoke runner, dimension matrices, tool health).
The old Streamlit sidebar-view UI was deleted 2026-08-08.

### System Prompt

Danish-language prompt (`app.py` ~line 920–945). Key rules:
- Quote amounts verbatim from graph; never hardcode specific indexed amounts
- For PSL § 20 regulated amounts: always search the graph for the reguleringstabel
- KSL § 48 E–F (forskerordning): always mention 27% rate and the 7-year period
- FRAVALG: explicitly state "der betales ikke [skattenavn]" when a tax doesn't apply
- FORMUESKAT: afskaffet i 1997 — state this explicitly
- Use `Contextual_Text_Retriever` with content descriptions, not paragraph references

## Key Cypher Patterns

Relationship alternation in Cypher must use a single leading colon: `[:HAS_PART|HAS_CHAPTER|HAS_SECTION*0..6]` — never `[:HAS_PART|:HAS_CHAPTER|...]`.

Point-in-time filter pattern:
```cypher
WHERE coalesce(n.restrict_start_date, date('0001-01-01')) <= date($cutoff_date)
  AND (n.restrict_end_date IS NULL OR n.restrict_end_date >= date($cutoff_date))
```

ELI URI format for Danish legislation: `https://www.retsinformation.dk/eli/lta/{year}/{number}` (e.g. `eli/lta/2024/460` for KSL 2024).
