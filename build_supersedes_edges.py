#!/usr/bin/env python3
"""Build SUPERSEDES / SUPERSEDED_BY edges between Legislation versions, and set an
explicit `is_current` flag on every Legislation node.

The graph holds multiple consolidated versions of the same law (Ligningsloven
has 4, PSL/SEL/KSL have 2) with no relationship linking them and no reliable
"current" marker — `status` is stale (SEL 2022/1241 is the newest yet tagged
Historic). This entangles old and new text in retrieval (e.g. topskat 15% from
the 2019 PSL vs mellemskat 7,5% from the 2024 reform), which is both a
correctness bug and a determinism bug. We order each law's versions by
`coming_into_force` and link newer -> older.

Idempotent (MERGE). Re-run after any graph rebuild.

Usage: .venv/bin/python3 build_supersedes_edges.py
"""
from dotenv import dotenv_values
from neo4j import GraphDatabase


def main() -> None:
    cfg = dotenv_values(".env")
    drv = GraphDatabase.driver(cfg["NEO4J_URI"], auth=(cfg["NEO4J_USER"], cfg["NEO4J_PASSWORD"]))
    db = cfg.get("NEO4J_DATABASE", "neo4j")

    with drv.session(database=db) as s:
        # 1) Link consecutive versions of each consolidated law (ordered by
        #    coming_into_force). Skip the reguleringstabel and amendment acts
        #    (description IS NULL) — those are not versions of one law.
        res = s.run(
            """
            MATCH (l:Legislation)
            WHERE l.description IS NOT NULL
              AND l.coming_into_force IS NOT NULL
              AND NOT l.description STARTS WITH 'Reguleringstabel'
            WITH l.description AS d, l ORDER BY l.coming_into_force
            WITH d, collect(l) AS versions
            WHERE size(versions) > 1
            UNWIND range(0, size(versions) - 2) AS i
            WITH versions[i] AS older, versions[i + 1] AS newer
            MERGE (newer)-[:SUPERSEDES]->(older)
            MERGE (older)-[:SUPERSEDED_BY]->(newer)
            RETURN newer.description AS lov,
                   split(older.uri, '/eli/lta/')[-1] AS older_uri,
                   split(newer.uri, '/eli/lta/')[-1] AS newer_uri
            ORDER BY lov, newer_uri
            """
        )
        edges = [r.data() for r in res]

        # 2) Mark the current version of every law: a Legislation node is current
        #    iff nothing supersedes it (no outgoing SUPERSEDED_BY). Single-version
        #    laws have no edges -> current. Used by retrieval to prefer in-force text.
        s.run(
            """
            MATCH (l:Legislation)
            OPTIONAL MATCH (l)-[sb:SUPERSEDED_BY]->()
            WITH l, count(sb) AS newer
            SET l.is_current = (newer = 0)
            """
        ).consume()

        current = s.run(
            "MATCH (l:Legislation) WHERE l.is_current RETURN count(l) AS n"
        ).single()["n"]
        total = s.run("MATCH (l:Legislation) RETURN count(l) AS n").single()["n"]

    print(f"Created/merged {len(edges)} SUPERSEDES edge(s):")
    for e in edges:
        print(f"  {e['lov']}: {e['newer_uri']} SUPERSEDES {e['older_uri']}")
    print(f"\nis_current set on all {total} Legislation nodes ({current} current, {total - current} historic).")
    drv.close()


if __name__ == "__main__":
    main()
