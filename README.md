# Dansk Skattelovgivning — Graph Agent

A Neo4j knowledge graph of Danish tax legislation sourced from [retsinformation.dk](https://www.retsinformation.dk/), with a Streamlit front-end that exposes a LangGraph ReAct agent for natural-language querying of the graph.

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
| — | LOV 482/2024 (PSL §§ 7/7a/8 reform) | 2024/482 |
| — | PSL § 20 reguleringstabel 2025–2026 | — |

## Architecture

```
User → Streamlit App
           ↓
    LangGraph ReAct Agent
           ↓
    13 StructuredTools (Python)
           ↓
    Neo4j Aura (graph DB)  +  Vector Index (intfloat/multilingual-e5-large, 1024 dims)
           ↑
    retsinformation.dk XML → danish_crawler.ipynb → loader.ipynb → vectorize_danish.py → indices.ipynb
```

### Graph Schema

Core hierarchy: `Legislation → Part → Chapter → Section → Paragraph`

Additional nodes: `Schedule`, `ScheduleParagraph`, `ExplanatoryNotes`, `Commentary`, `Citation`, `Text` (embedding carrier).

Key relationships: `HAS_PART`, `HAS_SECTION`, `HAS_PARAGRAPH`, `CITES`, `SUPERSEDES`, `SUPERSEDED_BY`, `LINKED_TO`.

Temporal fields on nodes: `restrict_start_date`, `restrict_end_date` — enable point-in-time queries.

### Agent Tools (13)

| Tool | Strategy | Purpose |
|---|---|---|
| `Legislation_Title_Resolver` | Lexical | Exact law title lookup |
| `Legislation_Finder` | Hybrid (lexical + vector) | Primary law discovery |
| `Contextual_Text_Retriever` | Vector | Evidence passages with full hierarchy context |
| `Semantic_Search` | Vector | Free-form semantic search |
| `Citation_Network_Explorer` | Graph | Citation edges between laws |
| `Supersedes_Network_Explorer` | Graph | Outgoing replacement lineage |
| `Superseded_By_Network_Explorer` | Graph | Incoming replacement lineage |
| `Legislation_By_URI` | Graph | Exact URI lookup |
| `Hierarchy_Path_Resolver` | Graph | Reconstruct node context |
| `Citation_Counts` | Graph | Inbound/outbound citation metrics |
| `Graph_Schema_Navigator` | Graph | Live schema via apoc.meta.data() |
| `Read_Only_Cypher` | Cypher | Analyst-provided read-only queries |
| `Text2Cypher_Expert` | Cypher | Last-resort NL-to-Cypher |

## Running the App

```bash
.venv/bin/streamlit run app.py
```

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

The app includes built-in observability views accessible from the sidebar:
- **Tools** — inspect all 13 agent tools: description, input schema, field types and constraints
- **Request Trace** — per-request waterfall timeline with LLM token counts, tool durations, and chain-of-thought reasoning
- **Evaluation** — run and inspect golden-set test cases (`eval_golden_set.json`, 30 cases)
- **Architecture** — system overview, ReAct loop, and graph schema diagrams
