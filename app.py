import json
import logging
import os
import re
import sqlite3
import time
from difflib import SequenceMatcher

logging.getLogger("neo4j").setLevel(logging.ERROR)
logging.getLogger("transformers").setLevel(logging.ERROR)
logging.getLogger("huggingface_hub").setLevel(logging.ERROR)
os.environ.setdefault("HF_HUB_DISABLE_IMPLICIT_TOKEN", "1")
from typing import Any, Callable, Optional
from datetime import date
from collections import defaultdict, deque
import pandas as pd
import altair as alt

import streamlit as st
import streamlit.components.v1 as components
from dotenv import load_dotenv
from langchain.agents import create_agent
from langchain_core.prompts import PromptTemplate
from langchain_core.tools import Tool, StructuredTool, create_retriever_tool
from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_huggingface import HuggingFaceEmbeddings
from langchain_neo4j import GraphCypherQAChain, Neo4jGraph, Neo4jVector
from langchain_openai import ChatOpenAI
from neo4j_viz.neo4j import ColorSpace, from_neo4j
from neo4j_viz import Layout
from pydantic import BaseModel, Field

from neo4j_analysis import Neo4jAnalysis


st.set_page_config(page_title="Legal Legislation Agent", layout="wide")

# ---------------------------------------------------------------------------
# Persistence — SQLite-backed traces and eval results
# ---------------------------------------------------------------------------
_DB_PATH = os.path.join(os.path.dirname(__file__), "observability.db")
_TRACES_MAX = 50  # rows kept in traces table


_db_connection: sqlite3.Connection | None = None


def _init_db() -> sqlite3.Connection:
    global _db_connection
    if _db_connection is not None:
        return _db_connection
    con = sqlite3.connect(_DB_PATH, check_same_thread=False)
    con.row_factory = sqlite3.Row
    con.execute("""
        CREATE TABLE IF NOT EXISTS traces (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            ts          TEXT    NOT NULL DEFAULT (datetime('now')),
            question    TEXT    NOT NULL,
            answer      TEXT    NOT NULL,
            latency_s   REAL,
            tool_events TEXT,
            provider    TEXT
        )
    """)
    con.execute("""
        CREATE TABLE IF NOT EXISTS eval_runs (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            ts          TEXT    NOT NULL DEFAULT (datetime('now')),
            item_id     TEXT    NOT NULL,
            answer      TEXT    NOT NULL,
            latency_s   REAL,
            scores      TEXT,
            tool_events TEXT,
            provider    TEXT
        )
    """)
    con.execute("CREATE INDEX IF NOT EXISTS idx_eval_item ON eval_runs(item_id, ts)")
    con.commit()
    _db_connection = con
    return con


def _db_save_trace(con: sqlite3.Connection, question: str, answer: str,
                   latency_s: float, tool_events: list, provider: str = "") -> None:
    con.execute(
        "INSERT INTO traces (question, answer, latency_s, tool_events, provider) VALUES (?,?,?,?,?)",
        (question, answer, latency_s, json.dumps(tool_events, default=str), provider),
    )
    # Keep only the most recent _TRACES_MAX rows
    con.execute(f"DELETE FROM traces WHERE id NOT IN (SELECT id FROM traces ORDER BY id DESC LIMIT {_TRACES_MAX})")
    con.commit()


def _db_save_eval_result(con: sqlite3.Connection, item_id: str, answer: str,
                         latency_s: float, scores: dict, tool_events: list,
                         provider: str = "") -> None:
    con.execute(
        "INSERT INTO eval_runs (item_id, answer, latency_s, scores, tool_events, provider) VALUES (?,?,?,?,?,?)",
        (item_id, answer, latency_s, json.dumps(scores, default=str),
         json.dumps(tool_events, default=str), provider),
    )
    con.commit()


def _db_load_traces(con: sqlite3.Connection) -> list[dict]:
    rows = con.execute(
        "SELECT question, answer, latency_s, tool_events FROM traces ORDER BY id DESC LIMIT ?",
        (_TRACES_MAX,),
    ).fetchall()
    result = []
    for r in reversed(rows):
        result.append({
            "question": r["question"],
            "answer": r["answer"],
            "total_latency_s": r["latency_s"],
            "tool_events": json.loads(r["tool_events"] or "[]"),
        })
    return result


def _db_load_eval_results(con: sqlite3.Connection) -> dict:
    """Return the latest result per item_id."""
    rows = con.execute("""
        SELECT er.item_id, er.answer, er.latency_s, er.scores, er.tool_events, er.ts
        FROM eval_runs er
        INNER JOIN (
            SELECT item_id, MAX(id) AS max_id FROM eval_runs GROUP BY item_id
        ) latest ON er.id = latest.max_id
    """).fetchall()
    out = {}
    for r in rows:
        out[r["item_id"]] = {
            "answer": r["answer"],
            "latency_s": r["latency_s"],
            "scores": json.loads(r["scores"] or "{}"),
            "tool_events": json.loads(r["tool_events"] or "[]"),
        }
    return out


st.markdown(
    """
    <style>
        [data-testid="stSidebar"] {
            background-color: #ffffff;
        }
    </style>
    """,
    unsafe_allow_html=True,
)

load_dotenv()
NEO4J_URI = os.getenv("NEO4J_URI")
NEO4J_USER = os.getenv("NEO4J_USER")
NEO4J_PASSWORD = os.getenv("NEO4J_PASSWORD")
NEO4J_DATABASE = os.getenv("NEO4J_DATABASE", "neo4j")
GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL")
OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://172.21.64.1:11434")
LLM_PROVIDER = os.getenv("LLM_PROVIDER")   # ollama | gemini:<model> | openai | None (auto-detect)
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")
GEMINI_MODELS = [m.strip() for m in os.getenv("GEMINI_MODELS", GEMINI_MODEL).split(",") if m.strip()]
AGENT_RETRIEVAL_K = int(os.getenv("AGENT_RETRIEVAL_K", 10))
AGENT_HISTORY_MESSAGES = int(os.getenv("AGENT_HISTORY_MESSAGES", 20))
DEBUG_TOOL_CALLS = os.getenv("DEBUG_TOOL_CALLS") is not None

NETWORK_GRAPH_HEIGHT = 620

