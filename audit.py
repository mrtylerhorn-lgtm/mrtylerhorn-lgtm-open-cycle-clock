"""Statistical audit of the Open Cycle Clock — the tests a practitioner would run before trusting it.

Writes output/audit.json, which the page renders as a report card.

Tests
  1. Does the clock carry information at all?  Dispersion of the nine cells' forward returns vs
     2,000 placebo clocks made by sliding the real zone history to random start dates (keeps how
     long zones last, destroys their timing).
  2. Rank correlation (information coefficient) of each hand with forward returns, and how stable
     it has been decade to decade.
  3. Direction vs level: is the inflation *impulse* more useful than the inflation *level*?
  4. Input report card: each input's own correlation with next-12-month stock returns, in both
     halves of the record (inputs should help, consistently, in the expected direction).
  5. Robustness grid: release lag × smoothing × start date — does the main result survive?
  6. Downside risk: chance of a 10%+ fall within the next 12 months, by cell and row.
  7. Are our "cyclicals" really cyclical?  Each industry's relative return vs forward real GDP growth.
  8. Point-in-time data (ALFRED vintages) for the most-revised inputs, if data/vintage/ exists.
"""
from __future__ import annotations

import copy
import json
import math
import os
import sys

import numpy as np
import pandas as pd

import clock as C

RNG = np.random.default_rng(7)
N_PLACEBO = 2000


def scored_hands(data_dir, cfg):
    g, gparts, _ = C.build_hand(data_dir, cfg["growth"], cfg)
    i, iparts, _ = C.build_hand(data_dir, cfg["inflation"], cfg)
    st = pd.Period(cfg["start"], "M")
    idx = g.index.intersection(i.index)
    g, i = g[idx], i[idx]
    keep = (idx >= st) & g.notna().values & i.notna().values
    return g[keep], i[keep], gparts, iparts


def fwd(mkt, index, cfg, n):
    s = cfg["release_lag"] + 1
    full = pd.period_range(index.min(), max(index.max(), mkt.index.max()) + n + 2, freq="M")
    m = mkt.reindex(full)
    return C.fwd_compound(m["mkt"], s, n).reindex(index), (C.fwd_compound(m["mkt"], s, n) - C.fwd_compound(m["rf"], s, n)).reindex(index)


def dispersion(y, z):
    d = pd.DataFrame({"y": y, "z": z}).dropna()
    m = d.groupby("z").y.mean()
    w = d.groupby("z").size() / len(d)
    return float(np.sqrt((w * (m - d.y.mean()) ** 2).sum()))


def placebo_p(y, z, stat):
    """Share of circularly-shifted zone histories whose statistic is at least as large."""
    d = pd.DataFrame({"y": y, "z": z}).dropna()
    real = stat(d.y, d.z)
    zv, n = d.z.values, len(d)
    hits = 0
    for _ in range(N_PLACEBO):
        k = RNG.integers(24, n - 24)
        if stat(d.y, pd.Series(np.roll(zv, k), index=d.index)) >= real:
            hits += 1
    return real, hits / N_PLACEBO


def spearman(a, b):
    d = pd.DataFrame({"a": a, "b": b}).dropna()
    return float(d.a.rank().corr(d.b.rank())), len(d)


def ic_test(x, y):
    """Rank correlation and a placebo p-value (circular shift of x)."""
    d = pd.DataFrame({"x": x, "y": y}).dropna()
    real = d.x.rank().corr(d.y.rank())
    xv = d.x.rank().values
    yr = d.y.rank().values
    n = len(d)
    null = np.array([np.corrcoef(np.roll(xv, RNG.integers(24, n - 24)), yr)[0, 1] for _ in range(N_PLACEBO)])
    return {"ic": float(real), "p": float((np.abs(null) >= abs(real)).mean()), "n": n}


def rolling_ic(x, y, years=10):
    d = pd.DataFrame({"x": x, "y": y}).dropna()
    out = []
    for end_year in range(d.index[0].year + years, d.index[-1].year + 1, 2):
        sub = d[(d.index.year > end_year - years) & (d.index.year <= end_year)]
        if len(sub) > 60:
            out.append({"end": end_year, "ic": float(sub.x.rank().corr(sub.y.rank()))})
    return out


