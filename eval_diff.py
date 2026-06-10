#!/usr/bin/env python3
"""Diff two eval_run.py output files per item. Usage: eval_diff.py before.jsonl after.jsonl"""
import json, sys

def load(p):
    out = {}
    for line in open(p, encoding="utf-8"):
        line = line.strip()
        if line:
            r = json.loads(line)
            out[r["item"]["id"]] = r
    return out

before = load(sys.argv[1])
after = load(sys.argv[2])
ids = sorted(set(before) | set(after))

bp = sum(1 for i in ids if before.get(i, {}).get("scores", {}).get("overall_pass"))
ap = sum(1 for i in ids if after.get(i, {}).get("scores", {}).get("overall_pass"))
print(f"BEFORE {sys.argv[1]}: {bp}/{len(ids)}")
print(f"AFTER  {sys.argv[2]}: {ap}/{len(ids)}\n")

gained, lost = [], []
for i in ids:
    b = before.get(i, {}).get("scores", {}).get("overall_pass", False)
    a = after.get(i, {}).get("scores", {}).get("overall_pass", False)
    if a and not b:
        gained.append(i)
    elif b and not a:
        lost.append(i)
print(f"GAINED ({len(gained)}): {gained}")
print(f"LOST   ({len(lost)}): {lost}\n")

# Did the rate tool get used, and on which items?
print("Regulering_Table_Lookup usage in AFTER run:")
for i in ids:
    seq = after.get(i, {}).get("scores", {}).get("tool_sequence", [])
    if "Regulering_Table_Lookup" in seq:
        passed = after[i]["scores"]["overall_pass"]
        print(f"  {i}: {'PASS' if passed else 'FAIL'}  seq={seq}")
