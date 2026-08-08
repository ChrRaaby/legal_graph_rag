"""Verify PBL § 16's content against the graph before it becomes ground truth.

The user approved the gs-039 rework, but the values came from an agent answer.
D3's authoring rule is "verify every anchor against graph/corpus" — so read § 16
straight out of Neo4j and confirm the grundbeløb and the regulated amounts.
"""
import os
import re
import sys

from dotenv import load_dotenv
from neo4j import GraphDatabase

load_dotenv(".env")
drv = GraphDatabase.driver(os.environ["NEO4J_URI"],
                           auth=(os.environ["NEO4J_USER"], os.environ["NEO4J_PASSWORD"]))
db = os.getenv("NEO4J_DATABASE", "neo4j")

Q_SECTION = """
MATCH (l:Legislation)-[:HAS_PART|HAS_CHAPTER|HAS_SECTION*1..6]->(s:Section)
WHERE toLower(l.title) CONTAINS 'pensionsbeskatning' AND s.number = $num
OPTIONAL MATCH (s)-[:HAS_PARAGRAPH]->(p:Paragraph)
RETURN l.title AS lov, s.number AS sec, s.title AS sectitle,
       collect(DISTINCT p.text)[0..8] AS stk
"""

Q_REG = """
MATCH (n)
WHERE n.text IS NOT NULL AND toLower(n.text) CONTAINS 'pensionsbeskatningslovens § 16'
RETURN labels(n) AS lbl, substring(n.text, 0, 700) AS txt
LIMIT 6
"""

with drv.session(database=db) as s:
    print("=== PBL § 16 in the graph ===")
    rows = list(s.run(Q_SECTION, num="16"))
    if not rows:
        print("  NOT FOUND — the rework cannot proceed on these values")
        sys.exit(1)
    for r in rows:
        print(f"  lov: {r['lov'][:70]}")
        print(f"  § {r['sec']}  {r['sectitle'] or ''}")
        for i, t in enumerate(r["stk"] or [], 1):
            if t:
                print(f"    stk-tekst {i}: {t[:300].strip()}")
    print()
    print("=== reguleringstabel rows mentioning PBL § 16 ===")
    for r in s.run(Q_REG):
        print(f"  {r['lbl']}: {r['txt'][:320].strip()}")

    print()
    print("=== amounts present anywhere in PBL § 16 texts ===")
    blob = " ".join(t or "" for r in rows for t in (r["stk"] or []))
    amounts = sorted(set(re.findall(r"\b\d{1,3}\.\d{3}\b", blob)))
    print("  ", amounts or "(none)")
drv.close()