def max_dd_forward(r, start_shift, n):
    """Worst peak-to-trough fall within months t+s … t+s+n-1 (starting from the level at t+s-1)."""
    lr = np.log1p(r.values)
    out = np.full(len(r), np.nan)
    for t in range(len(r)):
        a, b = t + start_shift, t + start_shift + n
        if b > len(r):
            break
        path = np.concatenate([[0.0], np.cumsum(lr[a:b])])
        out[t] = np.exp((path - np.maximum.accumulate(path)).min()) - 1
    return pd.Series(out, index=r.index)


def gap_t(y, z, lags):
    r = C.nw_gap(y, z, lags, 1)
    return r["gap"], r["t"]


def variant(data_dir, base, **kw):
    cfg = copy.deepcopy(base)
    cfg.update(kw)
    g, i, _, _ = scored_hands(data_dir, cfg)
    _, zc, _ = C.rank_thirds(g)
    _, zr, _ = C.rank_thirds(i)
    mkt = C.load_market(data_dir)
    f12, _ = fwd(mkt, g.index, cfg, 12)
    f6, _ = fwd(mkt, g.index, cfg, 6)
    gi12, ti12 = gap_t(f12, zr, 12)
    gi6, ti6 = gap_t(f6 * 2, zr, 6)
    gg12, tg12 = gap_t(f12, zc, 12)
    # real-time rule under this variant
    g_rt, _, _ = C.build_hand(data_dir, cfg["growth"], cfg, realtime=True)
    i_rt, _, _ = C.build_hand(data_dir, cfg["inflation"], cfg, realtime=True)
    i_rt = i_rt[(i_rt.index >= pd.Period(cfg["rt_start"], "M")) & (i_rt.index <= g.index.max())].dropna()
    zr_rt = C.expanding_terciles(i_rt, cfg["oos_min_months"])
    bt = C.backtest(zr_rt, mkt, cfg)
    return {"infl_gap12": gi12, "infl_t12": ti12, "infl_gap6": gi6, "infl_t6": ti6,
            "growth_gap12": gg12, "growth_t12": tg12,
            "rule_cagr": bt["rule"]["cagr"], "bh_cagr": bt["buy_hold"]["cagr"],
            "rule_dd": bt["rule"]["maxdd"], "bh_dd": bt["buy_hold"]["maxdd"],
            "rule_sharpe": bt["rule"]["sharpe"], "bh_sharpe": bt["buy_hold"]["sharpe"],
            "placebo": bt["placebo_better"]}


def vintage_test(data_dir, cfg):
    """Real-time rule rebuilt with first-release ("initial print") data for the revised inputs.

    fetch_data.py --vintage (needs a free FRED API key) saves first-release series to
    data/vintage/<ID>.csv. Here those replace the revised series and the real-time rule is re-run.
    """
    vdir = os.path.join(data_dir, "vintage")
    if not os.path.isdir(vdir):
        return None
    ids = [f[:-4] for f in os.listdir(vdir) if f.endswith(".csv")]
    if not ids:
        return None
    import shutil, tempfile
    tmp = tempfile.mkdtemp()
    shutil.copytree(os.path.join(data_dir, "fred"), os.path.join(tmp, "fred"))
    shutil.copy(os.path.join(data_dir, "french_monthly.csv"), tmp)
    for sid in ids:
        shutil.copy(os.path.join(vdir, sid + ".csv"), os.path.join(tmp, "fred", sid + ".csv"))
    mkt = C.load_market(data_dir)
    res = {}
    for lab, d in (("revised", data_dir), ("first_release", tmp)):
        g_rt, _, _ = C.build_hand(d, cfg["growth"], cfg, realtime=True)
        i_rt, _, _ = C.build_hand(d, cfg["inflation"], cfg, realtime=True)
        i_rt = i_rt[i_rt.index >= pd.Period(cfg["rt_start"], "M")].dropna()
        zr = C.expanding_terciles(i_rt, cfg["oos_min_months"])
        zc = C.expanding_terciles(g_rt.reindex(i_rt.index).dropna(), cfg["oos_min_months"])
        bt = C.backtest(zr, mkt, cfg)
        res[lab] = {"zr": zr, "zc": zc, "bt": bt}
    shutil.rmtree(tmp)
    a, b = res["revised"], res["first_release"]
    ci = a["zr"].dropna().index.intersection(b["zr"].dropna().index)
    cg = a["zc"].dropna().index.intersection(b["zc"].dropna().index)
    return {"series": sorted(ids),
            "row_agree": float((a["zr"][ci] == b["zr"][ci]).mean()),
            "col_agree": float((a["zc"][cg] == b["zc"][cg]).mean()),
            "rule_cagr": b["bt"]["rule"]["cagr"], "bh_cagr": b["bt"]["buy_hold"]["cagr"],
            "rule_dd": b["bt"]["rule"]["maxdd"], "bh_dd": b["bt"]["buy_hold"]["maxdd"],
            "rule_sharpe": b["bt"]["rule"]["sharpe"], "placebo": b["bt"]["placebo_better"],
            "start": b["bt"]["start"]}


