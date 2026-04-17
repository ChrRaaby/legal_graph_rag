# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

A Neo4j knowledge graph of UK legislation texts sourced from [legislation.gov.uk](https://www.legislation.gov.uk/) (CLML/XML format). The primary objective is GraphRAG (Graph Retrieval-Augmented Generation) for legal and professional services. The Streamlit app (`app.py`) is the main deliverable.

## Environment Setup

Uses Conda. Create and activate the environment:

```bash
conda env create -f environment.yml
conda activate legal-legislation-explorer
```

## Running the App

```bash
streamlit run app.py
```

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

## Pipeline Notebooks (run in order)

1. **`crawler.ipynb`** — Recursively crawls legislation.gov.uk from seed URLs in `legislation_list.txt`, parses CLML XML, and outputs JSON.
2. **`loader.ipynb`** — Loads parsed JSON into Neo4j using PySpark. Builds the graph nodes and relationships.
3. **`vectorize.ipynb`** — Embeds `Text` nodes using `nlpaueb/legal-bert-base-uncased` (HuggingFace) and writes embeddings back to Neo4j.
4. **`indices.ipynb`** — Creates Neo4j vector and text indexes (including `text_embeddings_index` used by the app).
5. **`examples.ipynb`** — Demonstrates Cypher queries for temporal analysis, citation networks, and shortest-path queries.

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

### `app.py` — Streamlit Agent App

`build_runtime()` (cached via `@st.cache_resource`) initializes everything at startup:
- **`Neo4jAnalysis`** (`neo4j_analysis.py`) — thin wrapper around the Neo4j driver; `run_query` → list of dicts, `run_query_df` → DataFrame, `run_query_viz` → graph object for `neo4j-viz`.
- **LLM** — `gemini-2.5-flash` (Google) if `GOOGLE_API_KEY` set, else `gpt-5-mini` (OpenAI).
- **Embeddings** — `nlpaueb/legal-bert-base-uncased` via HuggingFace.
- **`GraphCypherQAChain`** — NL-to-Cypher fallback using a detailed prompt with 14 rules.
- **`Neo4jVector`** — reads from `text_embeddings_index` on `Text` nodes.
- **Agent tools** — 13 LangChain `StructuredTool`s wrapping Cypher queries and hybrid search:
  - `Legislation_Title_Resolver` — lexical title matching with ranked scoring.
  - `Legislation_Finder` — hybrid (lexical + vector) legislation discovery.
  - `Contextual_Text_Retriever` — vector search returning full Legislation → Paragraph hierarchy context.
  - `Citation_Network_Explorer`, `Supersedes_Network_Explorer`, `Superseded_By_Network_Explorer` — relationship traversal.
  - `Graph_Schema_Navigator` — returns live schema via `apoc.meta.data()`.
  - `Read_Only_Cypher` — executes analyst-provided Cypher (blocks write operations).
  - `Text2Cypher_Expert` — last-resort NL-to-Cypher via `GraphCypherQAChain`.
  - `Legislation_By_URI`, `Hierarchy_Path_Resolver`, `Citation_Counts`, `Semantic_Search`.

`stream_agent_answer()` streams the LangGraph agent and collects tool trace events displayed in Streamlit expanders.

### Sidebar Views

The sidebar offers 9 views besides Chat: full schema graph, legislation hierarchy, parts/commentaries/schedules drill-downs, supersedes network, point-in-time split view, and temporal diff (Added/Removed/Restricted paragraphs between two dates with an Altair timeline chart).

## Key Cypher Patterns

Relationship alternation in Cypher must use a single leading colon: `[:HAS_PART|HAS_CHAPTER|HAS_SECTION*0..6]` — never `[:HAS_PART|:HAS_CHAPTER|...]`.

Point-in-time filter pattern:
```cypher
WHERE coalesce(n.restrict_start_date, date('0001-01-01')) <= date($cutoff_date)
  AND (n.restrict_end_date IS NULL OR n.restrict_end_date >= date($cutoff_date))
```
