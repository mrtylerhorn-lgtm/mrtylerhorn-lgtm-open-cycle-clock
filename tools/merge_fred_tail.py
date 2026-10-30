"""Merge recent FRED observations (fetched as text) into the stored series in data/fred/.

Used by the cloud weekly update, where the shell cannot reach FRED but the WebFetch tool can read
https://fred.stlouisfed.org/data/<ID>.txt . The update writes the recent lines it fetched into one
text file, a block per series:

    ## PAYEMS
    2025-07-01 158542
    2025-08-01 158472
    ...

Any separator between date and value works (space, comma, "|").

Usage: python -I tools/merge_fred_tail.py <tails.txt> [data_dir] [--confirmed <tails2.txt>]

--confirmed: a second, independent read of the same series. A series that the first read flags
as having many revisions is merged only if the second read gives identical lines.

What it does, per series:
  * replaces overlapping dates with the fetched values (data revisions) and appends new dates;
  * refuses to merge if the fetched block does not connect to the stored history (a gap),
    if a date or value fails to parse, or if dates are out of order;
  * refuses a series whose revised values move implausibly far from the stored ones, which
    catches transcription mistakes (a dropped digit, a shifted row);
  * refuses a block that matches the stored history better when shifted by one row (a misread),
    one that ends before the stored data (a truncated page), and one with more than
    MAX_REVISIONS changed values unless a second identical read confirms it;
  * prints every revised and every new observation, so the run log shows exactly what changed.
Exit code 1 if any series was refused; the others are still merged.
"""
from __future__ import annotations

import os
import re
import sys
from datetime import date

# how far a revised value may move before the block is treated as a transcription error
REL_LIMIT = {"default": 0.05, "ICSA": 0.15, "WTISPLC": 0.10, "GDPC1": 0.03,
             "CPIAUCSL": 0.01, "CPILFESL": 0.01, "PPIACO": 0.03, "AHETPI": 0.01}
ABS_LIMIT = {  # series measured in percent or index points: absolute limits instead
    "UNRATE": 0.5, "GS10": 0.3, "GS2": 0.3, "FEDFUNDS": 0.3, "USREC": 1.0,
    "GACDFSA066MSFRBPHI": 12.0, "NOCDFSA066MSFRBPHI": 12.0,
}
MAX_REVISIONS = 3
LINE = re.compile(r"(\d{4}-\d{2}-\d{2})\s*[,|\s]\s*(-?\d+(?:\.\d+)?)")


def parse_tails(text: str) -> dict[str, list[tuple[str, str]]]:
    out, cur = {}, None
    for raw in text.splitlines():
        line = raw.strip()
        if line.startswith("##"):
            cur = line.lstrip("#").strip().split()[0]
            out[cur] = []
        elif cur and line:
            m = LINE.search(line)
            if m:
                out[cur].append((m.group(1), m.group(2)))
    return {k: sorted(set(v)) for k, v in out.items()}   # pages may list newest first


def read_csv(path):
    rows = []
    with open(path) as f:
        header = f.readline().strip()
        for line in f:
            if line.strip():
                d, v = line.strip().split(",")[:2]
                rows.append((d, v))
    return header, rows


def step_ok(prev: str, nxt: str, sid: str) -> bool:
    """Is `nxt` the observation right after `prev`? (monthly, quarterly or weekly)"""
    a, b = date.fromisoformat(prev), date.fromisoformat(nxt)
    if sid == "ICSA":
        return (b - a).days == 7
    months = (b.year - a.year) * 12 + b.month - a.month
    return months == (3 if sid == "GDPC1" else 1)


def shift_score(fetched, stored_vals, lag):
    """How many fetched values equal the stored value `lag` rows away."""
    dates = sorted(stored_vals)
    pos = {d: i for i, d in enumerate(dates)}
    hit = 0
    for d, v in fetched:
        i = pos.get(d)
        if i is not None and 0 <= i + lag < len(dates) and stored_vals[dates[i + lag]] == v:
            hit += 1
    return hit