def oos_r2(g, i, y, min_obs=120, step=1):
    """Expanding-window regression forecast of forward returns from both hands, scored out of sample
    against the expanding historical mean (Campbell-Thompson R²). Uses only data known at the time:
    a 12-month forward return for month t is fully known only 13 months later."""
    d = pd.DataFrame({"g": g, "i": i, "y": y}).dropna()
    preds, bench, actual, idx = [], [], [], []
    for k in range(min_obs, len(d)):
        train = d.iloc[: max(0, k - 13)]
        if len(train) < min_obs - 13:
            continue
        X = np.column_stack([np.ones(len(train)), train.g, train.i])
        beta = np.linalg.lstsq(X, train.y.values, rcond=None)[0]
        row = d.iloc[k]
        preds.append(beta[0] + beta[1] * row.g + beta[2] * row.i)
        bench.append(train.y.mean())
        actual.append(row.y)
        idx.append(d.index[k])
    preds, bench, actual = map(np.array, (preds, bench, actual))
    r2 = 1 - ((actual - preds) ** 2).sum() / ((actual - bench) ** 2).sum()
    hit = float((np.sign(preds - bench) == np.sign(actual - bench)).mean())
    return {"r2": float(r2), "hit": hit, "start": str(idx[0]), "n": len(idx)}