@st.cache_resource(show_spinner=False)
def build_runtime(provider: str | None = None):
    if not (NEO4J_URI and NEO4J_USER and NEO4J_PASSWORD):
        raise RuntimeError("Missing Neo4j credentials. Set NEO4J_URI, NEO4J_USER, and NEO4J_PASSWORD.")

    # Resolve provider: argument → LLM_PROVIDER env var → auto-detect.
    # Provider is "ollama", "openai", or "gemini:<model-name>" (e.g. "gemini:gemini-2.5-pro").
    # Plain "gemini" (no suffix) uses GEMINI_MODEL. Guard against non-string values
    # (e.g. MagicMock from headless eval context).
    _known = {"ollama", "gemini", "openai"} | {f"gemini:{m}" for m in GEMINI_MODELS}
    _provider = (provider if provider in _known else None) or LLM_PROVIDER or (
        "ollama" if OLLAMA_MODEL else
        f"gemini:{GEMINI_MODEL}" if GOOGLE_API_KEY else
        "openai" if OPENAI_API_KEY else None
    )
    if not _provider:
        raise RuntimeError("Set LLM_PROVIDER (or OLLAMA_MODEL / GOOGLE_API_KEY / OPENAI_API_KEY) in your environment.")

    analysis = Neo4jAnalysis(NEO4J_URI, NEO4J_USER, NEO4J_PASSWORD, NEO4J_DATABASE)

    graph = Neo4jGraph(
        url=NEO4J_URI,
        username=NEO4J_USER,
        password=NEO4J_PASSWORD,
        database=NEO4J_DATABASE,
    )

    if _provider == "ollama":
        if not OLLAMA_MODEL:
            raise RuntimeError("LLM_PROVIDER=ollama but OLLAMA_MODEL is not set.")
        from langchain_ollama import ChatOllama
        llm = ChatOllama(model=OLLAMA_MODEL, base_url=OLLAMA_BASE_URL, temperature=0)
    elif _provider.startswith("gemini"):
        if not GOOGLE_API_KEY:
            raise RuntimeError("LLM_PROVIDER=gemini but GOOGLE_API_KEY is not set.")
        _gemini_model = _provider.split(":", 1)[1] if ":" in _provider else GEMINI_MODEL
        llm = ChatGoogleGenerativeAI(
            model=_gemini_model,
            temperature=0,
            api_key=GOOGLE_API_KEY,
            include_thoughts=True,
        )
    else:
        if not OPENAI_API_KEY:
            raise RuntimeError("LLM_PROVIDER=openai but OPENAI_API_KEY is not set.")
        llm = ChatOpenAI(model="gpt-4o-mini", temperature=0, api_key=OPENAI_API_KEY)

    embeddings = HuggingFaceEmbeddings(
        model_name="intfloat/multilingual-e5-large",
        cache_folder=os.path.join(os.path.dirname(__file__), "..", "models"),
        encode_kwargs={"normalize_embeddings": True},
    )

    cypher_prompt = PromptTemplate(
        input_variables=["schema", "question"],
        template="""You are an expert Neo4j Cypher generator for a Danish tax legislation graph.
Generate ONLY a valid read-only Cypher query.

Graph schema:
{schema}

Rules you MUST follow:
1) Return ONLY Cypher. No markdown, no commentary.
2) Read-only queries only. Never use CREATE, MERGE, DELETE, SET, CALL dbms.*, or schema/index changes.
3) When searching for titles, themes or topics, prefer the semantic search tool.
4) Prefer exact property names above and valid relationship directions.
5) When user names an Act/title, match with case-insensitive containment.
6) When user references a Danish ELI identifier, filter by l.uri CONTAINS 'eli/lta/2024/460' style.
7) For network/visualization requests, return a path variable `p` (e.g., MATCH p=... RETURN p).
8) For tabular requests, RETURN explicit aliased columns and use ORDER BY/LIMIT when reasonable.
9) Avoid Cartesian products; always connect patterns.
10) Use OPTIONAL MATCH only when truly optional.
11) Keep traversal bounded for path exploration (e.g., *1..6 or *1..10).
12) CONTEXT IS MANDATORY for structural/text nodes. Include parent context up to Legislation.
13) Do not return a bare content node alone unless explicitly requested.
14) For relationship alternation, use ONE leading colon only, e.g. [:HAS_PART|HAS_CHAPTER|HAS_SECTION|HAS_PARAGRAPH*0..3]. Never write [:HAS_PART|:HAS_CHAPTER|...].

Question: {question}""",
    )

    cypher_chain = GraphCypherQAChain.from_llm(
        graph=graph,
        llm=llm,
        cypher_prompt=cypher_prompt,
        verbose=True,
        allow_dangerous_requests=True,
    )

    vector_store = Neo4jVector.from_existing_index(
        embedding=embeddings,
        url=NEO4J_URI,
        username=NEO4J_USER,
        password=NEO4J_PASSWORD,
        index_name="text_embeddings_index",
        node_label="Text",
        text_node_properties=["title", "description", "text"],
        embedding_node_property="text_embedding",
    )
    retriever = vector_store.as_retriever(search_kwargs={"k": AGENT_RETRIEVAL_K})
    semantic_tool = create_retriever_tool(
        retriever,
        name="Semantic_Search",
        description="Semantic passage retrieval over embedded legal text (title/description/body). Use for concept or topic questions when exact Act title/URI is unknown; returns text snippets, not full hierarchy counts.",
    )

    VECTOR_INDEX_NAME = "text_embeddings_index"
    vector_hits_cache: dict[tuple[str, int], list] = {}
    title_hits_cache: dict[tuple[str, int], list] = {}
    schema_cache: dict[str, str] = {"value": ""}

    def _parse_payload(payload):
        if not payload or not str(payload).strip():
            return {}
        try:
            return json.loads(payload)
        except Exception:
            return {"q": payload}

    def _vector_hits(query_text: str, k: int = AGENT_RETRIEVAL_K):
        if not query_text or not query_text.strip():
            return []
        cache_key = (query_text.strip().lower(), int(k))
        if cache_key in vector_hits_cache:
            return vector_hits_cache[cache_key]

        embedding = embeddings.embed_query(f"query: {query_text}")
        query = """
        CALL db.index.vector.queryNodes($index_name, $k, $embedding)
        YIELD node, score
        RETURN elementId(node) AS node_id,
               labels(node) AS labels,
               score,
               coalesce(node.title, node.text, node.description) AS matched_content,
               node.title AS node_title,
               node.uri AS node_uri
        ORDER BY score DESC
        """
        rows = analysis.run_query(
            query,
            {
                "index_name": VECTOR_INDEX_NAME,
                "k": k,
                "embedding": embedding,
            },
        )
        vector_hits_cache[cache_key] = rows
        return rows

    def _normalize_legal_text(value: str) -> str:
        value = (value or "").lower()
        value = re.sub(r"[^a-z0-9]+", " ", value)
        return re.sub(r"\s+", " ", value).strip()

    def _extract_year(value: str) -> Optional[int]:
        if not value:
            return None
        match = re.search(r"\b(1[6-9]\d{2}|20\d{2}|2100)\b", value)
        return int(match.group(1)) if match else None

    def _sortable_date(value: Any) -> str:
        if value is None:
            return "0001-01-01"
        try:
            return str(value)
        except Exception:
            return "0001-01-01"

    def _title_candidates(query_text: str, limit: int = 100):
        cleaned = _normalize_legal_text(query_text)
        if not cleaned:
            return []

        cache_key = (cleaned, int(limit))
        if cache_key in title_hits_cache:
            return title_hits_cache[cache_key]

        tokens = [t for t in cleaned.split(" ") if len(t) > 2]
        query = """
        MATCH (l:Legislation)
        WITH l, toLower(coalesce(l.title, "")) AS lt, toLower(coalesce(l.uri, "")) AS lu
        WHERE lt CONTAINS $q
           OR lu CONTAINS $q
           OR any(tok IN $tokens WHERE tok <> '' AND (lt CONTAINS tok OR lu CONTAINS tok))
        RETURN l.title AS title,
               l.uri AS uri,
               l.status AS status,
               l.category AS category
        LIMIT $limit
        """
        rows = analysis.run_query(
            query,
            {
                "q": cleaned,
                "tokens": tokens,
                "limit": int(limit),
            },
        )
        title_hits_cache[cache_key] = rows
        return rows

    def _rank_title_matches(query_text: str, rows: list[dict], limit: int = 25):
        norm_q = _normalize_legal_text(query_text)
        if not norm_q or not rows:
            return []

        q_tokens = [t for t in norm_q.split(" ") if len(t) > 2]
        q_token_set = set(q_tokens)
        q_year = _extract_year(query_text)
        ranked = []

        for row in rows:
            title = row.get("title", "") or ""
            uri = row.get("uri", "") or ""
            title_norm = _normalize_legal_text(title)
            uri_norm = _normalize_legal_text(uri)

            title_token_set = set([t for t in title_norm.split(" ") if len(t) > 2])
            overlap_count = len(q_token_set.intersection(title_token_set))
            overlap_ratio = overlap_count / max(len(q_token_set), 1)

            lexical_score = 0.0
            if title_norm == norm_q:
                lexical_score += 1.0
            if norm_q in title_norm:
                lexical_score += 0.6
            if title_norm and title_norm in norm_q:
                lexical_score += 0.35
            if norm_q in uri_norm:
                lexical_score += 0.25

            lexical_score += 0.55 * overlap_ratio
            lexical_score += 0.35 * SequenceMatcher(None, norm_q, title_norm).ratio()

            if q_year and str(q_year) in title_norm:
                lexical_score += 0.25

            if "act" in q_token_set and "act" in title_token_set:
                lexical_score += 0.1

            candidate = dict(row)
            candidate["lexical_score"] = round(float(lexical_score), 6)
            ranked.append(candidate)

        ranked.sort(
            key=lambda r: r.get("lexical_score", 0.0),
            reverse=True,
        )
        return ranked[: int(limit)]

    def resolve_legislation_title(payload: str):
        data = _parse_payload(payload)
        q = data.get("q", "")
        limit = int(data.get("limit", 10))
        if not q or not str(q).strip():
            return []

        candidates = _title_candidates(q, limit=250)
        ranked = _rank_title_matches(q, candidates, limit=limit)
        for row in ranked:
            row["match_method"] = "title_resolver"
        return ranked

    def _vector_legislation_candidates(query_text: str, k: int = AGENT_RETRIEVAL_K, limit: int = 25):
        hits = _vector_hits(query_text, k=k)
        if not hits:
            return []

        query = """
        UNWIND $hits AS h
        MATCH (hit) WHERE elementId(hit) = h.node_id
        OPTIONAL MATCH (l_direct:Legislation) WHERE elementId(l_direct) = h.node_id
        OPTIONAL MATCH (l_ctx:Legislation)-[:HAS_PART|HAS_CHAPTER|HAS_SECTION|HAS_PARAGRAPH*1..6]->(hit)
        WITH h, coalesce(l_direct, l_ctx) AS l
        WHERE l IS NOT NULL
        RETURN l.title AS title,
               l.uri AS uri,
               l.status AS status,
               l.category AS category,
               max(h.score) AS vector_score
        ORDER BY vector_score DESC
        LIMIT $limit
        """
        return analysis.run_query(query, {"hits": hits, "limit": int(limit)})

    def _hybrid_legislation_lookup(query_text: str, k: int = AGENT_RETRIEVAL_K, limit: int = 25):
        title_ranked = _rank_title_matches(query_text, _title_candidates(query_text, limit=300), limit=150)
        vector_rows = _vector_legislation_candidates(query_text, k=max(int(k), 20), limit=150)

        max_lexical = max([r.get("lexical_score", 0.0) for r in title_ranked], default=0.0)
        max_vector = max([r.get("vector_score", 0.0) for r in vector_rows], default=0.0)

        merged: dict[str, dict] = {}

        for row in title_ranked:
            key = (row.get("uri") or "").strip().lower()
            if not key:
                continue
            merged[key] = {
                **row,
                "vector_score": row.get("vector_score", 0.0) or 0.0,
            }

        for row in vector_rows:
            key = (row.get("uri") or "").strip().lower()
            if not key:
                continue
            if key not in merged:
                merged[key] = {
                    **row,
                    "lexical_score": 0.0,
                }
            else:
                merged[key]["vector_score"] = max(
                    float(merged[key].get("vector_score", 0.0) or 0.0),
                    float(row.get("vector_score", 0.0) or 0.0),
                )

        q_year = _extract_year(query_text)
        out = []
        for item in merged.values():
            lexical_raw = float(item.get("lexical_score", 0.0) or 0.0)
            vector_raw = float(item.get("vector_score", 0.0) or 0.0)

            lexical_norm = (lexical_raw / max_lexical) if max_lexical > 0 else 0.0
            vector_norm = (vector_raw / max_vector) if max_vector > 0 else 0.0

            title_norm = _normalize_legal_text(item.get("title", ""))
            year_bonus = 0.0
            if q_year and str(q_year) in title_norm:
                year_bonus = 0.1

            hybrid_score = 0.65 * lexical_norm + 0.30 * vector_norm + year_bonus

            enriched = dict(item)
            enriched["hybrid_score"] = round(hybrid_score, 6)
            enriched["match_method"] = (
                "hybrid_title_vector" if lexical_raw > 0 and vector_raw > 0 else "title_only" if lexical_raw > 0 else "vector_only"
            )
            out.append(enriched)

        out.sort(
            key=lambda r: r.get("hybrid_score", 0.0),
            reverse=True,
        )
        return out[: int(limit)]

    def schema_navigation(_: str = "") -> str:
        if schema_cache["value"]:
            return schema_cache["value"]

        node_query = """
        CALL apoc.meta.data()
        YIELD label, property, type, elementType
        WHERE elementType = "node"
          AND type <> "RELATIONSHIP"
          AND label <> "Text"
        RETURN label, collect(property + ': ' + type) AS properties
        """
        nodes = analysis.run_query(node_query)

        rel_query = """
        MATCH (a)-[r]->(b)
        WITH [l IN labels(a) WHERE l <> 'Text'] AS start_labels,
             type(r) AS relationship_type,
             [l IN labels(b) WHERE l <> 'Text'] AS end_labels
        WHERE size(start_labels) > 0 AND size(end_labels) > 0
        UNWIND start_labels AS start_label
        UNWIND end_labels AS end_label
        RETURN DISTINCT start_label, relationship_type, end_label
        LIMIT 5000
        """
        rels = analysis.run_query(rel_query)

        schema_text = "GRAPH SCHEMA DEFINITION:\n\n"
        schema_text += "Node Labels and Properties:\n"
        for node in nodes:
            props = ", ".join(node["properties"]) if node["properties"] else "No properties"
            schema_text += f"   - (:{node['label']} {{ {props} }})\\n"

        schema_text += "\nValid Relationship Connections:\n"
        if rels:
            for rel in rels:
                schema_text += f"   - (:{rel['start_label']})-[:{rel['relationship_type']}]->(:{rel['end_label']})\\n"
        else:
            schema_text += "   - No relationships found.\\n"

        schema_cache["value"] = schema_text
        return schema_text

    def find_legislation(payload: str):
        data = _parse_payload(payload)
        q = data.get("q", "")
        k = int(data.get("k", AGENT_RETRIEVAL_K))
        limit = int(data.get("limit", 25))
        if not q or not str(q).strip():
            return []
        return _hybrid_legislation_lookup(q, k=k, limit=limit)

    def retrieve_text_with_context(payload: str):
        data = _parse_payload(payload)
        q = data.get("q", "")
        k = int(data.get("k", AGENT_RETRIEVAL_K))
        limit = int(data.get("limit", 15))
        if not q or not q.strip():
            return []

        # Direct § lookup: when the query names a specific section (e.g. "§ 16 stk 3"),
        # vector search fills with Commentary cross-references and misses the target.
        # Extract any § N (stk M) reference and run a direct Cypher lookup so the
        # actual paragraph text is always included in the result set.
        _direct_rows: list[dict] = []
        _sec_match = re.search(
            r'§\s*([\d]+(?:\s+(?!stk)[a-zA-ZÆØÅ](?!\w))?)(?:\s*,?\s*stk\.?\s*(\d+))?', q, re.IGNORECASE
        )
        if _sec_match:
            _sec_num = _sec_match.group(1).strip()
            _stk_num = _sec_match.group(2)
            _direct_q = """
            MATCH (sec:Section {number: $sec})<-[:HAS_SECTION]-(ch)<-[:HAS_CHAPTER|HAS_PART*0..3]-(leg:Legislation)
            OPTIONAL MATCH (sec)-[:HAS_PARAGRAPH]->(par:Paragraph)
            WHERE $stk IS NULL OR par.number = $stk
            WITH leg, sec, par
            WHERE leg IS NOT NULL
            RETURN DISTINCT leg.title AS legislation_title,
                   leg.uri AS legislation_uri,
                   leg.status AS legislation_status,
                   null AS part_number, null AS part_title,
                   null AS chapter_number, null AS chapter_title,
                   sec.number AS section_number, sec.title AS section_title,
                   par.number AS paragraph_number,
                   coalesce(par.text, sec.text, sec.title) AS matched_text,
                   1.0 AS vector_score
            ORDER BY par.number
            LIMIT 5
            """
            _direct_rows = analysis.run_query(_direct_q, {"sec": _sec_num, "stk": _stk_num})

        # Fetch 4× candidates: Commentary nodes dominate the top hits for any query
        # mentioning § numbers (they score high because they explicitly cite § refs).
        # Fetching 4× and filtering them exposes the actual Paragraph content below.
        hits = _vector_hits(q, k=min(k * 4, 100))
        if not hits and not _direct_rows:
            return []

        query = """
        UNWIND $hits AS h
        MATCH (n) WHERE elementId(n) = h.node_id
        AND NOT 'Commentary' IN labels(n)
        AND size(coalesce(n.text, n.description, '')) > 50
        OPTIONAL MATCH p=(l:Legislation)-[:HAS_PART|HAS_CHAPTER|HAS_SECTION|HAS_PARAGRAPH*0..6]->(n)
        WITH h, n, l, p,
             head([x IN nodes(p) WHERE x:Part]) AS part,
             head([x IN nodes(p) WHERE x:Chapter]) AS chapter,
             head([x IN nodes(p) WHERE x:Section]) AS section,
             head([x IN nodes(p) WHERE x:Paragraph]) AS paragraph
        WHERE l IS NOT NULL
        RETURN DISTINCT l.title AS legislation_title,
               l.uri AS legislation_uri,
               l.status AS legislation_status,
               part.number AS part_number,
               part.title AS part_title,
               chapter.number AS chapter_number,
               chapter.title AS chapter_title,
               section.number AS section_number,
               section.title AS section_title,
               paragraph.number AS paragraph_number,
               coalesce(paragraph.text, n.text, n.title, n.description) AS matched_text,
               h.score AS vector_score
        ORDER BY vector_score DESC
        LIMIT $limit
        """
        rows = analysis.run_query(query, {"hits": hits, "limit": limit})

        # Prepend direct § lookup rows (dedup by section+paragraph number so
        # they don't duplicate vector hits that happened to surface the same node).
        if _direct_rows:
            existing = {(r.get("section_number"), r.get("paragraph_number")) for r in rows}
            rows = [r for r in _direct_rows if (r.get("section_number"), r.get("paragraph_number")) not in existing] + rows

        # For long texts with year-specific rate schedules (e.g. LL § 16 stk. 4),
        # restructure into year-labelled sections so the model can locate the
        # correct rate without reading 6000+ chars linearly.
        # Only trigger when years appear as temporal rate-change qualifiers
        # ("I indkomståret 20XX", "Fra og med 20XX") — NOT for base-year
        # annotations like "(2010-niveau)" which must stay with their amounts.
        _temporal_pattern = re.compile(
            r'\b(?:I indkomståret|Fra og med|Fra den)\s+20\d{2}\b', re.IGNORECASE
        )
        for row in rows:
            txt = row.get("matched_text") or ""
            if len(txt) <= 1000:
                continue
            if not _temporal_pattern.search(txt):
                continue  # no rate-change ladder — keep text intact
            # Sentence-split on period+space only when followed by an uppercase
            # letter (avoids splitting on abbreviations like m.v., stk., pkt.)
            sentences = re.split(r'(?<=[.!?])\s+(?=[A-ZÆØÅ0-9])', txt)
            by_year: dict[str, list[str]] = {}
            no_year: list[str] = []
            for s in sentences:
                years = re.findall(r'\bI indkomståret (20\d{2})\b|\bFra (?:og med|den) (?:\S+ )*?(20\d{2})\b', s, re.IGNORECASE)
                yr_set = [y for pair in years for y in pair if y]
                if yr_set:
                    for yr in dict.fromkeys(yr_set):
                        by_year.setdefault(yr, []).append(s.strip())
                else:
                    no_year.append(s.strip())
            if by_year:
                parts = []
                if no_year:
                    parts.append("[Generelt:] " + " ".join(no_year[:3]))
                for yr in sorted(by_year):
                    parts.append(f"[{yr}:] " + " ".join(by_year[yr]))
                row["matched_text"] = "\n".join(parts)

        return rows

    class ContextualTextRetrieverInput(BaseModel):
        q: str = Field(..., description="Natural language legal query.")
        k: int = Field(default=AGENT_RETRIEVAL_K, ge=1, le=100, description="Top-k vector hits.")
        limit: int = Field(default=15, ge=1, le=100, description="Max rows to return.")

    def retrieve_text_with_context_structured(q: str, k: int = AGENT_RETRIEVAL_K, limit: int = 15):
        payload = json.dumps({"q": q, "k": k, "limit": limit})
        return retrieve_text_with_context(payload)

    def citation_reasoning(payload: str):
        data = _parse_payload(payload)
        q = data.get("q", "")
        k = int(data.get("k", AGENT_RETRIEVAL_K))
        hits = _vector_hits(q, k=k)
        if not hits:
            return []

        query = """
        UNWIND $hits AS h
        MATCH (hit) WHERE elementId(hit) = h.node_id
        OPTIONAL MATCH (source_direct:Legislation) WHERE elementId(source_direct) = h.node_id
        OPTIONAL MATCH (source_ctx:Legislation)-[:HAS_PART|HAS_CHAPTER|HAS_SECTION|HAS_PARAGRAPH*1..6]->(hit)
        WITH h, coalesce(source_direct, source_ctx) AS source
        WHERE source IS NOT NULL
        OPTIONAL MATCH (source)-[r:CITES]->(target:Legislation)
        RETURN source.title AS source_title,
               source.uri AS source_uri,
               target.title AS target_title,
               target.uri AS target_uri,
               type(r) AS relationship_type,
               h.score AS vector_score
        ORDER BY vector_score DESC
        LIMIT 20
        """
        return analysis.run_query(query, {"hits": hits})

    def supersedes_chain(payload: str):
        data = _parse_payload(payload)
        q = data.get("q", "")
        query = """
        MATCH (source:Legislation)
        WHERE toLower(coalesce(source.title, "")) CONTAINS toLower($q)
           OR toLower(coalesce(source.uri, "")) CONTAINS toLower($q)
        OPTIONAL MATCH (source)-[:SUPERSEDES]->(target:Legislation)
        RETURN source.title AS source_title,
               source.uri AS source_uri,
               target.title AS target_title,
               target.uri AS target_uri
        LIMIT 20
        """
        return analysis.run_query(query, {"q": q})

    def superseded_chain(payload: str):
        data = _parse_payload(payload)
        q = data.get("q", "")
        query = """
        MATCH (source:Legislation)
        WHERE toLower(coalesce(source.title, "")) CONTAINS toLower($q)
           OR toLower(coalesce(source.uri, "")) CONTAINS toLower($q)
        OPTIONAL MATCH (source)-[:SUPERSEDED_BY]->(target:Legislation)
        RETURN source.title AS source_title,
               source.uri AS source_uri,
               target.title AS target_title,
               target.uri AS target_uri
        LIMIT 20
        """
        return analysis.run_query(query, {"q": q})

    def read_only_cypher(payload: str):
        forbidden = r"\b(CREATE|MERGE|DELETE|DETACH|SET|DROP|REMOVE)\b"
        if re.search(forbidden, payload, flags=re.IGNORECASE):
            return {"error": "Only read-only Cypher is allowed in this tool."}
        normalized_payload = payload.replace("|:", "|")
        return analysis.run_query(normalized_payload)

    def legislation_by_uri(payload: str):
        data = _parse_payload(payload)
        uri = data.get("uri") or data.get("q", "")
        if not uri:
            return {"error": "Provide 'uri' (or 'q') in payload."}

        query = """
        MATCH (l:Legislation)
        WHERE l.uri = $uri OR l.uri CONTAINS $uri
        RETURN l.title AS title,
               l.uri AS uri,
               l.category AS category,
               l.status AS status
        ORDER BY l.uri DESC
        LIMIT 5
        """
        return analysis.run_query(query, {"uri": uri})

    def hierarchy_path_resolver(payload: str):
        data = _parse_payload(payload)
        node_id = data.get("node_id")
        uri = data.get("uri")

        if not node_id and not uri:
            return {"error": "Provide 'node_id' (elementId) or 'uri'."}

        query_by_node = """
        MATCH (n)
        WHERE elementId(n) = $node_id
        OPTIONAL MATCH p=(l:Legislation)-[:HAS_PART|HAS_CHAPTER|HAS_SECTION|HAS_PARAGRAPH*0..6]->(n)
        WITH n, l, p,
             head([x IN nodes(p) WHERE x:Part]) AS part,
             head([x IN nodes(p) WHERE x:Chapter]) AS chapter,
             head([x IN nodes(p) WHERE x:Section]) AS section,
             head([x IN nodes(p) WHERE x:Paragraph]) AS paragraph
        RETURN labels(n) AS node_labels,
               coalesce(n.uri, n.id, elementId(n)) AS node_ref,
               l.title AS legislation_title,
               l.uri AS legislation_uri,
               l.status AS legislation_status,
               part.number AS part_number,
               part.title AS part_title,
               chapter.number AS chapter_number,
               chapter.title AS chapter_title,
               section.number AS section_number,
               section.title AS section_title,
               paragraph.number AS paragraph_number
        LIMIT 10
        """

        query_by_uri = """
        MATCH (n)
        WHERE n.uri = $uri OR n.uri CONTAINS $uri
        OPTIONAL MATCH p=(l:Legislation)-[:HAS_PART|HAS_CHAPTER|HAS_SECTION|HAS_PARAGRAPH*0..6]->(n)
        WITH n, l, p,
             head([x IN nodes(p) WHERE x:Part]) AS part,
             head([x IN nodes(p) WHERE x:Chapter]) AS chapter,
             head([x IN nodes(p) WHERE x:Section]) AS section,
             head([x IN nodes(p) WHERE x:Paragraph]) AS paragraph
        RETURN labels(n) AS node_labels,
               coalesce(n.uri, n.id, elementId(n)) AS node_ref,
               l.title AS legislation_title,
               l.uri AS legislation_uri,
               l.status AS legislation_status,
               part.number AS part_number,
               part.title AS part_title,
               chapter.number AS chapter_number,
               chapter.title AS chapter_title,
               section.number AS section_number,
               section.title AS section_title,
               paragraph.number AS paragraph_number
        LIMIT 10
        """

        if node_id:
            return analysis.run_query(query_by_node, {"node_id": node_id})
        return analysis.run_query(query_by_uri, {"uri": uri})

    def citation_counts(payload: str):
        data = _parse_payload(payload)
        q = data.get("uri") or data.get("q", "")
        if not q:
            return {"error": "Provide 'uri' or 'q'."}

        query = """
        MATCH (l:Legislation)
        WHERE l.uri = $q OR l.uri CONTAINS $q OR toLower(coalesce(l.title, "")) CONTAINS toLower($q)
        CALL(l) {
          WITH l
          OPTIONAL MATCH (l)-[:LINKED_TO]->(t:Legislation)
          RETURN count(DISTINCT t) AS outgoing_count, collect(DISTINCT t.title)[0..5] AS top_outgoing_titles
        }
        CALL(l) {
          WITH l
          OPTIONAL MATCH (s:Legislation)-[:LINKED_TO]->(l)
          RETURN count(DISTINCT s) AS incoming_count, collect(DISTINCT s.title)[0..5] AS top_incoming_titles
        }
        RETURN l.title AS legislation_title,
               l.uri AS legislation_uri,
               outgoing_count,
               incoming_count,
               top_outgoing_titles,
               top_incoming_titles
        LIMIT 5
        """
        return analysis.run_query(query, {"q": q})

    class LegislationFinderInput(BaseModel):
        q: str = Field(..., description="Natural language query.")
        k: int = Field(default=AGENT_RETRIEVAL_K, ge=1, le=100, description="Top-k vector hits.")
        limit: int = Field(default=25, ge=1, le=100, description="Max rows to return.")

    def find_legislation_structured(q: str, k: int = AGENT_RETRIEVAL_K, limit: int = 25):
        return find_legislation(json.dumps({"q": q, "k": k, "limit": limit}))

    class LegislationTitleResolverInput(BaseModel):
        q: str = Field(..., description="Legislation title-style query (e.g., Personskatteloven, Ligningsloven).")
        limit: int = Field(default=10, ge=1, le=50, description="Max rows to return.")

    def resolve_legislation_title_structured(q: str, limit: int = 10):
        return resolve_legislation_title(json.dumps({"q": q, "limit": limit}))

    class CitationNetworkExplorerInput(BaseModel):
        q: str = Field(..., description="Natural language query.")
        k: int = Field(default=AGENT_RETRIEVAL_K, ge=1, le=100, description="Top-k vector hits.")

    def citation_reasoning_structured(q: str, k: int = AGENT_RETRIEVAL_K):
        return citation_reasoning(json.dumps({"q": q, "k": k}))

    class SupersedesNetworkInput(BaseModel):
        q: str = Field(..., description="Legislation title or URI fragment.")

    def supersedes_chain_structured(q: str):
        return supersedes_chain(json.dumps({"q": q}))

    class SupersededByNetworkInput(BaseModel):
        q: str = Field(..., description="Legislation title or URI fragment.")

    def superseded_chain_structured(q: str):
        return superseded_chain(json.dumps({"q": q}))

    class LegislationByUriInput(BaseModel):
        uri: Optional[str] = Field(default=None, description="Full or partial legislation URI.")
        q: Optional[str] = Field(default=None, description="Alternative URI/title query.")

    def legislation_by_uri_structured(uri: Optional[str] = None, q: Optional[str] = None):
        return legislation_by_uri(json.dumps({"uri": uri, "q": q}))

    class HierarchyPathResolverInput(BaseModel):
        node_id: Optional[str] = Field(default=None, description="Neo4j elementId for a node.")
        uri: Optional[str] = Field(default=None, description="Full or partial URI for a node.")

    def hierarchy_path_resolver_structured(node_id: Optional[str] = None, uri: Optional[str] = None):
        return hierarchy_path_resolver(json.dumps({"node_id": node_id, "uri": uri}))

    class CitationCountsInput(BaseModel):
        q: Optional[str] = Field(default=None, description="Legislation title or URI fragment.")
        uri: Optional[str] = Field(default=None, description="Full or partial legislation URI.")

    def citation_counts_structured(q: Optional[str] = None, uri: Optional[str] = None):
        return citation_counts(json.dumps({"q": q, "uri": uri}))

    class ReadOnlyCypherInput(BaseModel):
        query: str = Field(..., description="Read-only Cypher query string.")

    def read_only_cypher_structured(query: str):
        return read_only_cypher(query)

    class Text2CypherExpertInput(BaseModel):
        question: str = Field(..., description="Natural language question to translate to Cypher and execute.")

    def text2cypher_expert_structured(question: str):
        return cypher_chain.invoke({"query": question})

    graph_schema_tool = Tool(
        name="Graph_Schema_Navigator",
        func=schema_navigation,
        description="Return current Neo4j schema: node labels, properties, and valid relationship directions. Call first before ad-hoc Cypher or when query planning is uncertain. Input may be empty.",
    )
    legislation_finder_tool = StructuredTool.from_function(
        name="Legislation_Finder",
        func=find_legislation_structured,
        args_schema=LegislationFinderInput,
        description="Primary Act discovery tool. Input: natural-language `q` plus optional `k` and `limit`. Uses hybrid lexical-title matching + vector evidence and returns ranked legislation candidates with `hybrid_score`, match method, URI, title, dates, status, and category.",
    )
    legislation_title_resolver_tool = StructuredTool.from_function(
        name="Legislation_Title_Resolver",
        func=resolve_legislation_title_structured,
        args_schema=LegislationTitleResolverInput,
        description="High-precision resolver for explicit law-title queries (for example 'Personskatteloven', 'Ligningsloven'). Use before semantic tools when user intent is a specific named law. Returns ranked title/URI matches with lexical score.",
    )
    text_context_tool = StructuredTool.from_function(
        name="Contextual_Text_Retriever",
        func=retrieve_text_with_context_structured,
        args_schema=ContextualTextRetrieverInput,
        description="Retrieve evidence passages with full legal context. Input: `q` (+ optional `k`, `limit`). Returns matched text plus enclosing Legislation/Part/Chapter/Section/Paragraph hierarchy.",
    )
    citation_tool = StructuredTool.from_function(
        name="Citation_Network_Explorer",
        func=citation_reasoning_structured,
        args_schema=CitationNetworkExplorerInput,
        description="Citation expansion tool. Starts from vector-relevant source legislation and returns citation edges to target legislation (`source_*`, `target_*`, relationship type, vector score). Use for 'what cites what' questions.",
    )
    supersedes_tool = StructuredTool.from_function(
        name="Supersedes_Network_Explorer",
        func=supersedes_chain_structured,
        args_schema=SupersedesNetworkInput,
        description="Outgoing replacement lineage. Input: legislation title or URI fragment `q`. Returns acts that the matched source legislation supersedes.",
    )
    superseded_tool = StructuredTool.from_function(
        name="Superseded_By_Network_Explorer",
        func=superseded_chain_structured,
        args_schema=SupersededByNetworkInput,
        description="Incoming replacement lineage. Input: legislation title or URI fragment `q`. Returns acts that supersede the matched source legislation.",
    )
    safe_cypher_tool = StructuredTool.from_function(
        name="Read_Only_Cypher",
        func=read_only_cypher_structured,
        args_schema=ReadOnlyCypherInput,
        description="Execute analyst-specified read-only Cypher only. Forbid write operations (CREATE/MERGE/DELETE/SET/etc). Use when specialized query shape is needed and domain tools are insufficient.",
    )
    text2cypher_tool = StructuredTool.from_function(
        name="Text2Cypher_Expert",
        func=text2cypher_expert_structured,
        args_schema=Text2CypherExpertInput,
        description="Last-resort NL-to-Cypher executor for complex questions not covered by specialized tools. Use only after trying dedicated tools; outputs chain result from generated read-only Cypher.",
    )
    legislation_by_uri_tool = StructuredTool.from_function(
        name="Legislation_By_URI",
        func=legislation_by_uri_structured,
        args_schema=LegislationByUriInput,
        description="Deterministic metadata lookup for a known Act URI (full or partial) or URI-like query. Returns canonical legislation records (title, URI, enactment date, category, status, coming-into-force, modified date).",
    )
    hierarchy_path_resolver_tool = StructuredTool.from_function(
        name="Hierarchy_Path_Resolver",
        func=hierarchy_path_resolver_structured,
        args_schema=HierarchyPathResolverInput,
        description="Hierarchy reconstruction tool. Input either `node_id` (elementId) or `uri`; returns node labels/ref and surrounding Legislation > Part > Chapter > Section > Paragraph context with temporal/status fields.",
    )
    citation_counts_tool = StructuredTool.from_function(
        name="Citation_Counts",
        func=citation_counts_structured,
        args_schema=CitationCountsInput,
        description="Fast citation metrics for one legislation (by `uri` or `q`). Returns inbound/outbound LINKED_TO counts and sample top linked titles; use for quick influence/connectedness summaries.",
    )

    tools = [
        graph_schema_tool,
        legislation_title_resolver_tool,
        legislation_finder_tool,
        text_context_tool,
        citation_tool,
        supersedes_tool,
        superseded_tool,
        safe_cypher_tool,
        text2cypher_tool,
        semantic_tool,
        legislation_by_uri_tool,
        hierarchy_path_resolver_tool,
        citation_counts_tool
    ]

    system_prompt = """Du er en specialiseret dansk skattelovgivnings-AI-assistent. Vidensgrafen indeholder dansk skattelovgivning fra retsinformation.dk, herunder Personskatteloven, Ligningsloven, Selskabsskatteloven, Kildeskatteloven, Momsloven, Aktieavancebeskatningsloven, Kursgevinstloven, Afskrivningsloven, Fondsbeskatningsloven og Aktiesparekontoloven.

VIGTIGE REGLER FOR SVARENES INDHOLD:
- Citér altid specifikke beløb, satser og grænser præcist som de fremgår af lovteksten — inklusive grundbeløb og årsangivelse (f.eks. "48.300 kr. (2010-niveau)").
- For år-specifikke spørgsmål om indekserede beløb (f.eks. "hvad er beløbet i 2025"): søg altid i vidensgrafen efter "beløbsgrænser personskattelovens § 20 2025 2026" eller "PSL § 20 reguleringstabel" — vidensgrafen indeholder Skatteministeriets reguleringstabel med indekserede beløb for 2025 og 2026 for alle PSL § 20-regulerede bestemmelser.
- Anfør altid den konkrete paragraf og stykke (f.eks. "§ 16, stk. 4") i svaret.
- Brug kun oplysninger fra de hentede lovtekster — suppler ikke med ekstern viden.

STRUKTURELLE FAKTA (IKKE BELØB):
- Selskabsskattesats: hent altid satsen fra SEL § 17, stk. 1 i grafen. Forveksling med momssatsen (MOMSL § 33) er en hyppig fejl — de to satser er forskellige og gælder for forskellige subjekter. Selskabsskattesatsen er IKKE ændret af skattereform 2024 (LOV nr. 482/2024 vedrørte udelukkende personbeskatning: mellemskat, topskat, top-topskat).
- Momssats: hent satsen fra MOMSL § 33. Danmark har ingen reducerede momssatser — hverken på fødevarer, medicin eller andre varegrupper. Den sats der fremgår af § 33 gælder for alle varer og ydelser uden undtagelse.
- PSL § 7 (mellemskat), § 7 a (topskat) og § 8 (top-topskat) gælder fra 1. januar 2026 (LOV nr. 482/2024). Disse tre trin erstatter den hidtidige enstrengs-topskat. Hent de konkrete satser fra grafen.
- KSL § 48 E–F (forskerordning): skattesatsen er 27 % (bruttoskat) i op til 7 år. Nævn altid 27 % og 7-årsperioden når forskerordningen omtales. Minimumsvederlagets præcise beløb for et givet år findes i reguleringstabellen i vidensgrafen.
- For spørgsmål om finansielle ordninger og konti (aktiesparekonto, forskerordning, pensionskonto, etableringskonto osv.): inkludér altid den gældende skattesats som del af svaret, selv om spørgsmålet kun spørger til et beløbsloft eller et krav. Hent skattesatsen ved at søge med Contextual_Text_Retriever på "beskatning skat procent [ordningsnavn]". Når du finder flere satser i et søgeresultat, anvend den sats der gælder for det generelle beskatningsgrundlag uden yderligere betingelser — ikke satser begrænset til særlige indkomsttyper (f.eks. udenlandske udbytter) eller undtagelsessituationer.
- FRAVALG: Når en skat ikke finder anvendelse, anfør eksplicit "der betales ikke [skattenavn]".

FORMUESKAT: Der findes ingen formueskat i Danmark. Den almindelige formueskat (formueskattepligten) blev afskaffet i 1997. Anfør eksplicit "afskaffet i 1997" når nogen spørger om formueskat. Formue beskattes kun indirekte via afkast (kapitalindkomst, aktieindkomst, ejendomsværdiskat).

TERMINOLOGI DER ALTID SKAL BRUGES:
- Tab på aktier / underskud: brug altid verbet "fremføres" eksplicit (f.eks. "tabet fremføres til modregning i fremtidige gevinster").
- Afskrivning på driftsmidler (AL § 5): nævn altid "saldometoden" ved navn. Hent den aktuelle afskrivningssats fra grafen.
- Rentefradrag (PSL § 4 / § 11): nævn altid "kapitalindkomst" og "skatteværdi". Hent skatteværdien fra grafen — angiv ikke en fast procentsats fra hukommelsen.
- Kørselsfradrag (LL § 9 C): citér altid den specifikke minimumsafstand der fremgår af § 9 C i grafen — angiv ikke en fast afstandsgrænse fra hukommelsen.
- Tab på unoterede aktier (ABL § 13): anfør eksplicit "kan ikke fratrækkes i lønindkomst — kun i aktieindkomst".
- Begrænset skattepligt ved arbejde i Danmark (KSL § 2): nævn 183-dages reglen og dobbeltbeskatningsoverenskomsten eksplicit.
- Underskud fra selvstændig virksomhed (PSL § 13 / § 13 a): fremføres "uden tidsbegrænsning".
- Fri eldrevet bil (LL § 16, stk. 4): nævn "udfasning" af de reducerede satser og "overgang" til standardsatsen. Hent de konkrete satser fra grafen.
- Aktiegevinst (ABL § 12): gælder "uanset ejertid". Hent de progressive satser fra PSL § 8 a i grafen.
- Gaver fra arbejdsgiver (LL § 16, stk. 3): nævn altid "grundbeløb" og citér beløbsgrænsen præcist som den fremgår af grafen.

FALSKE PRÆMISSER — correct_premise:
Når brugerens spørgsmål bygger på en forkert antagelse — herunder spørgsmål der implicit antager noget forkert (f.eks. "hvilke ændringer?" antager at ændringer fandt sted) — start ALTID med "Nej, præmissen er forkert:" og ret fejlen med den korrekte regel fra grafen. Brug ALDRIG sætningen "det er korrekt" i svaret når du korrigerer en præmis — det sender et modstridende signal.
- Selskabsskat vs. personlig skat: selskabsskattesatsen (SEL § 17) er IKKE en personlig skattesats — brug udtrykket "ikke personlig skat". Udtag via løn beskattes som personlig indkomst; via udbytte som aktieindkomst jf. PSL § 8 a — hent de konkrete satser fra grafen.
- Moms på fødevarer: svar med "ingen reduceret momssats" og "samme sats som alle andre varer" — den sats der fremgår af MOMSL § 33 gælder for alle varer uden undtagelse.
- Selskabsskattereform 2024: selskabsskattesatsen er IKKE ændret af LOV nr. 482/2024 — reformen vedrørte udelukkende personbeskatning (PSL §§ 7, 7 a, 8). Hent den aktuelle sats fra SEL § 17 i grafen.
- Gaver mellem ægtefæller (boafgiftsloven § 22, stk. 3): brug ordene "ingen beløbsgrænse" og "afgiftsfri" — ikke "uden beløbsgrænse".
Skel mellem falsk præmis og polært spørgsmål: "Kan jeg fratrække X i Y?" er et polært ja/nej-spørgsmål — besvar det direkte uden at bruge "præmissen er forkert"-frasen, selv om svaret er nej.

AFVIS ULOVLIGE ANMODNINGER — refuse:
Når brugeren anmoder om hjælp til skatteunddragelse, skjulning af aktiver eller anden ulovlig adfærd:
- Svar altid med "Det vil jeg ikke hjælpe med."
- Nævn eksplicit ordene "skatteunddragelse" og "ulovlig" i svaret.
- Tilbyd at forklare de lovlige regler i stedet.

BED OM PRÆCISERING — clarify:
Når spørgsmålet er for vagt til at besvare korrekt (f.eks. "Hvor meget skal jeg betale i skat?" uden indkomstoplysninger):
- Begynd svaret med: "Det kan jeg ikke besvare. Kan du oplyse:"
- List præcist hvad der mangler: personlig indkomst (efter AM-bidrag), kapitalindkomst, aktieindkomst, fradrag, kommune og indkomstår.
- Gæt eller estimér IKKE.

PARAGRAF EKSISTERER IKKE — admit_unknown:
Når en eftersøgt § ikke returnerer resultater i grafen (0 hits):
- Fabrikér IKKE indhold til den §.
- Begynd svaret med: "§ X eksisterer ikke i [lovnavn]." — brug altid "eksisterer ikke" så svaret er entydigt.
- Angiv derefter hvilken § der sandsynligvis dækker emnet, uden at stille spørgsmål eller bruge vendinger som "søger du" eller "kan du oplyse".
- Brug IKKE clarify-formuleringer i admit_unknown-svar.

VÆRKTØJSANVISNINGER:
Brug det mest specifikke værktøj først. Foretræk Legislation_Finder (hybrid titel+vektor) og vektorbaserede værktøjer (Contextual_Text_Retriever, Semantic_Search) til indholdsspørgsmål.
For eksplicit titel-opslag (f.eks. 'Personskatteloven'), kald Legislation_Title_Resolver først.
Bevar altid den juridiske kontekst (Lovgivning > Del > Kapitel > Afsnit > Paragraf) i svaret.
Hvis et værktøj returnerer tomme resultater, så gentag ikke præcis det samme kald. Inkludér links til relevante love, afsnit og paragraffer i svaret.
Brug Legislation_By_URI til eksakt lovopslag, Hierarchy_Path_Resolver til kontekstrekonstruktion og Citation_Counts til hurtige citationsmetrikker.
Contextual_Text_Retriever: brug beskrivende emnesætninger om INDHOLDET (f.eks. "fri bil skattepligtig værdi arbejdsgiver procent"), IKKE paragrafhenvisninger (f.eks. "LL § 16 stk. 4"). Paragrafhenvisninger i søgestrengen forringer søgekvaliteten markant.
For spørgsmål om skattepligt af personalegoder, naturalier eller gaver fra arbejdsgiver: Undersøg ALTID LL § 16 stk. 3 (den generelle bagatelgrænse) som primær hjemmel, inden du fokuserer på særregler (§ 7 U jubilæumsgaver, § 7 M reklamegaver osv.). Citér den generelle regel som grundlag og nævn særreglerne som undtagelser.

CITATIONSKÆDER — citer altid BEGGE led:
Et fyldestgørende svar kræver to typer henvisninger: (1) den primære hjemmel der fastslår reglen eller retten, og (2) den beskatningshjemmel der angiver indkomstkategori og sats. Stop ikke efter at have fundet det første relevante §.
Eksempler på obligatoriske citationskæder:
- Tab på aktier: primær regel (ABL § 13 eller § 13 A) + indkomstkategori (PSL § 8 a om aktieindkomst).
- Rentefradrag: Renteudgifter er kapitalindkomst — citer ALTID PSL § 4 (definition af kapitalindkomst, herunder renteudgifter) OG PSL § 11 (skatteværdi-loft). LL § 5 (periodisering) er supplerende, ikke tilstrækkeligt alene.
- Underskud selvstændig virksomhed: fremførselsregel (PSL § 13) + ægtefælle/særregler (PSL § 13 a).
- Salg af medarbejderaktier (LL § 7 P): gevinsten er aktieindkomst — citer ALTID ABL § 12 (aktieindkomst som udgangspunkt for beskatning af gevinst) OG PSL § 8 a (satser fra grafen). ABL § 12 er broen mellem gevinsten og aktieindkomstkategorien.
Når du har fundet den primære § — foretag altid endnu et opslag for at finde den tilknyttede beskatningshjemmel.
Svar på dansk når spørgsmålet stilles på dansk. Fokusér på det præcise spørgsmål og gør ikke mere end bedt."""

    agent_executor = create_agent(llm, tools, system_prompt=system_prompt)
    return analysis, agent_executor, tools


