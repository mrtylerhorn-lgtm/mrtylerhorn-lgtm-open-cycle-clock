"""Fail the run (non-zero exit) if the new output looks wrong, so it is never committed or published.

Usage: python tools/check_outputs.py <prev_clock.json or missing> <new clock.json>
"""
import json
import os
import sys

prev_path, cur_path = sys.argv[1], sys.argv[2]
cur = json.load(open(cur_path))
problems = []

if os.path.exists(prev_path):
    prev = json.load(open(prev_path))
    if cur["latest_month"] < prev["latest_month"]:
        problems.append(f"latest month went backwards: {prev['latest_month']} -> {cur['latest_month']}")
    if cur.get("returns_through", "") < prev.get("returns_through", ""):
        problems.append("stock-return history got shorter")


def months_between(a, b):
    ya, ma = map(int, a[:7].split("-"))
    yb, mb = map(int, b[:7].split("-"))
    return (yb - ya) * 12 + mb - ma


for x in cur["inputs"]["growth"] + cur["inputs"]["inflation"]:
    if months_between(x["asof"], cur["latest_month"]) > 3:
        problems.append(f"{x['id']} is stale (data to {x['asof']})")
if len(cur["series"]) < 600:
    problems.append(f"only {len(cur['series'])} monthly readings; history looks truncated")
if cur["current"]["z"] not in cur["zones"]:
    problems.append("current cell missing")

if problems:
    print("CHECKS FAILED:\n  " + "\n  ".join(problems))
    sys.exit(1)
print(f"Checks passed: {cur['latest_month']}, {cur['zones'][cur['current']['z']]['name']}")