def merge(sid, stored, fetched, confirmed=False):
    issues, notes = [], []
    if not fetched:
        return None, ["no lines parsed"], notes
    if len({d for d, _ in fetched}) != len(fetched):
        return None, ["the same date appears with two different values"], notes
    stored_dates = [d for d, v in stored if v not in ("", ".")]   # blank rows are FRED's own gaps
    for (d1, _), (d2, _) in zip(fetched, fetched[1:]):
        if d2 <= d1:
            issues.append(f"dates out of order: {d1} -> {d2}")
        elif not step_ok(d1, d2, sid) and any(d1 < d < d2 for d in stored_dates):
            issues.append(f"rows missing between {d1} and {d2}")   # a gap FRED itself has is fine
    pos = {d: i for i, (d, _) in enumerate(stored)}
    first = fetched[0][0]
    if first not in pos:
        issues.append(f"fetched block starts at {first}, which is not in the stored history (gap or bad date)")
    if issues:
        return None, issues, notes
    old = {d: v for d, v in stored if v not in ("", ".")}
    last_stored = max(old)
    if fetched[-1][0] < last_stored:
        return None, [f"fetched data ends {fetched[-1][0]}, before the stored data ({last_stored}); the page was probably cut off"], notes
    s0 = shift_score(fetched, old, 0)
    s1 = max(shift_score(fetched, old, -1), shift_score(fetched, old, 1))
    if s1 > s0 and s1 >= 3:
        return None, [f"values line up with the stored data one row off ({s1} vs {s0} matches); the read was probably misaligned"], notes
    decimals = lambda v: len(v.split(".")[1]) if "." in v else 0
    usual = {decimals(v) for v in list(old.values())[-24:]}
    for d, v in fetched:
        if usual and decimals(v) not in usual:
            issues.append(f"{d}: value {v} has {decimals(v)} decimals; this series uses {sorted(usual)} (likely a copying error)")
    for d, v in fetched:
        if d in old and old[d] != v:
            a, b = float(old[d]), float(v)
            if sid in ABS_LIMIT:
                bad = abs(b - a) > ABS_LIMIT[sid]
            else:
                bad = a != 0 and abs(b / a - 1) > REL_LIMIT.get(sid, REL_LIMIT["default"])
            (issues if bad else notes).append(f"{'implausible revision' if bad else 'revised'} {d}: {old[d]} -> {v}")
        elif d not in old:
            notes.append(f"new {d}: {v}")
    n_rev = sum(1 for n in notes if n.startswith("revised"))
    if n_rev > MAX_REVISIONS and not confirmed:
        issues.append(f"{n_rev} values revised at once; read the series again and pass the second read with --confirmed")
    if issues:
        return None, issues, notes
    rows = dict(stored)
    rows.update(dict(fetched))          # fetched values win; blank gap rows and older history are kept
    return sorted(rows.items()), issues, notes


def main():
    args = sys.argv[1:]
    second = None
    if "--confirmed" in args:
        k = args.index("--confirmed")
        second = parse_tails(open(args[k + 1]).read())
        args = args[:k] + args[k + 2:]
    tails = parse_tails(open(args[0]).read())
    data = args[1] if len(args) > 1 else "data"
    refused = []
    for sid, fetched in tails.items():
        path = os.path.join(data, "fred", f"{sid}.csv")
        if not os.path.exists(path):
            print(f"{sid}: no stored file, skipped"); refused.append(sid); continue
        header, stored = read_csv(path)
        ok2 = bool(second and second.get(sid) == fetched)
        if second and sid in second and not ok2:
            print(f"{sid}: REFUSED — the two reads disagree"); refused.append(sid); continue
        merged, issues, notes = merge(sid, stored, fetched, confirmed=ok2)
        if merged is None:
            print(f"{sid}: REFUSED — " + "; ".join(issues)); refused.append(sid); continue
        with open(path, "w") as f:
            f.write(header + "\n" + "\n".join(f"{d},{v}" for d, v in merged) + "\n")
        print(f"{sid}: ok, latest {merged[-1][0]}" + (f" — {'; '.join(notes)}" if notes else " — no change"))
    if refused:
        print("Refused:", ", ".join(refused))
        sys.exit(1)


if __name__ == "__main__":
    main()