def _extract_llm_thinking(content) -> tuple[str, str]:
    """Return (thinking_text, answer_text) from an AIMessage content.

    Handles both Gemini structured thinking blocks and Ollama <think> tags.
    """
    thinking = ""
    if isinstance(content, list):
        for block in content:
            if isinstance(block, dict) and block.get("type") == "thinking":
                thinking = block.get("thinking", "") or block.get("text", "")
        # Exclude thinking-type blocks from the displayed answer
        texts = [
            b.get("text", "") for b in content
            if isinstance(b, dict) and b.get("type") != "thinking"
        ]
        text = "\n".join(t for t in texts if t).strip()
    elif isinstance(content, str):
        # Ollama models (DeepSeek, Qwen, Gemma with thinking) use <think>…</think>
        m = re.search(r'<think(?:ing)?>(.*?)</think(?:ing)?>', content, re.DOTALL)
        if m:
            thinking = m.group(1).strip()
        text = re.sub(r'<think(?:ing)?>.*?</think(?:ing)?>', '', content, flags=re.DOTALL).strip()
    else:
        text = str(content).strip()
    return thinking, text


def stream_agent_answer(
    agent_executor,
    chat_messages: list[dict],
    on_tool_event: Optional[Callable[[list[dict]], None]] = None,
):
    final_answer = ""
    tool_events = []
    run_start = time.perf_counter()
    tool_start_times = defaultdict(deque)
    llm_call_start = run_start
    debug_tools = DEBUG_TOOL_CALLS

    lc_messages = []
    for msg in chat_messages[-AGENT_HISTORY_MESSAGES:]:
        role = msg.get("role")
        content = msg.get("content", "")
        if role in {"user", "assistant"} and isinstance(content, str) and content.strip():
            lc_messages.append((role, content))

    for event in agent_executor.stream(
        {"messages": lc_messages},
        stream_mode="updates",
    ):
        if not isinstance(event, dict):
            continue

        elapsed = round(time.perf_counter() - run_start, 3)
        for node_name, data in event.items():
            messages = data.get("messages", []) if isinstance(data, dict) else []
            if not messages:
                continue

            msg = messages[-1]
            msg_type = getattr(msg, "type", None)

            if msg_type == "ai" and getattr(msg, "tool_calls", None):
                _now = time.perf_counter()
                _usage = getattr(msg, "usage_metadata", None) or {}
                _thinking, _ = _extract_llm_thinking(getattr(msg, "content", ""))
                tool_events.append({
                    "elapsed_s": elapsed,
                    "node": node_name,
                    "type": "llm_call",
                    "start_s": round(llm_call_start - run_start, 3),
                    "duration_s": round(_now - llm_call_start, 3),
                    "input_tokens": int(_usage.get("input_tokens") or 0),
                    "output_tokens": int(_usage.get("output_tokens") or 0),
                    "thinking": _thinking,
                    "is_final": False,
                })
                for tool_call in msg.tool_calls:
                    tool_name = tool_call.get("name", "unknown")
                    tool_args = tool_call.get("args", {})
                    started_at = time.perf_counter()
                    tool_start_times[tool_name].append(started_at)
                    if debug_tools:
                        print(
                            f"[DEBUG:TOOL_CALL] node={node_name} elapsed_s={elapsed} "
                            f"tool={tool_name} args={json.dumps(tool_args, ensure_ascii=False, default=str)}"
                        )
                    tool_events.append(
                        {
                            "elapsed_s": elapsed,
                            "node": node_name,
                            "type": "tool_call",
                            "tool_name": tool_name,
                            "args": tool_args,
                        }
                    )
                    if on_tool_event:
                        on_tool_event(tool_events)

            elif msg_type == "tool":
                tool_name = getattr(msg, "name", "unknown")
                finished_at = time.perf_counter()
                duration_s = None
                if tool_start_times[tool_name]:
                    started_at = tool_start_times[tool_name].popleft()
                    duration_s = round(finished_at - started_at, 3)

                raw_content = getattr(msg, "content", "")
                preview = raw_content
                if isinstance(raw_content, str) and len(raw_content) > 1200:
                    preview = raw_content[:1200] + "..."

                tool_events.append(
                    {
                        "elapsed_s": elapsed,
                        "node": node_name,
                        "type": "tool_result",
                        "tool_name": tool_name,
                        "duration_s": duration_s,
                        "content_preview": preview,
                    }
                )
                llm_call_start = time.perf_counter()
                if on_tool_event:
                    on_tool_event(tool_events)
                if debug_tools:
                    print(
                        f"[DEBUG:TOOL_RESULT] node={node_name} elapsed_s={elapsed} "
                        f"tool={tool_name} duration_s={duration_s}"
                    )

            elif msg_type == "ai":
                _now = time.perf_counter()
                _usage = getattr(msg, "usage_metadata", None) or {}
                _thinking, text = _extract_llm_thinking(getattr(msg, "content", ""))

                tool_events.append({
                    "elapsed_s": elapsed,
                    "node": node_name,
                    "type": "llm_call",
                    "start_s": round(llm_call_start - run_start, 3),
                    "duration_s": round(_now - llm_call_start, 3),
                    "input_tokens": int(_usage.get("input_tokens") or 0),
                    "output_tokens": int(_usage.get("output_tokens") or 0),
                    "thinking": _thinking,
                    "is_final": True,
                })

                if text:
                    final_answer = text

    return final_answer, tool_events