def run(data_dir="data", out_json="output/audit.json"):
    cfg = C.CONFIG
    g, i, gparts, iparts = scored_hands(data_dir, cfg)
    _, zc, _ = C.rank_thirds(g)
    _, zr, _ = C.rank_thirds(i)
    zkey = zr.astype(int).astype(str) + zc.astype(int).astype(str)
    mkt = C.load_market(data_dir)
    f12, f12x = fwd(mkt, g.index, cfg, 12)
    f6, _ = fwd(mkt, g.index, cfg, 6)
    s = cfg["release_lag"] + 1
    nxt = mkt["mkt"].reindex(pd.period_range(g.index.min(), mkt.index.max() + 3, freq="M")).shift(-s).reindex(g.index)
    first = g.index < pd.Period(cfg["split"], "M")
    out = {"generated": pd.Timestamp.now(tz="UTC").strftime("%Y-%m-%d")}

    # 1. information content of the whole clock, rows and columns
    info = {}
    for lab, z in (("cells", zkey), ("rows", zr), ("cols", zc)):
        r12, p12 = placebo_p(f12, z, dispersion)
        r6, p6 = placebo_p(f6, z, dispersion)
        info[lab] = {"disp12": r12, "p12": p12, "disp6": r6, "p6": p6}
    out["information"] = info

    # 2. information coefficient and its stability
    out["ic"] = {
        "inflation": {"f12": ic_test(i, f12), "f6": ic_test(i, f6), "m1": ic_test(i, nxt),
                      "h1": spearman(i[first], f12[first])[0], "h2": spearman(i[~first], f12[~first])[0],
                      "rolling": rolling_ic(i, f12)},
        "growth": {"f12": ic_test(g, f12), "f6": ic_test(g, f6), "m1": ic_test(g, nxt),
                   "h1": spearman(g[first], f12[first])[0], "h2": spearman(g[~first], f12[~first])[0],
                   "rolling": rolling_ic(g, f12)},
    }

    # 3. direction vs level of inflation
    cpi = C.load_fred(data_dir, "CPIAUCSL")
    level = (100 * np.log(cpi / cpi.shift(12))).rolling(3).mean().reindex(g.index)
    _, zl, _ = C.rank_thirds(level)
    gl, tl = gap_t(f12, zl, 12)
    gd, td = gap_t(f12, zr, 12)
    out["direction_vs_level"] = {
        "direction": {"ic": ic_test(i, f12)["ic"], "gap12": gd, "t12": td},
        "level": {"ic": ic_test(level, f12)["ic"], "gap12": gl, "t12": tl},
        "corr_between": float(i.corr(level)),
    }

    # 4. input report card
    cards = []
    for hand, spec, parts in (("growth", cfg["growth"], gparts), ("inflation", cfg["inflation"], iparts)):
        for sid, _, _, _, label, _ in spec:
            x = parts[sid].reindex(g.index)
            full = ic_test(x, f12)
            a, _ = spearman(x[first], f12[first])
            b, _ = spearman(x[~first], f12[~first])
            cards.append({"id": sid, "hand": hand, "label": label, "ic": full["ic"], "p": full["p"],
                          "h1": a, "h2": b, "consistent": bool(np.sign(a) == np.sign(b) == -1)})
    out["inputs"] = cards

    # 5. robustness grid
    grid = []
    for lag in (1, 2):
        for sm in (1, 3, 6):
            for start in ("1968-07", "1975-01"):
                r = variant(data_dir, cfg, release_lag=lag, smooth_months=sm, start=start)
                r.update({"lag": lag, "smooth": sm, "start": start})
                grid.append(r)
    out["grid"] = grid

    # 6. downside risk
    dd = max_dd_forward(mkt["mkt"].reindex(pd.period_range(g.index.min(), mkt.index.max(), freq="M")), s, 12).reindex(g.index)
    has = dd.notna()
    risk = {"all": float((dd[has] <= -0.10).mean()), "all20": float((dd[has] <= -0.20).mean()), "cells": {}, "rows": [], "cols": []}
    for k in C.KEYS:
        m = has & (zkey == k)
        risk["cells"][k] = {"p10": float((dd[m] <= -0.10).mean()), "p20": float((dd[m] <= -0.20).mean()),
                            "median_dd": float(dd[m].median()), "n": int(m.sum())}
    for j in range(3):
        m = has & (zr == j)
        risk["rows"].append(float((dd[m] <= -0.10).mean()))
        m = has & (zc == j)
        risk["cols"].append(float((dd[m] <= -0.10).mean()))
    # is the higher crash risk in "heating" months more than timing luck?
    d = pd.DataFrame({"hit": (dd <= -0.10).astype(float), "z": zr}).dropna()
    def gap_stat(y, z):
        return float(y[z == 2].mean() - y[z != 2].mean())
    real, p = placebo_p(d.hit, d.z, gap_stat)
    risk["heating_gap"], risk["heating_p"] = real, p
    # same, using the real-time (no-hindsight) rows
    i_rt, _, _ = C.build_hand(data_dir, cfg["inflation"], cfg, realtime=True)
    i_rt = i_rt[(i_rt.index >= pd.Period(cfg["rt_start"], "M")) & (i_rt.index <= g.index.max())].dropna()
    zr_rt = C.expanding_terciles(i_rt, cfg["oos_min_months"]).dropna()
    dd_rt = max_dd_forward(mkt["mkt"].reindex(pd.period_range(zr_rt.index.min(), mkt.index.max(), freq="M")), s, 12).reindex(zr_rt.index)
    m = dd_rt.notna()
    risk["rt_rows"] = [float((dd_rt[m & (zr_rt == j)] <= -0.10).mean()) for j in range(3)]
    risk["rt_all"] = float((dd_rt[m] <= -0.10).mean())
    risk["rt_start"] = str(zr_rt.index[0])
    for j, half in ((0, "h1"), (1, "h2")):
        mm = has & ((g.index < pd.Period(cfg["split"], "M")) if j == 0 else (g.index >= pd.Period(cfg["split"], "M")))
        risk[half] = [float((dd[mm & (zr == k)] <= -0.10).mean()) for k in range(3)]
    out["risk"] = risk

    # 7. cyclicality of industries: relative 12m return vs real GDP growth over the same 12 months
    gdp = C.load_fred(data_dir, "GDPC1")
    gdp_m = gdp.resample("M").interpolate() if hasattr(gdp, "resample") else gdp
    gdp_m = pd.Series(np.interp(np.arange(gdp.index.min().ordinal, gdp.index.max().ordinal + 1),
                                [p.ordinal for p in gdp.index], gdp.values),
                      index=pd.period_range(gdp.index.min(), gdp.index.max(), freq="M"))
    gdp_g = (gdp_m.shift(-12) / gdp_m - 1)
    rows = []
    full = pd.period_range(g.index.min(), mkt.index.max(), freq="M")
    m = mkt.reindex(full)
    mk12 = C.fwd_compound(m["mkt"], 0, 12)
    for ind, label in C.INDUSTRY_LABELS.items():
        rel = C.fwd_compound(m[ind], 0, 12) - mk12
        d = pd.DataFrame({"rel": rel, "gdp": gdp_g.reindex(full)}).dropna()
        beta = float(np.polyfit(d.gdp, d.rel, 1)[0])
        rows.append({"id": ind, "label": label, "corr": float(d.rel.corr(d.gdp)), "beta": beta,
                     "assigned": "cyclical" if ind in cfg["cyclicals"] else "defensive" if ind in cfg["defensives"] else "neither"})
    out["cyclicality"] = sorted(rows, key=lambda r: -r["corr"])

    # 8. out-of-sample forecast power of the two hands combined
    out["oos"] = {"f12": oos_r2(g, i, f12), "f6": oos_r2(g, i, f6)}

    # 9. point-in-time data
    out["vintage"] = vintage_test(data_dir, cfg)

    os.makedirs(os.path.dirname(out_json) or ".", exist_ok=True)
    json.dump(out, open(out_json, "w"), separators=(",", ":"))
    return out


