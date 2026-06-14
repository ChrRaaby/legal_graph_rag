#!/usr/bin/env python3
"""Build §-level CITES edges from statutory cross-references in the law text.

Parses references like "ligningslovens § 16 A" (cross-law) and "jf. § 13, stk. 2"
(same-law) out of every current-version Paragraph, resolves them to a real Section
node in the (current version of the) cited law, and creates
(:Section)-[:CITES]->(:Section). High-confidence only: an edge is created only when
BOTH endpoints resolve to real nodes — refs to laws not loaded in the graph
(boafgiftsloven, pensionsbeskatningsloven, …) are correctly skipped.

Surfaced later by retrieval as 'krydshenvisninger' so the agent can follow
multi-law chains (gs-024: LL § 2 -> LL § 16 A -> PSL § 8 a; gs-025).

Usage:
  .venv/bin/python3 build_cites_edges.py            # DRY RUN (stats + samples, no writes)
  .venv/bin/python3 build_cites_edges.py --commit   # create edges (idempotent MERGE)

See whitepapers/CITES_design.md.
"""
import re
import sys
from collections import Counter, defaultdict
from dotenv import dotenv_values
from neo4j import GraphDatabase

# Explicit aliases beyond simple stem-prefix matching (text form -> graph description).
ALIASES = {
    "merværdiafgiftslov": "Momsloven",
    "momslov": "Momsloven",
    # common abbreviations seen in statutory text
    "ll": "Ligningsloven",
    "psl": "Personskatteloven",
    "sel": "Selskabsskatteloven",
    "ksl": "Kildeskatteloven",
    "ml": "Momsloven",
    "abl": "Aktieavancebeskatningsloven",
    "kgl": "Kursgevinstloven",
    "al": "Afskrivningsloven",
    "fbl": "Fondsbeskatningsloven",
    "askl": "Aktiesparekontoloven",
}

# A § reference: number + optional single section-letter (graph stores "13 A", "9 C").
# Require the letter to be a standalone token (space/punct/end after it) so we don't
# grab the first letter of the next word ("§ 1 Efter" -> "1", not "1 E").
SEC_RE = re.compile(r"§+\s*(\d+)\s*([A-Za-z])?(?![a-zæøåA-ZÆØÅ0-9])")
# A law name in GENITIVE form ("ligningslovens", "kursgevinstlovens") — introduces
# the § that follows it. Require the genitive ending so bare "lov"/"denne lov"
# don't match. The § itself is excluded from the scanned gap, so we must NOT
# require a trailing § here.
LAW_ANY = re.compile(r"([a-zæøå]{4,}lov)(?:en|ens|s)\b", re.IGNORECASE)
# Connector right before a § (examined in the gap since the previous §):
#  - LIST ("og", ",", "eller", "samt"): the § continues the current law context.
#  - REF  ("jf.", "efter", "i", "stk." …): a genuine same-law reference.
LIST_CONNECTOR = re.compile(r"(?:og|eller|samt|,|;)\s*$", re.IGNORECASE)
REF_CONNECTOR = re.compile(r"(?:jf\.?|efter|dog|se|i|af|nr\.|stk\.|pkt\.|litra)\s*$", re.IGNORECASE)


def normalize_sec(num: str, letter) -> str:
    s = num.strip()
    if letter:
        s += " " + letter.upper()
    return s