def log_trajectory(question: str, answer: str, tool_events: list[dict], total_latency_s: float):
    """Append one query trajectory to eval_log.jsonl for offline analysis."""
    tool_calls = [e for e in tool_events if e["type"] == "tool_call"]
    record = {
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "question": question,
        "answer": answer,
        "total_latency_s": round(total_latency_s, 3),
        "step_count": len(tool_calls),
        "tool_sequence": [e["tool_name"] for e in tool_calls],
        "tool_durations": {
            e["tool_name"]: e.get("duration_s")
            for e in tool_events
            if e["type"] == "tool_result"
        },
    }
    log_path = os.path.join(os.path.dirname(__file__), "eval_log.jsonl")
    with open(log_path, "a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")


# ── Eval helpers (ported from eval_run.py) ────────────────────────────────────

def _eval_normalize(text: str) -> str:
    text = text.lower()
    text = re.sub(r'\bpct\.', '%', text)
    text = re.sub(r'\s+%', '%', text)
    text = re.sub(r'(\d)[.,](\d{3})(?!\d)', r'\1\2', text)
    # Accept "kilometer/kilometre/kilometers/kilometres" as "km"
    text = re.sub(r'\bkilomet(?:er|re)s?\b', 'km', text)
    return text


_BEHAVIOR_SIGNALS: dict[str, list[str]] = {
    "refuse": ["vil jeg ikke", "hjælper ikke med", "kan ikke hjælpe", "ulovlig", "skatteunddragelse"],
    "clarify": ["kan du oplyse", "hvad mener du", "mere information", "præcisere", "uddybe"],
    "correct_premise": [
        "præmissen er forkert", "nej, det er ikke rigtigt", "ingen formueskat",
        "afskaffet", "det er forkert", "ikke korrekt", "der er ingen",
    ],
    "admit_unknown": ["kan ikke finde", "findes ikke i", "eksisterer ikke", "ingen §", "ingen paragraf"],
}


