import os
import json
import sqlite3
import subprocess
from pathlib import Path
from google.cloud import firestore
from google.oauth2.credentials import Credentials

PROJECT_ID = "gen-lang-client-0167283966"
DB_PATH = "observability.db"

def migrate():
    print("Fetching access token via gcloud...")
    token = subprocess.check_output(["gcloud", "auth", "print-access-token"]).decode().strip()
    creds = Credentials(token)

    # 1. Firestore Migration
    print(f"Connecting to Firestore for project {PROJECT_ID}...")
    db = firestore.Client(project=PROJECT_ID, credentials=creds)
    
    if os.path.exists(DB_PATH):
        print(f"Reading SQLite database from {DB_PATH}...")
        con = sqlite3.connect(DB_PATH)
        con.row_factory = sqlite3.Row
        
        # Migrate mr_runs
        print("Migrating mr_runs...")
        runs = con.execute("SELECT * FROM mr_runs").fetchall()
        for row in runs:
            doc_ref = db.collection("mr_runs").document(row["id"])
            doc_ref.set({
                "ts": row["ts"],
                "events": row["events"]
            })
        print(f"Migrated {len(runs)} rows to mr_runs.")

        # Migrate mr_feedback
        print("Migrating mr_feedback...")
        feedback = con.execute("SELECT * FROM mr_feedback").fetchall()
        for row in feedback:
            db.collection("mr_feedback").add({
                "run_id": row["run_id"],
                "ts": row["ts"],
                "verdict": row["verdict"],
                "comment": row["comment"]
            })
        print(f"Migrated {len(feedback)} rows to mr_feedback.")
        
        con.close()
    else:
        print(f"No {DB_PATH} found locally, skipping SQLite migration.")

if __name__ == "__main__":
    migrate()