if __name__ == "__main__":
    res = run(*(sys.argv[1:] or []))
    I = res["information"]
    print("Information test (placebo p, 12m / 6m):", {k: (round(v["p12"], 3), round(v["p6"], 3)) for k, v in I.items()})
    for h in ("inflation", "growth"):
        x = res["ic"][h]
        print(f"IC {h}: 12m {x['f12']['ic']:+.3f} (p {x['f12']['p']:.3f})  6m {x['f6']['ic']:+.3f} (p {x['f6']['p']:.3f})  "
              f"1m {x['m1']['ic']:+.3f} (p {x['m1']['p']:.3f})  halves {x['h1']:+.3f}/{x['h2']:+.3f}")
    dv = res["direction_vs_level"]
    print("Direction vs level:", {k: (round(v["ic"], 3), round(v["t12"], 2)) for k, v in dv.items() if isinstance(v, dict)}, "corr", round(dv["corr_between"], 2))
    for c in res["inputs"]:
        print(f"  {c['label']:32s} IC {c['ic']:+.3f} p {c['p']:.2f}  halves {c['h1']:+.3f} {c['h2']:+.3f}  {'OK' if c['consistent'] else '--'}")
    print("Grid:")
    for r in res["grid"]:
        print(f"  lag {r['lag']} sm {r['smooth']} start {r['start']}: infl gap12 {r['infl_gap12']:+.3f} t {r['infl_t12']:.2f} | 6m t {r['infl_t6']:.2f} | growth t12 {r['growth_t12']:.2f} | rule {r['rule_cagr']:.3f} vs {r['bh_cagr']:.3f}, dd {r['rule_dd']:.2f} vs {r['bh_dd']:.2f}, placebo {r['placebo']:.3f}")
    print("Risk rows (P 10% fall):", [round(x, 2) for x in res["risk"]["rows"]], "cols", [round(x, 2) for x in res["risk"]["cols"]], "all", round(res["risk"]["all"], 2))
    print("Cyclicality:", [(c["id"], round(c["corr"], 2), c["assigned"][0]) for c in res["cyclicality"]])
    print("OOS R2:", res["oos"])
    r = res["risk"]
    print("Risk heating gap", round(r["heating_gap"], 3), "placebo p", r["heating_p"], "rt rows", [round(x, 2) for x in r["rt_rows"]], "h1", [round(x, 2) for x in r["h1"]], "h2", [round(x, 2) for x in r["h2"]])
    print("Vintage:", res["vintage"])