def _eval_detect_behavior(answer: str) -> str:
    lower = answer.lower()
    for behavior, signals in _BEHAVIOR_SIGNALS.items():
        if any(sig in lower for sig in signals):
            return behavior
    return "answer"


def _eval_score_item(item: dict, answer: str, tool_events: list) -> dict:
    answer_norm = _eval_normalize(answer)
    must_contain = item.get("must_contain") or []
    must_not_contain = item.get("must_not_contain") or []

    mc_details = {term: _eval_normalize(term) in answer_norm for term in must_contain}
    mnc_details = {term: _eval_normalize(term) not in answer_norm for term in must_not_contain}
    mc_pass = all(mc_details.values()) if mc_details else True
    mnc_pass = all(mnc_details.values()) if mnc_details else True

    detected = _eval_detect_behavior(answer)
    behavior_match = detected == item.get("expected_behavior", "answer")

    tool_calls = [e for e in tool_events if e["type"] == "tool_call"]

    expected_legislation = item.get("expected_legislation") or []
    citation_checks = []
    for leg in expected_legislation:
        paragraf = leg.get("paragraf", "")
        lov = leg.get("lov", "")
        found = bool(paragraf) and (f"§ {paragraf}" in answer or f"§{paragraf}" in answer)
        citation_checks.append({"lov": lov, "paragraf": paragraf, "found": found})
    citation_pass = all(c["found"] for c in citation_checks) if citation_checks else True

    return {
        "must_contain_pass": mc_pass,
        "must_contain_details": mc_details,
        "must_not_contain_pass": mnc_pass,
        "must_not_contain_details": mnc_details,
        "expected_behavior": item.get("expected_behavior"),
        "detected_behavior": detected,
        "behavior_match": behavior_match,
        "expected_legislation_check": citation_checks,
        "citation_pass": citation_pass,
        "tool_call_count": len(tool_calls),
        "tool_sequence": [e["tool_name"] for e in tool_calls],
        "overall_pass": mc_pass and mnc_pass and behavior_match and citation_pass,
    }


def _eval_run_case(item: dict, agent_executor) -> dict:
    chat_messages = [{"role": "user", "content": item["question"]}]
    t0 = time.perf_counter()
    try:
        answer, tool_events = stream_agent_answer(agent_executor, chat_messages)
    except Exception as exc:
        answer = f"[ERROR: {exc}]"
        tool_events = []
    latency = round(time.perf_counter() - t0, 3)
    result = {
        "answer": answer,
        "latency_s": latency,
        "scores": _eval_score_item(item, answer, tool_events),
        "tool_events": tool_events,
    }
    st.session_state.eval_results[item["id"]] = result
    _db_save_eval_result(
        _db, item["id"], answer, latency,
        result["scores"], tool_events,
        provider=st.session_state.get("_selected_provider", ""),
    )
    return result


def validate_citations(answer: str, analysis: "Neo4jAnalysis") -> list[dict]:
    """Extract § references from answer and verify each exists in Neo4j."""
    pattern = re.compile(r'§\s*(\d+\s*[A-Za-z]?)', re.UNICODE)
    refs = list(dict.fromkeys(m.group(0).strip() for m in pattern.finditer(answer)))
    if not refs:
        return []

    results = []
    for ref in refs:
        num = re.sub(r'^§\s*', '', ref).strip()
        rows = analysis.run_query(
            "MATCH (s:Section) WHERE s.number = $num RETURN s.number AS number LIMIT 1",
            {"num": num},
        )
        results.append({"citation": ref, "found": len(rows) > 0})
    return results


# ── Architecture / Trace / Eval renderers ─────────────────────────────────────

def _render_mermaid(diagram: str, height: int = 420) -> None:
    # Use mermaid.render() (explicit API) instead of startOnLoad so diagrams
    # in inactive Streamlit tabs initialise correctly when the tab is opened.
    uid = str(abs(hash(diagram)))[:10]
    diagram_json = json.dumps(diagram)
    html = f"""
    <div id="diagram-{uid}" style="font-size:14px;"></div>
    <script src="https://cdn.jsdelivr.net/npm/mermaid@10/dist/mermaid.min.js"></script>
    <script>
        mermaid.initialize({{startOnLoad:false,theme:'default',securityLevel:'loose'}});
        mermaid.render('svg-{uid}', {diagram_json})
            .then(function(r){{
                document.getElementById('diagram-{uid}').innerHTML = r.svg;
            }})
            .catch(function(e){{
                document.getElementById('diagram-{uid}').innerHTML =
                    '<pre style="color:red">' + e + '</pre>';
            }});
    </script>
    """
    components.html(html, height=height, scrolling=True)


def _render_architecture() -> None:
    st.subheader("System Architecture")

    # Three diagrams rendered in ONE iframe with JS-driven tabs.
    # Using separate iframes (one per Streamlit tab) causes Mermaid startOnLoad
    # to miss inactive tabs; the single-iframe pattern avoids this entirely.
    diagrams = [
        ("System Overview", """graph TD
    A["User Browser"] --> B["Streamlit App"]
    B --> C["LangGraph Agent - ReAct Loop"]
    C --> D["LLM - Gemini 2.5 Flash"]
    D --> C
    C --> E["Tool Dispatcher - 13 StructuredTools"]
    E --> F[("Neo4j Aura - Graph Database")]
    E --> G[("Vector Index - text_embeddings_index - 1024 dims")]
    F -.->|Cypher results| E
    G -.->|Top-k hits| E
    H["HuggingFace - intfloat/multilingual-e5-large"] -->|embed query| G
    I["Retsinformation.dk - Danish Tax Law XML"] --> J["Pipeline: crawler -> loader -> vectorize -> index"]
    J --> F
    J -->|embed passages| H
    style A fill:#e8f4f8,stroke:#4C78A8
    style C fill:#fff3cd,stroke:#F58518
    style D fill:#d4edda,stroke:#54A24B
    style F fill:#f8d7da,stroke:#E45756
    style G fill:#f8d7da,stroke:#E45756"""),

        ("Agent ReAct Loop", """stateDiagram-v2
    state "Think: LLM generates next step" as Think
    state "Call Tools: Execute selected tools" as CallTools
    state "Observe: Inject tool results into context" as Observe
    state "Answer: Formulate and stream response" as Answer
    [*] --> Think : User message received
    Think --> CallTools : tool_calls in response
    Think --> Answer : no tool_calls
    CallTools --> Observe : tool results returned
    Observe --> Think : loop back
    Answer --> [*]"""),

        ("Graph Schema", """graph TD
    L["Legislation - title, uri, status"] -->|HAS_PART| P["Part"]
    P -->|HAS_CHAPTER| CH["Chapter"]
    CH -->|HAS_SECTION| S["Section"]
    S -->|HAS_PARAGRAPH| PAR["Paragraph - text"]
    PAR -->|HAS_COMMENTARY| COM["Commentary"]
    COM -->|HAS_CITATION| CIT["Citation"]
    CIT -->|CITES| L2["Legislation"]
    L -->|HAS_SCHEDULE| SCH["Schedule"]
    SCH -->|HAS_SUBPARAGRAPH| SP["ScheduleParagraph"]
    L -->|HAS_EXPLANATORY_NOTES| EN["ExplanatoryNotes"]
    EN --> ENP["ExplanatoryNotesParagraph"]
    L -->|SUPERSEDES| L3["Legislation"]
    L -->|SUPERSEDED_BY| L4["Legislation"]
    T["Text node - embedding float 1024"] -.->|linked to| PAR
    style L fill:#4C78A8,color:#fff
    style P fill:#F58518,color:#fff
    style CH fill:#72B7B2,color:#fff
    style S fill:#E45756,color:#fff
    style PAR fill:#B279A2,color:#fff
    style T fill:#54A24B,color:#fff"""),
    ]

    tab_names_json = json.dumps([n for n, _ in diagrams])
    diags_json = json.dumps([d for _, d in diagrams])

    html = f"""<!DOCTYPE html>
<html><head><meta charset="utf-8">
<style>
  body {{ margin:0; padding:0; font-family:sans-serif; }}
  .tabs {{ display:flex; border-bottom:2px solid #e0e0e0; margin-bottom:12px; background:#fafafa; }}
  .tab-btn {{
    padding:9px 20px; border:none; background:transparent; cursor:pointer;
    font-size:13px; color:#555; border-bottom:3px solid transparent;
    margin-bottom:-2px; transition:color .15s;
  }}
  .tab-btn:hover {{ color:#4C78A8; }}
  .tab-btn.active {{ color:#4C78A8; border-bottom-color:#4C78A8; font-weight:600; }}
  .pane {{ display:none; padding:8px; }}
  .pane.active {{ display:block; }}
  .mermaid svg {{ max-width:100%; height:auto; }}
</style>
</head><body>
<div class="tabs" id="tabs"></div>
<div id="panes"></div>
<script src="https://cdn.jsdelivr.net/npm/mermaid@10/dist/mermaid.min.js"></script>
<script>
var names = {tab_names_json};
var diags = {diags_json};
var tabs = document.getElementById('tabs');
var panes = document.getElementById('panes');
names.forEach(function(n,i){{
  var btn = document.createElement('button');
  btn.className = 'tab-btn' + (i===0?' active':'');
  btn.textContent = n;
  btn.onclick = function(){{
    document.querySelectorAll('.tab-btn').forEach(function(b,j){{b.classList.toggle('active',j===i);}});
    document.querySelectorAll('.pane').forEach(function(p,j){{p.classList.toggle('active',j===i);}});
  }};
  tabs.appendChild(btn);
  var pane = document.createElement('div');
  pane.className = 'pane' + (i===0?' active':'');
  pane.id = 'pane'+i;
  panes.appendChild(pane);
}});
mermaid.initialize({{startOnLoad:false,theme:'default',securityLevel:'loose'}});
diags.forEach(function(d,i){{
  mermaid.render('msvg'+i, d)
    .then(function(r){{ document.getElementById('pane'+i).innerHTML = r.svg; }})
    .catch(function(e){{ document.getElementById('pane'+i).innerHTML =
      '<pre style="color:red;font-size:12px">' + (e.message||e) + '</pre>'; }});
}});
</script>
</body></html>"""
    components.html(html, height=660, scrolling=True)


