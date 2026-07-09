"""Embed unembedded Text nodes (D1+; recreation of the wiped /tmp/vectorize_danish.py).

Auto-detects Text nodes lacking `text_embedding` and embeds them with
intfloat/multilingual-e5-large (1024 dims, CUDA if available, `passage: `
prefix per the model's convention, L2-normalized — verified identical to the
existing 9,246 embeddings' norm and to app.py's query-side
`normalize_embeddings: True`). Idempotent: re-running embeds only what's missing.

Usage:  OMP_NUM_THREADS=1 .venv/bin/python3 vectorize_danish.py [--batch 32]
"""
import argparse
import os
import sys

REPO = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, REPO)
os.chdir(REPO)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--batch", type=int, default=32)
    args = ap.parse_args()

    from dotenv import load_dotenv
    load_dotenv(os.path.join(REPO, ".env"))
    from neo4j_analysis import Neo4jAnalysis
    a = Neo4jAnalysis(os.getenv("NEO4J_URI"), os.getenv("NEO4J_USER"),
                      os.getenv("NEO4J_PASSWORD"), os.getenv("NEO4J_DATABASE", "neo4j"))

    # Labeling pass: newly loaded structural nodes (Paragraph/Section/Chapter)
    # carrying text get the :Text label the vector index reads. Commentary is
    # deliberately EXCLUDED: 2,099 pre-existing commentaries are unlabeled by
    # the original pipeline's choice, and labeling+embedding them would change
    # the vector-pool composition — an unmeasured, agent-visible retrieval
    # change (backlog ground rule 6).
    labeled = a.run_query(
        "MATCH (n) WHERE (n:Paragraph OR n:Section OR n:Chapter) "
        "AND n.text IS NOT NULL AND size(n.text) > 0 AND NOT n:Text "
        "SET n:Text RETURN count(n) AS n"
    )[0]["n"]
    print(f"labeled {labeled} new structural node(s) as :Text")

    todo = a.run_query(
        "MATCH (t:Text) WHERE t.text_embedding IS NULL AND t.text IS NOT NULL "
        "RETURN elementId(t) AS id, t.text AS text"
    )
    total = a.run_query("MATCH (t:Text) RETURN count(t) AS n")[0]["n"]
    print(f"Text nodes: {total} total, {len(todo)} unembedded")
    if not todo:
        return 0

    import torch
    from sentence_transformers import SentenceTransformer
    device = "cuda" if torch.cuda.is_available() else "cpu"
    print(f"loading intfloat/multilingual-e5-large on {device}…")
    model = SentenceTransformer(
        "intfloat/multilingual-e5-large",
        cache_folder=os.path.join(REPO, "..", "models"),
        device=device,
    )

    done = 0
    for i in range(0, len(todo), args.batch):
        chunk = todo[i:i + args.batch]
        vecs = model.encode(
            ["passage: " + r["text"] for r in chunk],
            normalize_embeddings=True,
            show_progress_bar=False,
        )
        a.run_query(
            """
            UNWIND $rows AS row
            MATCH (t:Text) WHERE elementId(t) = row.id
            CALL db.create.setNodeVectorProperty(t, 'text_embedding', row.vec)
            """,
            {"rows": [{"id": r["id"], "vec": v.tolist()} for r, v in zip(chunk, vecs)]},
        )
        done += len(chunk)
        print(f"  embedded {done}/{len(todo)}", flush=True)

    left = a.run_query(
        "MATCH (t:Text) WHERE t.text_embedding IS NULL AND t.text IS NOT NULL RETURN count(t) AS n"
    )[0]["n"]
    print(f"done — remaining unembedded: {left}")
    return 0 if left == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