def main(commit: bool) -> None:
    cfg = dotenv_values(".env")
    drv = GraphDatabase.driver(cfg["NEO4J_URI"], auth=(cfg["NEO4J_USER"], cfg["NEO4J_PASSWORD"]))
    db = cfg.get("NEO4J_DATABASE", "neo4j")

    def q(c, **p):
        with drv.session(database=db) as s:
            return [r.data() for r in s.run(c, **p)]

    # current-version law descriptions
    descs = [r["d"] for r in q(
        "MATCH (l:Legislation) WHERE coalesce(l.is_current,true) AND l.description IS NOT NULL "
        "AND l.description ENDS WITH 'loven' RETURN DISTINCT l.description AS d")]
    desc_lower = {d.lower(): d for d in descs}

    # section-number -> elementId, per current law
    sec_id = defaultdict(dict)  # desc -> {NUM -> elementId}
    for d in descs:
        for r in q("""MATCH (l:Legislation)-[:HAS_PART|HAS_CHAPTER|HAS_SECTION*1..6]->(s:Section)
                      WHERE l.description=$d AND coalesce(l.is_current,true)
                      RETURN toUpper(trim(s.number)) AS num, elementId(s) AS id""", d=d):
            if r["num"]:
                sec_id[d].setdefault(r["num"], r["id"])

    def resolve_law(stem: str):
        s = stem.lower()
        if s in ALIASES:
            return ALIASES[s]
        for low, d in desc_lower.items():
            if low.startswith(s) or s in low:
                return d
        return None

    # walk current-version paragraphs, parse refs, resolve
    paras = q("""MATCH (l:Legislation)-[:HAS_PART|HAS_CHAPTER|HAS_SECTION*1..6]->(sec:Section)-[:HAS_PARAGRAPH]->(par:Paragraph)
                 WHERE coalesce(l.is_current,true) AND par.text IS NOT NULL AND par.text CONTAINS '§'
                 RETURN l.description AS lov, toUpper(trim(sec.number)) AS sec_num,
                        elementId(sec) AS sec_id, par.text AS text""")

    edges = []          # (src_id, tgt_id, src_label, tgt_label, ref_phrase)
    seen = set()
    n_refs = n_crosslaw = n_resolved = 0
    skip_outof_graph = Counter()
    for p in paras:
        src_lov, src_id_ = p["lov"], p["sec_id"]
        if not src_lov or src_lov not in sec_id:
            continue
        text = p["text"]
        last_end = 0
        ctx_law = None          # law context from the most recent explicit law name
        ctx_in_graph = True     # whether ctx_law resolved to a loaded law
        for m in SEC_RE.finditer(text):
            n_refs += 1
            sec = normalize_sec(m.group(1), m.group(2))
            gap = text[last_end:m.start()]   # text since the previous § (or start)
            last_end = m.end()
            law_hits = list(LAW_ANY.finditer(gap))
            if law_hits:
                # an explicit law name introduces this § — set the context
                n_crosslaw += 1
                ctx_law = resolve_law(law_hits[-1].group(1))
                ctx_in_graph = ctx_law is not None
                if not ctx_in_graph:
                    skip_outof_graph[law_hits[-1].group(1).lower()] += 1
                    continue
                tgt_lov = ctx_law
            else:
                tail = gap[-14:]
                if LIST_CONNECTOR.search(tail) and ctx_law is not None:
                    # list continuation — inherit the current law context
                    if not ctx_in_graph:
                        continue
                    tgt_lov = ctx_law
                elif REF_CONNECTOR.search(tail):
                    tgt_lov = src_lov       # genuine same-law reference
                    ctx_law, ctx_in_graph = None, True
                else:
                    continue
            tgt_id_ = sec_id.get(tgt_lov, {}).get(sec)
            if not tgt_id_ or tgt_id_ == src_id_:
                continue
            n_resolved += 1
            key = (src_id_, tgt_id_)
            if key in seen:
                continue
            seen.add(key)
            edges.append((src_id_, tgt_id_, f"{src_lov} § {p['sec_num']}", f"{tgt_lov} § {sec}",
                          re.sub(r"\s+", " ", text[max(0, m.start()-25):m.end()+5]).strip()))

    def law_of(label):  # "Personskatteloven § 3" -> "Personskatteloven"
        return label.rsplit(" § ", 1)[0]
    crosslaw = [e for e in edges if law_of(e[2]) != law_of(e[3])]
    print(f"paragraphs scanned: {len(paras)}")
    print(f"§-refs parsed: {n_refs}")
    print(f"distinct CITES edges: {len(edges)}  (true cross-law: {len(crosslaw)}, same-law: {len(edges)-len(crosslaw)})")
    print(f"\ntop skipped out-of-graph laws (correctly not edged): {skip_outof_graph.most_common(6)}")
    print("\n=== sample CROSS-LAW edges (the multi-law-chain payoff — verify) ===")
    for e in crosslaw[:16]:
        print(f"  {e[2]:30} -CITES-> {e[3]:32}  «…{e[4]}…»")

    if commit:
        with drv.session(database=db) as s:
            s.run("""UNWIND $rows AS r
                     MATCH (a) WHERE elementId(a)=r.src
                     MATCH (b) WHERE elementId(b)=r.tgt
                     MERGE (a)-[c:CITES]->(b)
                     SET c.via = r.ref""",
                  rows=[{"src": e[0], "tgt": e[1], "ref": e[4]} for e in edges]).consume()
        total = q("MATCH ()-[c:CITES]->() RETURN count(c) AS n")[0]["n"]
        print(f"\nCOMMITTED. CITES edges in graph: {total}")
    else:
        print("\n(DRY RUN — no edges written. Re-run with --commit to create them.)")
    drv.close()


if __name__ == "__main__":
    main(commit="--commit" in sys.argv)