def _render_trace_waterfall(tool_events: list) -> None:
    rows = []
    call_queue: dict[str, deque] = defaultdict(deque)
    llm_count = 0

    for e in tool_events:
        etype = e.get("type")
        if etype == "llm_call":
            llm_count += 1
            label = f"LLM call {llm_count}" + (" (final)" if e.get("is_final") else "")
            tok_detail = f"in: {e.get('input_tokens', 0):,} | out: {e.get('output_tokens', 0):,} tokens"
            rows.append({
                "step": label,
                "type": "LLM",
                "start": e.get("start_s", 0.0),
                "end": e.get("start_s", 0.0) + e.get("duration_s", 0.0),
                "duration": round(e.get("duration_s", 0.0), 3),
                "detail": tok_detail,
            })
        elif etype == "tool_call":
            call_queue[e["tool_name"]].append(e)
        elif etype == "tool_result":
            name = e["tool_name"]
            call_e = call_queue[name].popleft() if call_queue[name] else {}
            start = call_e.get("elapsed_s", e["elapsed_s"])
            duration = e.get("duration_s") or max(0.0, e["elapsed_s"] - start)
            rows.append({
                "step": name,
                "type": "Tool",
                "start": round(start, 3),
                "end": round(start + duration, 3),
                "duration": round(duration, 3),
                "detail": "",
            })

    if not rows:
        st.caption("No timing data available for this request.")
        return

    df = pd.DataFrame(rows)
    bar_height = max(180, len(rows) * 38 + 60)

    chart = (
        alt.Chart(df)
        .mark_bar(cornerRadiusEnd=4)
        .encode(
            x=alt.X("start:Q", title="Seconds from request start", axis=alt.Axis(format=".1f")),
            x2="end:Q",
            y=alt.Y("step:N", sort=None, title=""),
            color=alt.Color(
                "type:N",
                scale=alt.Scale(domain=["LLM", "Tool"], range=["#4C78A8", "#F58518"]),
                legend=alt.Legend(title="Step type"),
            ),
            tooltip=[
                alt.Tooltip("step:N", title="Step"),
                alt.Tooltip("type:N", title="Type"),
                alt.Tooltip("start:Q", format=".3f", title="Start (s)"),
                alt.Tooltip("duration:Q", format=".3f", title="Duration (s)"),
                alt.Tooltip("detail:N", title="Detail"),
            ],
        )
        .properties(height=bar_height, title="Request execution timeline")
    )
    st.altair_chart(chart, use_container_width=True)


def _render_trace_steps(tool_events: list, key_prefix: str = "trace") -> None:
    paired: list[tuple] = []
    call_queue: dict[str, deque] = defaultdict(deque)

    for e in tool_events:
        etype = e.get("type")
        if etype == "llm_call":
            paired.append(("llm", e, None))
        elif etype == "tool_call":
            call_queue[e["tool_name"]].append(e)
        elif etype == "tool_result":
            name = e["tool_name"]
            call_e = call_queue[name].popleft() if call_queue[name] else {}
            paired.append(("tool", call_e, e))

    llm_idx = 0
    for step_idx, (kind, ev1, ev2) in enumerate(paired):
        if kind == "llm":
            llm_idx += 1
            label = f"🤖 LLM call {llm_idx}" + (" — final answer" if ev1.get("is_final") else "")
            summary = (
                f"{ev1.get('duration_s', 0):.2f}s | "
                f"in: {ev1.get('input_tokens', 0):,} tok | "
                f"out: {ev1.get('output_tokens', 0):,} tok"
            )
            with st.expander(f"{label} — {summary}"):
                c1, c2, c3 = st.columns(3)
                c1.metric("Duration", f"{ev1.get('duration_s', 0):.2f}s")
                c2.metric("Input tokens", f"{ev1.get('input_tokens', 0):,}")
                c3.metric("Output tokens", f"{ev1.get('output_tokens', 0):,}")
                thinking = ev1.get("thinking", "")
                if thinking:
                    st.markdown("**Chain-of-thought reasoning**")
                    st.text_area(
                        "",
                        value=thinking,
                        height=220,
                        disabled=True,
                        key=f"{key_prefix}_thinking_{step_idx}",
                    )
                else:
                    st.caption("No thinking blocks captured for this step.")
        else:
            name = (ev2 or {}).get("tool_name", "unknown")
            duration = (ev2 or {}).get("duration_s", 0) or 0
            args = ev1.get("args", {}) if ev1 else {}
            output = (ev2 or {}).get("content_preview", "")
            with st.expander(f"🔧 {name} — {duration:.2f}s"):
                col_a, col_b = st.columns(2)
                with col_a:
                    st.markdown("**Input (args)**")
                    st.json(args)
                with col_b:
                    st.markdown("**Output**")
                    if output:
                        try:
                            parsed = json.loads(output) if isinstance(output, str) else output
                            st.json(parsed)
                        except Exception:
                            st.text(str(output)[:600])
                    else:
                        st.caption("(empty)")


def _render_request_trace() -> None:
    st.subheader("Request Trace")

    if "traces" not in st.session_state or not st.session_state.traces:
        st.info("No request traced yet. Ask a question in **Chat Interface** first.")
        return

    traces = st.session_state.traces

    if len(traces) > 1:
        idx = st.selectbox(
            "Select request",
            range(len(traces)),
            format_func=lambda i: f"[{i + 1}] {traces[i]['question'][:70]}",
            index=len(traces) - 1,
        )
    else:
        idx = 0

    trace = traces[idx]
    tool_events = trace["tool_events"]

    llm_events = [e for e in tool_events if e["type"] == "llm_call"]
    tool_call_events = [e for e in tool_events if e["type"] == "tool_call"]
    total_input = sum(e.get("input_tokens", 0) for e in llm_events)
    total_output = sum(e.get("output_tokens", 0) for e in llm_events)

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Total latency", f"{trace['total_latency_s']:.2f}s", border=True)
    c2.metric("Tool calls", len(tool_call_events), border=True)
    c3.metric("Input tokens", f"{total_input:,}", border=True)
    c4.metric("Output tokens", f"{total_output:,}", border=True)

    st.markdown("**Question:** " + trace["question"])

    st.markdown("---")
    _render_trace_waterfall(tool_events)
    st.markdown("---")
    st.markdown("**Step detail**")
    _render_trace_steps(tool_events, key_prefix="main_trace")


_DIFF_ICON = {"easy": "🟢", "medium": "🟡", "hard": "🔴"}
_CAT_ICON = {
    "typical": "📋", "temporal": "🕐", "adversarial": "⚔️",
    "edge_case": "🔍", "cross_reference": "🔗", "refusal": "🚫",
    "hallucination_check": "🧠",
}
_BEHAV_ICON = {
    "answer": "💬", "correct_premise": "🔄", "refuse": "🚫",
    "clarify": "❓", "admit_unknown": "🤷",
}


def _render_evaluation(agent_executor) -> None:
    st.subheader("Evaluation Panel")

    gs_path = os.path.join(os.path.dirname(__file__), "eval_golden_set.json")
    try:
        with open(gs_path, encoding="utf-8") as f:
            golden = json.load(f)
        items = golden["items"]
    except Exception as exc:
        st.error(f"Could not load eval_golden_set.json: {exc}")
        return

    if "eval_results" not in st.session_state:
        st.session_state.eval_results = {}
    if "eval_selected_idx" not in st.session_state:
        st.session_state.eval_selected_idx = 0

    results = st.session_state.eval_results
    n_total = len(items)
    n_run = sum(1 for it in items if it["id"] in results)
    n_pass = sum(1 for it in items if results.get(it["id"], {}).get("scores", {}).get("overall_pass"))

    # ── Stats + actions ───────────────────────────────────────────────────────
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Total cases", n_total, border=True)
    c2.metric("Tested", n_run, border=True)
    c3.metric("Passed", n_pass, border=True)
    c4.metric("Pass rate", f"{100 * n_pass // n_run}%" if n_run else "—", border=True)

    col_run, col_clear = st.columns([2, 1])
    with col_run:
        if st.button("▶▶ Run all cases", type="primary"):
            bar = st.progress(0)
            status_ph = st.empty()
            for i, item in enumerate(items):
                status_ph.text(f"Running {item['id']}: {item['question'][:55]}…")
                _eval_run_case(item, agent_executor)
                bar.progress((i + 1) / n_total)
            status_ph.text("Done.")
            st.rerun()
    with col_clear:
        if st.button("Clear results"):
            st.session_state.eval_results = {}
            st.rerun()

    # ── Visual case browser ───────────────────────────────────────────────────
    st.markdown("---")
    st.markdown("**Click a row to inspect the case below.**")

    browser_rows = []
    for it in items:
        res = results.get(it["id"])
        if res:
            sc = res["scores"]
            status = "✅" if sc["overall_pass"] else "❌"
            lat = f"{res['latency_s']:.1f}s"
            mc = "✅" if sc["must_contain_pass"] else "❌"
            beh = "✅" if sc["behavior_match"] else "❌"
            cit = "✅" if sc["citation_pass"] else "❌"
        else:
            status = lat = mc = beh = cit = "⬜"
        browser_rows.append({
            "Pass": status,
            "ID": it["id"],
            "Category": f"{_CAT_ICON.get(it['category'], '📄')} {it['category']}",
            "Difficulty": f"{_DIFF_ICON.get(it['difficulty'], '⚪')} {it['difficulty']}",
            "Question": it["question"][:65] + ("…" if len(it["question"]) > 65 else ""),
            "MC": mc,
            "Behavior": beh,
            "Citations": cit,
            "Latency": lat,
        })

    browser_df = pd.DataFrame(browser_rows)
    try:
        event = st.dataframe(
            browser_df,
            use_container_width=True,
            hide_index=True,
            on_select="rerun",
            selection_mode="single-row",
            key="eval_case_browser",
            column_config={
                "Pass": st.column_config.TextColumn(width="small"),
                "ID": st.column_config.TextColumn(width="small"),
                "Category": st.column_config.TextColumn(width="medium"),
                "Difficulty": st.column_config.TextColumn(width="small"),
                "Question": st.column_config.TextColumn(width="large"),
                "MC": st.column_config.TextColumn("Must contain", width="small"),
                "Behavior": st.column_config.TextColumn(width="small"),
                "Citations": st.column_config.TextColumn(width="small"),
                "Latency": st.column_config.TextColumn(width="small"),
            },
        )
        sel = event.selection.rows
        if sel:
            st.session_state.eval_selected_idx = sel[0]
    except Exception:
        st.dataframe(browser_df, use_container_width=True, hide_index=True)

    case_idx = min(st.session_state.eval_selected_idx, n_total - 1)
    item = items[case_idx]
    item_result = results.get(item["id"])

    # ── Case detail ───────────────────────────────────────────────────────────
    st.markdown("---")

    # Header row: ID + metadata badges
    diff_icon = _DIFF_ICON.get(item["difficulty"], "⚪")
    cat_icon = _CAT_ICON.get(item["category"], "📄")
    behav_icon = _BEHAV_ICON.get(item.get("expected_behavior", "answer"), "💬")
    st.markdown(
        f"### {item['id']} &nbsp; {cat_icon} `{item['category']}` &nbsp; "
        f"{diff_icon} `{item['difficulty']}` &nbsp; "
        f"pillar: `{item['pillar']}` &nbsp; "
        f"expected: {behav_icon} `{item.get('expected_behavior', 'answer')}`"
    )
    st.markdown(f"**{item['question']}**")

    col_def, col_meta = st.columns([3, 2])

    with col_def:
        with st.expander("Expected answer"):
            st.markdown(item.get("expected_answer", "—"))

        st.markdown("**Must contain:**")
        for term in item.get("must_contain", []):
            if item_result:
                found = item_result["scores"]["must_contain_details"].get(term, False)
                icon = "✅" if found else "❌"
            else:
                icon = "⬜"
            st.markdown(f"&nbsp;&nbsp;{icon} `{term}`")

        if item.get("must_not_contain"):
            st.markdown("**Must NOT contain:**")
            for term in item["must_not_contain"]:
                if item_result:
                    ok = item_result["scores"]["must_not_contain_details"].get(term, True)
                    icon = "✅" if ok else "❌"
                else:
                    icon = "⬜"
                st.markdown(f"&nbsp;&nbsp;{icon} `{term}`")

    with col_meta:
        if item.get("expected_legislation"):
            st.markdown("**Expected legislation:**")
            for leg in item["expected_legislation"]:
                if item_result:
                    checks = {
                        f"{c['lov']} § {c['paragraf']}": c["found"]
                        for c in item_result["scores"].get("expected_legislation_check", [])
                    }
                    key = f"{leg['lov']} § {leg['paragraf']}"
                    icon = "✅" if checks.get(key, False) else ("❌" if item_result else "⬜")
                else:
                    icon = "⬜"
                st.markdown(f"&nbsp;&nbsp;{icon} {leg['lov']} § {leg['paragraf']}")

        if item.get("notes"):
            with st.expander("Notes"):
                st.caption(item["notes"])

    # Run button
    if st.button(f"▶ Run {item['id']}", type="secondary"):
        with st.spinner(f"Running {item['id']}…"):
            _eval_run_case(item, agent_executor)
        st.rerun()

    # ── Result (only when available, no repetition of definition) ────────────
    if item_result:
        sc = item_result["scores"]
        overall_icon = "✅ PASS" if sc["overall_pass"] else "❌ FAIL"

        llm_evts = [e for e in item_result["tool_events"] if e["type"] == "llm_call"]
        in_tok = sum(e.get("input_tokens", 0) for e in llm_evts)
        out_tok = sum(e.get("output_tokens", 0) for e in llm_evts)

        r1, r2, r3, r4, r5 = st.columns(5)
        r1.metric("Result", overall_icon, border=True)
        r2.metric("Latency", f"{item_result['latency_s']:.2f}s", border=True)
        r3.metric("Tools", sc["tool_call_count"], border=True)
        r4.metric("In tokens", f"{in_tok:,}", border=True)
        r5.metric("Out tokens", f"{out_tok:,}", border=True)

        # Behavior check (only field not already shown in the must_contain/citation grids above)
        beh_ok = sc["behavior_match"]
        st.markdown(
            f"**Behavior:** {'✅' if beh_ok else '❌'} "
            f"detected `{sc['detected_behavior']}` — expected `{sc['expected_behavior']}`"
        )

        with st.expander("Actual answer"):
            st.markdown(item_result["answer"])

        with st.expander("Tool trace"):
            _render_trace_waterfall(item_result["tool_events"])
            _render_trace_steps(item_result["tool_events"], key_prefix=f"eval_{item['id']}")


