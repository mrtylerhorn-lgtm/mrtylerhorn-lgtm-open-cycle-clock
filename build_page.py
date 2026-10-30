"""Build the self-contained page: inject the model output, the audit and a "what changed" summary.

Usage:  python build_page.py [--prev previous_clock.json]
"""
import argparse
import json
import os
from datetime import datetime, timezone


def changes(cur, prev):
    """Plain facts about what moved since the previous run."""
    if not prev:
        return None
    out = {"prev_generated": prev.get("built") or prev.get("generated"), "prev_month": prev.get("latest_month"), "items": []}
    c, p = cur["current"], prev["current"]
    zn = lambda d, k: d["zones"][k]["name"]
    if cur["latest_month"] != prev["latest_month"]:
        out["items"].append(f"New monthly reading: {cur['latest_month']} (previous run ended at {prev['latest_month']}).")
    if c["z"] != p["z"]:
        out["items"].append(f"The clock moved from {zn(prev, p['z'])} to {zn(cur, c['z'])}.")
    else:
        out["items"].append(f"Still in {zn(cur, c['z'])}.")
    for lab, a, b in (("Growth", p["x"], c["x"]), ("Inflation direction", p["y"], c["y"])):
        if abs(b - a) >= 0.02:
            out["items"].append(f"{lab} moved from the {round(a * 100)}th to the {round(b * 100)}th percentile.")
    pin = {x["id"]: x for x in prev["inputs"]["growth"] + prev["inputs"]["inflation"]}
    fresh = [x["label"] for x in cur["inputs"]["growth"] + cur["inputs"]["inflation"]
             if x["id"] in pin and x["asof"] != pin[x["id"]]["asof"]]
    if fresh:
        out["items"].append("New data since last run: " + ", ".join(fresh) + ".")
    pe = {e["id"]: e for e in prev.get("facts", {}).get("early", [])}
    for e in cur.get("facts", {}).get("early", []):
        q = pe.get(e["id"])
        if q is None or q["value"] != e["value"] or q["month"] != e["month"]:
            v = f"{e['value'] / 1000:.0f}k" if e["id"] == "ICSA" else e["value"]
            out["items"].append(f"Early data for {e['month']}: {e['label']} now {v}.")
    if prev.get("returns_through") and cur.get("returns_through") != prev.get("returns_through"):
        out["items"].append(f"Stock-return history extended to {cur['returns_through']}.")
    pb, cb = prev.get("backtest", {}), cur.get("backtest", {})
    if pb and cb and pb.get("in_cash_next") != cb.get("in_cash_next"):
        out["items"].append(f"The trading rule switched to {'cash' if cb['in_cash_next'] else 'stocks'}.")
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--clock", default="output/clock.json")
    ap.add_argument("--audit", default="output/audit.json")
    ap.add_argument("--prev", default=None)
    ap.add_argument("--out", default="output/cycle_clock.html")
    ap.add_argument("--standalone", action="store_true",
                    help="wrap in a full HTML document (for GitHub Pages or opening as a file)")
    a = ap.parse_args()
    cur = json.load(open(a.clock))
    aud = json.load(open(a.audit)) if os.path.exists(a.audit) else None
    prev = json.load(open(a.prev)) if a.prev and os.path.exists(a.prev) else None
    cur["built"] = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    cur["changes"] = changes(cur, prev)
    tpl = open("web/template.html").read()
    html = tpl.replace("/*__DATA__*/null", json.dumps(cur, separators=(",", ":"))) \
              .replace("/*__AUDIT__*/null", json.dumps(aud, separators=(",", ":")))
    if a.standalone:
        html = ('<!doctype html><html lang="en"><head><meta charset="utf-8">'
                '<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">'
                '<style>:root{padding-top:env(safe-area-inset-top,0px);padding-bottom:env(safe-area-inset-bottom,0px)}'
                'body{margin:0;font:14px system-ui,sans-serif}img{max-width:100%}[hidden]{display:none!important}</style>'
                '</head><body>' + html + '</body></html>')
    os.makedirs(os.path.dirname(a.out) or ".", exist_ok=True)
    open(a.out, "w").write(html)
    if a.standalone:
        print("wrote", a.out)
        return
    keep = {k: v for k, v in cur.items() if k != "changes"}
    json.dump(keep, open(a.clock, "w"), separators=(",", ":"))   # carries "built" into next week's comparison
    print("wrote", a.out, "| changes:", (cur["changes"] or {}).get("items"))


if __name__ == "__main__":
    main()