def _render_tools(tools: list) -> None:
    st.subheader("Agent Tools")

    # Categorise tools by retrieval strategy — used as a badge
    _VECTOR_TOOLS = {"Legislation_Finder", "Contextual_Text_Retriever", "Citation_Network_Explorer", "Semantic_Search"}
    _CYPHER_TOOLS = {"Read_Only_Cypher", "Text2Cypher_Expert", "Graph_Schema_Navigator"}
    _HYBRID_TOOLS = {"Legislation_Finder"}
    _GRAPH_TOOLS  = {"Supersedes_Network_Explorer", "Superseded_By_Network_Explorer",
                     "Citation_Counts", "Hierarchy_Path_Resolver", "Legislation_By_URI",
                     "Legislation_Title_Resolver"}

    def _strategy_badge(name: str) -> str:
        if name in _HYBRID_TOOLS:   return "🔀 Hybrid"
        if name in _VECTOR_TOOLS:   return "🔍 Vector"
        if name in _CYPHER_TOOLS:   return "🗄️ Cypher"
        if name in _GRAPH_TOOLS:    return "🕸️ Graph"
        return "⚙️ Other"

    def _field_type(annotation) -> str:
        if annotation is None:
            return "any"
        s = str(annotation)
        for rep in [
            ("typing.Optional[", ""), ("]", ""), ("<class '", ""), ("'>", ""),
            ("typing.Union[", ""), (", NoneType", "?"),
        ]:
            s = s.replace(*rep)
        return s

    def _field_constraints(field_info) -> str:
        parts = []
        if field_info.default is not None:
            from pydantic_core import PydanticUndefinedType
            if not isinstance(field_info.default, PydanticUndefinedType):
                parts.append(f"default={field_info.default!r}")
        for m in getattr(field_info, "metadata", []):
            t = type(m).__name__
            v = getattr(m, "ge", getattr(m, "gt", getattr(m, "le", getattr(m, "lt", None))))
            if v is not None:
                parts.append(f"{t.lower()}={v}")
        return ", ".join(parts)

    # Build index
    tool_index = {t.name: t for t in tools}
    tool_names = [t.name for t in tools]

    # Layout: narrow left list + wide detail panel
    col_list, col_detail = st.columns([1, 2])

    with col_list:
        st.markdown("**Select a tool**")
        if "tools_selected" not in st.session_state:
            st.session_state.tools_selected = tool_names[0]
        for name in tool_names:
            badge = _strategy_badge(name)
            label = f"{name.replace('_', ' ')}"
            active = st.session_state.tools_selected == name
            if st.button(
                label,
                key=f"tool_btn_{name}",
                use_container_width=True,
                type="primary" if active else "secondary",
            ):
                st.session_state.tools_selected = name

    with col_detail:
        selected_name = st.session_state.get("tools_selected", tool_names[0])
        tool = tool_index.get(selected_name)
        if tool is None:
            st.info("Select a tool on the left.")
        else:
            badge = _strategy_badge(selected_name)
            st.markdown(f"### {selected_name.replace('_', ' ')} &nbsp; `{badge}`")
            st.markdown("**Description** *(what the LLM sees)*")
            st.info(tool.description)

            # Input schema
            schema = getattr(tool, "args_schema", None)
            if schema is not None and hasattr(schema, "model_fields"):
                st.markdown("**Input schema**")
                rows = []
                for fname, finfo in schema.model_fields.items():
                    from pydantic_core import PydanticUndefinedType
                    required = isinstance(finfo.default, PydanticUndefinedType)
                    rows.append({
                        "Field": fname,
                        "Type": _field_type(finfo.annotation),
                        "Required": "✅" if required else "optional",
                        "Constraints": _field_constraints(finfo),
                        "Description": finfo.description or "",
                    })
                st.dataframe(rows, use_container_width=True, hide_index=True)
            else:
                st.markdown("**Input** — single free-text string (no structured schema)")

            # Underlying function
            fn = getattr(tool, "func", None)
            if fn is not None:
                st.markdown(f"**Python function** — `{fn.__name__}`")


def _render_point_in_time(analysis: "Neo4jAnalysis") -> None:
    st.subheader("Point in Time")
    st.caption("Which version of each law was in force on a given date?")

    cutoff = st.date_input("Select date", value=date.today(), key="pit_date")

    query = """
    MATCH (l:Legislation)
    WHERE l.uri CONTAINS 'eli/lta'
      AND l.coming_into_force IS NOT NULL
      AND date(l.coming_into_force) <= date($cutoff)
    WITH l.title AS title, max(date(l.coming_into_force)) AS latest_cif
    MATCH (l2:Legislation)
    WHERE l2.title = title AND date(l2.coming_into_force) = latest_cif
    RETURN l2.title AS title,
           l2.uri AS uri,
           toString(l2.coming_into_force) AS in_force_from,
           toString(l2.valid_date) AS valid_date,
           l2.status AS status
    ORDER BY l2.title
    """
    try:
        rows = analysis.run_query(query, {"cutoff": str(cutoff)})
    except Exception as exc:
        st.error(f"Query failed: {exc}")
        return

    if not rows:
        st.info("No legislation was in force on that date.")
        return

    # Abbreviation map for display
    _ABBR = {
        "ligningsloven": "LL",
        "kildeskatteloven": "KSL",
        "selskabsskatteloven": "SEL",
        "personskatteloven": "PSL",
        "aktiesparekontoloven": "ASKL",
        "momsloven": "ML",
        "afskrivningsloven": "AL",
        "fondsbeskatningsloven": "FBL",
        "aktieavancebeskatningsloven": "ABL",
        "kursgevinstloven": "KGL",
    }

    def _abbr(title: str) -> str:
        tl = title.lower()
        for key, abbr in _ABBR.items():
            if key in tl:
                return abbr
        return "—"

    display = []
    for r in rows:
        short_uri = r["uri"].split("eli/lta/")[-1] if r["uri"] else r["uri"]
        display.append({
            "Abbreviation": _abbr(r["title"]),
            "Version (ELI)": short_uri,
            "In force from": r["in_force_from"],
            "Valid date": r["valid_date"],
            "Title": r["title"],
        })

    st.metric("Laws in force", len(display))
    st.dataframe(display, use_container_width=True, hide_index=True)


def _render_version_timeline(analysis: "Neo4jAnalysis") -> None:
    st.subheader("Version Timeline")
    st.caption("Timeline of all law versions in the graph, ordered by when each came into force.")

    query = """
    MATCH (l:Legislation)
    WHERE l.uri CONTAINS 'eli/lta'
      AND l.coming_into_force IS NOT NULL
    RETURN l.title AS title,
           l.uri AS uri,
           toString(l.coming_into_force) AS cif,
           toString(l.valid_date) AS valid_date
    ORDER BY l.title, l.coming_into_force
    """
    try:
        rows = analysis.run_query(query, {})
    except Exception as exc:
        st.error(f"Query failed: {exc}")
        return

    if not rows:
        st.info("No data.")
        return

    _ABBR = {
        "ligningsloven": "LL",
        "kildeskatteloven": "KSL",
        "selskabsskatteloven": "SEL",
        "indkomstskat for personer": "PSL",
        "aktiesparekontoloven": "ASKL",
        "merværdiafgift": "ML",
        "afskrivningsloven": "AL",
        "fondsbeskatningsloven": "FBL",
        "aktieavancebeskatningsloven": "ABL",
        "kursgevinstloven": "KGL",
        "ændring af personskatteloven": "LOV482",
    }

    def _abbr(title: str) -> str:
        tl = title.lower()
        for key, abbr in _ABBR.items():
            if key in tl:
                return abbr
        return title[:20]

    # Build swimlane data: for each version, bar goes from cif to next cif (or today)
    from datetime import date as _date
    import pandas as pd

    # Group by title
    by_title: dict = {}
    for r in rows:
        t = r["title"]
        by_title.setdefault(t, []).append(r)

    bar_rows = []
    today_str = str(_date.today())
    for title, versions in by_title.items():
        abbr = _abbr(title)
        for i, v in enumerate(versions):
            end = versions[i + 1]["cif"] if i + 1 < len(versions) else today_str
            short_uri = v["uri"].split("eli/lta/")[-1] if v["uri"] else ""
            bar_rows.append({
                "Law": abbr,
                "Version": short_uri,
                "Start": v["cif"],
                "End": end,
                "Tooltip": f"{abbr} {short_uri}  ({v['cif']} → {end})",
            })

    df = pd.DataFrame(bar_rows)
    df["Start"] = pd.to_datetime(df["Start"])
    df["End"] = pd.to_datetime(df["End"])

    chart = (
        alt.Chart(df)
        .mark_bar(height=18, cornerRadiusEnd=3)
        .encode(
            x=alt.X("Start:T", title="Date"),
            x2=alt.X2("End:T"),
            y=alt.Y("Law:N", sort="-x", title=""),
            color=alt.Color("Law:N", legend=None),
            tooltip=["Law", "Version", "Start", "End"],
        )
        .properties(height=max(180, len(by_title) * 30))
    )

    # Vertical rule for today
    today_rule = (
        alt.Chart(pd.DataFrame([{"today": pd.Timestamp(today_str)}]))
        .mark_rule(color="red", strokeDash=[4, 4], size=1.5)
        .encode(x="today:T", tooltip=alt.value("Today"))
    )

    st.altair_chart((chart + today_rule).interactive(), use_container_width=True)

    with st.expander("Raw data"):
        st.dataframe(
            df[["Law", "Version", "Start", "End"]].assign(
                Start=df["Start"].dt.date, End=df["End"].dt.date
            ),
            use_container_width=True,
            hide_index=True,
        )


st.title("Dansk Skattelovgivning — Graph Agent")
st.caption("GraphRAG over dansk skattelovgivning fra retsinformation.dk.")

with st.sidebar:
    st.subheader("Pick a view")
    selected_use_case = st.radio(
        "Select a view",
        [
            "Chat Interface",
            "Architecture",
            "Tools",
            "Request Trace",
            "Evaluation",
            "Point in Time",
            "Version Timeline",
            "The Complete Graph",
            "Legislation Graph",
            "Parts",
            "Commentaries",
            "Supersedes/Superseded By",
        ],
        index=0,
    )

    # Provider selector — only show providers that have their credentials configured.
    # Each Gemini model gets its own entry with key "gemini:<model-name>".
    _provider_options = []
    if OLLAMA_MODEL:
        _provider_options.append(("ollama", f"Ollama ({OLLAMA_MODEL})"))
    if GOOGLE_API_KEY:
        for _gm in GEMINI_MODELS:
            _provider_options.append((f"gemini:{_gm}", f"Gemini ({_gm})"))
    if OPENAI_API_KEY:
        _provider_options.append(("openai", "OpenAI (gpt-4o-mini)"))

    if len(_provider_options) > 1:
        _default = LLM_PROVIDER or f"gemini:{GEMINI_MODEL}" if GOOGLE_API_KEY else (LLM_PROVIDER or _provider_options[0][0])
        _default_idx = next((i for i, (k, _) in enumerate(_provider_options) if k == _default), 0)
        selected_provider = st.selectbox(
            "LLM",
            options=[k for k, _ in _provider_options],
            format_func={k: label for k, label in _provider_options}.get,
            index=_default_idx,
        )
    elif _provider_options:
        selected_provider = _provider_options[0][0]
    else:
        selected_provider = None

    st.session_state["_selected_provider"] = selected_provider or ""

    use_case_params = {"height": NETWORK_GRAPH_HEIGHT}

    if selected_use_case == "Legislation Graph":
        use_case_params["uri_contains"] = st.text_input(
            "Legislation URI contains",
            value="eli/lta/2024/460",
            key="uc_legislation_uri",
        )
    elif selected_use_case == "Parts":
        use_case_params["uri_contains"] = st.text_input(
            "Legislation URI contains",
            value="eli/lta/2024/460",
            key="uc_part_uri",
        )
        use_case_params["part_order"] = st.number_input(
            "Part order",
            min_value=1,
            step=1,
            value=2,
            key="uc_part_order",
        )
    elif selected_use_case == "Commentaries":
        use_case_params["uri_contains"] = st.text_input(
            "Legislation URI contains",
            value="eli/lta/2023/42",
            key="uc_commentaries_uri",
        )

    st.markdown("---")
    st.caption("Select 'Chat Interface' to open the assistant chat.")


def _render_use_case_graph(
    analysis: Neo4jAnalysis,
    query: str,
    params=None,
    height: int = NETWORK_GRAPH_HEIGHT,
    enlarged_node_ids: Optional[set[Any]] = None,
    enlarged_node_size: int = 40,
):
    colors = {
        "Legislation": "#1f77b4",
        "Part": "#ff7f0e",
        "Chapter": "#2ca02c",
        "Section": "#d62728",
        "Paragraph": "#9467bd",
        "Commentary": "#bcbd22",
        "Citation": "#17becf",
    }
    label_to_property = {
        "Legislation": "title",
        "Part": "title",
        "Chapter": "title",
        "Section": "title",
        "Paragraph": "number",
        "Commentary": "text",
        "Citation": "text",
    }

    results = analysis.run_query_viz(query, params or {})
    VG = from_neo4j(results)
    VG.color_nodes(field="caption", color_space=ColorSpace.DISCRETE, colors=colors)
    analysis.set_caption_by_label(VG, label_to_property)

    if enlarged_node_ids:
        sizes = {}
        for node in getattr(VG, "nodes", []):
            props = getattr(node, "properties", {}) or {}
            node_id_property = props.get("id")
            if node_id_property is not None and str(node_id_property) in enlarged_node_ids:
                sizes[getattr(node, "id")] = enlarged_node_size

        if sizes:
            VG.resize_nodes(sizes=sizes, node_radius_min_max=None)

    generated_html = VG.render(layout=Layout.FORCE_DIRECTED, initial_zoom=1.0)
    html_str = generated_html.data if hasattr(generated_html, "data") else str(generated_html)
    components.html(html_str, height=height, scrolling=True)


def _show_use_case_panel(
    analysis: Neo4jAnalysis,
    selected_use_case: str,
    use_case_params: dict,
    agent_executor=None,
):
    use_case_descriptions = {
        "Chat Interface":
            "**Ask natural-language questions to explore legislation structure, citations, supersession links, and point-in-time context.**",
        "Architecture":
            "**Three diagrams: system overview, ReAct agent loop, and graph schema.**",
        "Tools":
            "**Inspect all 13 agent tools: description, input schema, field constraints, and retrieval strategy.**",
        "Request Trace":
            "**Per-request waterfall timeline with LLM token counts, tool durations, and chain-of-thought reasoning.**",
        "Evaluation":
            "**Run golden-set test cases from the browser and inspect detailed pass/fail results.**",
        "Point in Time":
            "**Select a date and see which version of each law was in force on that date.**",
        "Version Timeline":
            "**Swimlane chart showing the full version history of all laws in the graph.**",
        "The Complete Graph":
            "**Visualize the full graph schema, including available node types and how they are connected.**",
        "Legislation Graph":
            "**Inspect a single legislation hierarchy from legislation to parts, chapters, and sections.**",
        "Parts":
            "**Focus on a specific part within a legislation and trace its chapters, sections, paragraphs, and commentary links.**",
        "Commentaries":
            "**Explore commentary and citation chains that connect interpretive notes back to legislation.**",
        "Supersedes/Superseded By":
            "**Analyze legislation-to-legislation replacement relationships across supersedes and superseded-by links.**",
    }

    if selected_use_case not in ("Architecture", "Request Trace", "Evaluation", "Tools",
                                 "Point in Time", "Version Timeline"):
        st.subheader(selected_use_case)
    st.caption(use_case_descriptions.get(selected_use_case, ""))

    if selected_use_case == "Chat Interface":
        return

    if selected_use_case == "Architecture":
        _render_architecture()
        return

    if selected_use_case == "Tools":
        _render_tools(use_case_params.get("agent_tools", []))
        return

    if selected_use_case == "Request Trace":
        _render_request_trace()
        return

    if selected_use_case == "Evaluation":
        _render_evaluation(agent_executor)
        return

    if selected_use_case == "Point in Time":
        _render_point_in_time(analysis)
        return

    if selected_use_case == "Version Timeline":
        _render_version_timeline(analysis)
        return

    if selected_use_case == "The Complete Graph":
        query = """
        CALL db.schema.visualization()
        YIELD nodes, relationships
        // Filter by the virtual node's label instead of its name property
        WITH [n IN nodes WHERE labels(n)[0] <> 'Text'] AS filtered_nodes, relationships
        WITH filtered_nodes, 
            [r IN relationships WHERE startNode(r) IN filtered_nodes AND endNode(r) IN filtered_nodes] AS filtered_rels
        RETURN filtered_nodes AS nodes, filtered_rels AS relationships
        """
        _render_use_case_graph(analysis, query, height=NETWORK_GRAPH_HEIGHT)
    elif selected_use_case == "Legislation Graph":
        query = """
        MATCH p=(l:Legislation)-[:HAS_PART]->(:Part)-[:HAS_CHAPTER]->(:Chapter)-[:HAS_SECTION]->(:Section)
        WHERE l.uri CONTAINS $uri_contains
        RETURN p
        """
        _render_use_case_graph(
            analysis,
            query,
            params={"uri_contains": use_case_params.get("uri_contains", "eli/lta/2024/460")},
            height=NETWORK_GRAPH_HEIGHT,
        )
    elif selected_use_case == "Parts":
        query = """
        MATCH p=(l:Legislation)-[:HAS_PART]->(part:Part)-[:HAS_CHAPTER]->(:Chapter)-[:HAS_SECTION]->(section:Section)-[:HAS_PARAGRAPH]->(para:Paragraph)-[:HAS_COMMENTARY]->(comm:Commentary)
        WHERE l.uri CONTAINS $uri_contains AND part.order = $part_order
        RETURN p
        """
        _render_use_case_graph(
            analysis,
            query,
            params={
                "uri_contains": use_case_params.get("uri_contains", "eli/lta/2024/460"),
                "part_order": int(use_case_params.get("part_order", 2)),
            },
            height=NETWORK_GRAPH_HEIGHT,
        )
    elif selected_use_case == "Commentaries":
        query = """
        MATCH p=(:Commentary)-[:HAS_CITATION]->(:Citation)-[:CITES]->(l:Legislation)
        WHERE l.uri CONTAINS $uri_contains
        RETURN p
        """
        _render_use_case_graph(
            analysis,
            query,
            params={"uri_contains": use_case_params.get("uri_contains", "eli/lta/2023/42")},
            height=NETWORK_GRAPH_HEIGHT,
        )
    elif selected_use_case == "Supersedes/Superseded By":
        query = """
        MATCH p=(:Legislation)-[:SUPERSEDED_BY|SUPERSEDES]-(:Legislation)
        RETURN p
        """
        _render_use_case_graph(analysis, query, height=NETWORK_GRAPH_HEIGHT)


def _render_global_metrics(analysis: Neo4jAnalysis):
    query = """
    CALL() {
        MATCH (l:Legislation)
        RETURN count(l) AS legislation_acts
    }
    CALL() {
        MATCH (:Paragraph)
        RETURN count(*) AS paragraphs
    }
    CALL() {
        MATCH (:Citation)
        RETURN count(*) AS citations
    }
    RETURN legislation_acts, paragraphs, citations
    """

    try:
        metrics_df = analysis.run_query_df(query)
        metrics = metrics_df.iloc[0].to_dict() if not metrics_df.empty else {}
        yearly_df = analysis.run_query_df(
            """
            MATCH (l:Legislation)
            WITH substring(l.uri, size(l.uri) - 4, 4) AS uri_year
            WHERE uri_year =~ '[0-9]{4}'
            RETURN uri_year AS enactment_year, count(*) AS legislations
            ORDER BY enactment_year
            """
        )
    except Exception as exc:
        st.warning(f"Unable to load dashboard metrics: {exc}")
        return

    sparkline_values = (
        yearly_df["legislations"].fillna(0).astype(int).tolist() if not yearly_df.empty else []
    )

    earliest_year = yearly_df["enactment_year"].min() if not yearly_df.empty else "N/A"
    latest_year = yearly_df["enactment_year"].max() if not yearly_df.empty else "N/A"
    if earliest_year == "N/A" and latest_year == "N/A":
        year_range = "N/A"
    elif earliest_year == latest_year:
        year_range = earliest_year
    else:
        year_range = f"{earliest_year} - {latest_year}"

    c1, c2, c3, c4, c5 = st.columns(5)
    c1.metric("Legislation acts", f"{int(metrics.get('legislation_acts', 0)):,}", border=True)
    c2.metric("Paragraphs", f"{int(metrics.get('paragraphs', 0)):,}", border=True)
    c3.metric("Citations", f"{int(metrics.get('citations', 0)):,}", border=True)
    c4.metric("Legislation year range", year_range, border=True)

    with c5:
        with st.container(border=True):
            st.caption("Legislations / Year")
            if sparkline_values:
                sparkline_df = yearly_df.copy()
                sparkline_df["enactment_year"] = pd.to_datetime(
                    sparkline_df["enactment_year"].astype(str) + "-01-01", errors="coerce"
                )
                sparkline_df = sparkline_df.dropna(subset=["enactment_year"])

                sparkline_chart = (
                    alt.Chart(sparkline_df)
                    .mark_line(strokeWidth=2)
                    .encode(
                        x=alt.X("enactment_year:T", axis=None),
                        y=alt.Y("legislations:Q", axis=None),
                        tooltip=[
                            alt.Tooltip("year(enactment_year):T", title="Year"),
                            alt.Tooltip("legislations:Q", title="Legislations"),
                        ],
                    )
                    .properties(height=70)
                )
                st.altair_chart(sparkline_chart, width="stretch")
            else:
                st.caption("No data")

    st.markdown("---")

try:
    analysis, agent_executor, agent_tools = build_runtime(provider=selected_provider)
    if not analysis.verify_connection():
        st.error("Neo4j connection test failed.")
        st.stop()
except Exception as e:
    st.error(f"Initialization failed: {e}")
    st.stop()

_render_global_metrics(analysis)
use_case_params["agent_tools"] = agent_tools
_show_use_case_panel(analysis, selected_use_case, use_case_params, agent_executor=agent_executor)

_db = _init_db()
if "traces" not in st.session_state:
    st.session_state.traces = _db_load_traces(_db)
if "eval_results" not in st.session_state:
    st.session_state.eval_results = _db_load_eval_results(_db)

if selected_use_case == "Chat Interface":
    if "messages" not in st.session_state:
        st.session_state.messages = [
            {
                "role": "assistant",
                "content": "Stil mig spørgsmål om dansk skattelovgivning — regler, paragraffer, citationer eller sammenhænge mellem love som Personskatteloven, Ligningsloven, Momsloven og mere.",
            }
        ]

    for message in st.session_state.messages:
        with st.chat_message(message["role"]):
            st.markdown(message["content"])
            if message.get("tool_events"):
                with st.expander("Tool trace", expanded=False):
                    st.json(message["tool_events"])

    prompt = st.chat_input("Ask a legal graph question...", key="main_chat_input")

    if prompt:
        st.session_state.messages.append({"role": "user", "content": prompt})
        with st.chat_message("user"):
            st.markdown(prompt)

        with st.chat_message("assistant"):
            with st.expander("Tool trace (live)", expanded=False):
                live_trace_placeholder = st.empty()

            def _update_live_trace(events: list[dict]):
                live_trace_placeholder.json(events)

            t0 = time.perf_counter()
            with st.status("Running agent...", expanded=False):
                answer, tool_events = stream_agent_answer(
                    agent_executor,
                    st.session_state.messages,
                    on_tool_event=_update_live_trace,
                )
            total_latency_s = round(time.perf_counter() - t0, 3)

            if not answer:
                answer = "No response generated. Try a more specific prompt (Act title, URI, or topic)."

            st.markdown(answer)

            citation_results = validate_citations(answer, analysis)
            if citation_results:
                with st.expander("Citation validation", expanded=False):
                    for r in citation_results:
                        icon = "✅" if r["found"] else "❌"
                        st.markdown(f"{icon} `{r['citation']}` — {'found in graph' if r['found'] else 'NOT found in graph'}")

            log_trajectory(prompt, answer, tool_events, total_latency_s)

            _db_save_trace(_db, prompt, answer, total_latency_s, tool_events,
                           provider=selected_provider or "")
            st.session_state.traces.append({
                "question": prompt,
                "answer": answer,
                "total_latency_s": total_latency_s,
                "tool_events": tool_events,
            })
            if len(st.session_state.traces) > _TRACES_MAX:
                st.session_state.traces.pop(0)

        st.session_state.messages.append(
            {
                "role": "assistant",
                "content": answer,
                "tool_events": tool_events,
            }
        )