"""Open Cycle Clock — an open-source take on RenMac's Market Cycle Clock.

RenMac's publicly described design (see README):
  * inflation on the vertical axis, growth on the horizontal axis
  * each axis cut into thirds  ->  a 3x3 grid of nine zones
  * monthly readings, history back to the 1960s
  * inflation is about DIRECTION, not level; energy prices are a big input
  * the clock is judged by what each zone did for stocks, sectors and yields

This file rebuilds that from public data:
  growth hand    = average z-score of activity growth and factory surveys
  inflation hand = average z-score of inflation *impulse* (is it speeding up?)
  zones          = historical thirds of each hand
then measures what stocks, cash, industries, yields and the Fed did afterwards,
with Newey-West standard errors wherever forward windows overlap, a real-time
(no-hindsight) rebuild, a simple trading test, and a sensitivity check on
today's reading.

Usage:  python clock.py [data_dir] [out_json]
"""
from __future__ import annotations

import json
import math
import os
import sys

import numpy as np
import pandas as pd

# --------------------------------------------------------------------------
# configuration — everything that defines the model lives here
# --------------------------------------------------------------------------
CONFIG = {
    "start": "1968-07",          # first scored month: every input exists from here
    "rt_start": "1959-01",       # real-time rebuild may use the thinner early data that was knowable
    "split": "1995-01",          # robustness check: first half vs second half
    "smooth_months": 3,          # rolling mean applied to each hand
    "min_inputs": 3,             # a hand needs at least this many inputs in a month
    "carry_months": 2,           # late releases: carry an input's last value forward this far
    "release_lag": 1,            # a month-t reading is treated as known at the end of month t+1
    "trail_months": 24,
    "oos_min_months": 120,       # real-time: history needed before zones are assigned
    # (FRED id, transform, sign, months after month-end until published, label, plain-English description)
    "growth": [
        ("INDPRO",   "yoy_log",    +1, 1, "Industrial production", "Output of factories, mines and utilities vs a year ago"),
        ("PAYEMS",   "yoy_log",    +1, 1, "Payroll jobs", "Number of jobs vs a year ago"),
        ("W875RX1",  "yoy_log",    +1, 1, "Real income ex-transfers", "Household income after inflation, excluding government payments, vs a year ago"),
        ("CMRMTSPL", "yoy_log",    +1, 2, "Real business sales", "Inflation-adjusted manufacturing, wholesale and retail sales vs a year ago (published about two months late, so lagged a month here)"),
        ("HOUST",    "yoy_log_3m", +1, 1, "Housing starts", "New homes being built (3-month average) vs a year ago"),
        ("UNRATE",   "diff12",     -1, 1, "Unemployment rate (inverted)", "Change in the jobless rate over 12 months; a falling rate counts as strength"),
        ("ICSA",     "yoy_log",    -1, 0, "Jobless claims (inverted)", "New unemployment claims vs a year ago; fewer claims count as strength"),
        ("GACDFSA066MSFRBPHI", "level3", +1, 0, "Philly Fed factory activity", "Survey of manufacturers: share reporting more vs less activity (3-month average)"),
        ("NOCDFSA066MSFRBPHI", "level3", +1, 0, "Philly Fed new orders", "Survey of manufacturers: share reporting more vs fewer new orders (3-month average)"),
    ],
    "inflation": [
        ("WTISPLC",  "yoy_log",     +1, 0, "Crude oil price", "Oil price vs a year ago"),
        ("PPIACO",   "yoy_impulse", +1, 1, "Producer prices", "Is producer commodity-price inflation speeding up? Change over 6 months in its yearly rate"),
        ("CPIAUCSL", "yoy_impulse", +1, 1, "Consumer prices", "Is consumer inflation speeding up? Change over 6 months in the yearly CPI rate"),
        ("CPILFESL", "yoy_impulse", +1, 1, "Core consumer prices", "Same, excluding food and energy"),
        ("AHETPI",   "yoy_impulse", +1, 1, "Hourly wages", "Is wage growth speeding up? Change over 6 months in its yearly rate"),
    ],
    "cyclicals": ["Durbl", "Manuf", "BusEq"],          # chosen by relative return vs forward real GDP (audit.py §7)
    "defensives": ["NoDur", "Utils", "Hlth"],
    "survey_ids": ["GACDFSA066MSFRBPHI", "NOCDFSA066MSFRBPHI"],
}

INDUSTRY_LABELS = {
    "NoDur": "Consumer staples", "Durbl": "Cars & durables", "Manuf": "Manufacturing",
    "Enrgy": "Energy", "Chems": "Chemicals", "BusEq": "Technology",
    "Telcm": "Telecom", "Utils": "Utilities", "Shops": "Retail & services",
    "Hlth": "Health care", "Money": "Financials", "Other": "Other",
}

# (inflation row, growth col) -> (short name, long name); row 0 = cooling, col 0 = weak
ZONE_NAMES = {
    (0, 0): ("Cooling slowdown", "Inflation cooling, growth weak"),
    (0, 1): ("Goldilocks", "Inflation cooling, growth average"),
    (0, 2): ("Cool boom", "Inflation cooling, growth strong"),
    (1, 0): ("Soft patch", "Inflation steady, growth weak"),
    (1, 1): ("Steady state", "Inflation steady, growth average"),
    (1, 2): ("Expansion", "Inflation steady, growth strong"),
    (2, 0): ("Stagflation", "Inflation heating, growth weak"),
    (2, 1): ("Late-cycle squeeze", "Inflation heating, growth average"),
    (2, 2): ("Overheating", "Inflation heating, growth strong"),
}
KEYS = [f"{r}{c}" for r in range(3) for c in range(3)]

RENMAC_CHECKS = [  # (latest data month at the time, RenMac's stated position, its cell, source)
    ("2022-04", "Top middle: slowing growth, high inflation", "21", "RenMac Tech deep dive, May 2022"),
    ("2024-01", "Bullish bottom zone, 17th straight month", "00", "RenMac Desk Note, Mar 2024"),
    ("2024-03", "Rising from bottom left to middle left", "10", "Josh Brown, May 2024"),
    ("2024-10", "Bottom left: low inflation, low growth", "00", "EFG podcast, Dec 2024"),
    ("2025-08", "Below-average growth, inflation ticked up", "10", "EFG podcast, Oct 2025"),
    ("2025-10", "Slow growth, inflation still elevated", "10", "Mauldin Economics, Dec 2025"),
    ("2026-08", "Top right: growth and inflation, Fed biased tight", "22", "The Compound, Oct 2026"),
]


# --------------------------------------------------------------------------
# data
# --------------------------------------------------------------------------
def load_fred(data_dir: str, sid: str) -> pd.Series:
    df = pd.read_csv(os.path.join(data_dir, "fred", f"{sid}.csv"))
    s = pd.Series(pd.to_numeric(df.iloc[:, 1], errors="coerce").values,
                  index=pd.to_datetime(df.iloc[:, 0]), name=sid).dropna()
    if sid == "ICSA":  # weekly -> monthly average
        s = s.resample("MS").mean()
    s.index = s.index.to_period("M")
    return s


def load_market(data_dir: str) -> pd.DataFrame:
    df = pd.read_csv(os.path.join(data_dir, "french_monthly.csv"), dtype={"ym": str})
    df.index = pd.PeriodIndex(pd.to_datetime(df.pop("ym"), format="%Y%m"), freq="M")
    return df.replace([-99.99, -999], np.nan) / 100.0


def transform(s: pd.Series, how: str) -> pd.Series:
    full = s.reindex(pd.period_range(s.index.min(), s.index.max(), freq="M"))
    if how == "yoy_log":
        return 100 * np.log(full / full.shift(12))
    if how == "yoy_log_3m":
        sm = full.rolling(3).mean()
        return 100 * np.log(sm / sm.shift(12))
    if how == "diff12":
        return full - full.shift(12)
    if how == "yoy_impulse":
        yoy = 100 * np.log(full / full.shift(12))
        return yoy - yoy.shift(6)
    if how == "level3":
        return full.rolling(3).mean()
    raise ValueError(how)


# --------------------------------------------------------------------------
# building the hands
# --------------------------------------------------------------------------
def zscore_full(x, start):
    base = x[x.index >= pd.Period(start, "M")].dropna()
    return (x - base.mean()) / base.std()


def zscore_expanding(x, min_obs=60):
    return (x - x.expanding(min_obs).mean()) / x.expanding(min_obs).std()


def build_hand(data_dir, spec, cfg, realtime=False, smooth=None):
    """Return (hand, parts, fresh_counts). parts holds each input's signed z-score."""
    smooth = smooth or cfg["smooth_months"]
    parts, fresh = {}, {}
    for sid, how, sign, pub_lag, *_ in spec:
        t = transform(load_fred(data_dir, sid), how)
        t = t.shift(max(0, pub_lag - cfg["release_lag"]))     # late publishers enter a month later
        z = zscore_expanding(t) if realtime else zscore_full(t, cfg["start"])
        z = (sign * z).clip(-4, 4)                            # winsorise so single months (2020) don't dominate
        fresh[sid] = z.notna()
        parts[sid] = z
    df = pd.DataFrame(parts)
    fr = pd.DataFrame(fresh).reindex(df.index).fillna(False)
    df = df.ffill(limit=cfg["carry_months"])                  # ragged edge
    raw = df.mean(axis=1, skipna=True).where(df.notna().sum(axis=1) >= cfg["min_inputs"])
    hand = raw.rolling(smooth, min_periods=1).mean().where(raw.notna())
    if not realtime:                                          # so +1 means one typical swing of the hand
        hand = zscore_full(hand, cfg["start"])
    return hand, df, fr.sum(axis=1)


def rank_thirds(x: pd.Series):
    """Percentile (mid-rank) and third for each month; the dot and its cell always agree."""
    n = x.notna().sum()
    p = (x.rank(method="first") - 0.5) / n
    z = np.floor(p * 3).clip(0, 2)
    v = np.sort(x.dropna().values)
    k1, k2 = int((z == 0).sum()), int((z <= 1).sum())
    cuts = [float((v[k1 - 1] + v[k1]) / 2), float((v[k2 - 1] + v[k2]) / 2)]
    return p, z, cuts


def expanding_terciles(x: pd.Series, min_obs: int) -> pd.Series:
    out = pd.Series(np.nan, index=x.index)
    vals = x.dropna()
    for k in range(min_obs, len(vals)):
        lo, hi = vals.iloc[:k].quantile([1 / 3, 2 / 3])
        v = vals.iloc[k]
        out[vals.index[k]] = 0 if v < lo else (1 if v < hi else 2)
    return out


def current_zone(data_dir, cfg, gspec, ispec, last, smooth=None):
    g, _, _ = build_hand(data_dir, gspec, cfg, smooth=smooth)
    i, _, _ = build_hand(data_dir, ispec, cfg, smooth=smooth)
    st = pd.Period(cfg["start"], "M")
    idx = g.index.intersection(i.index)
    g, i = g[idx], i[idx]
    keep = (idx >= st) & (idx <= last) & g.notna().values & i.notna().values
    pg, zc, _ = rank_thirds(g[keep])
    pi, zr, _ = rank_thirds(i[keep])
    return f"{int(zr[last])}{int(zc[last])}", float(pg[last]), float(pi[last])


# --------------------------------------------------------------------------
# statistics
# --------------------------------------------------------------------------
def fwd_compound(r, start_shift, n):
    """Compounded return over months t+start_shift … t+start_shift+n-1."""
    return np.expm1(np.log1p(r).rolling(n).sum().shift(-(start_shift + n - 1)))


def nw_means(y: pd.Series, groups: pd.Series, labels, lags: int):
    """Group means of y with Newey-West covariance (handles overlapping windows).

    Regresses y on one dummy per group (no intercept): coefficients are the group
    means; the HAC covariance lets us test means and differences honestly.
    """
    d = pd.DataFrame({"y": y, "g": groups}).dropna()
    X = np.column_stack([(d.g == l).values.astype(float) for l in labels])
    yv = d.y.values
    XtX = X.T @ X
    if np.linalg.matrix_rank(XtX) < len(labels):
        return None
    inv = np.linalg.inv(XtX)
    b = inv @ X.T @ yv
    u = yv - X @ b
    Xu = X * u[:, None]
    S = Xu.T @ Xu
    for l in range(1, lags + 1):
        w = 1 - l / (lags + 1)
        G = Xu[l:].T @ Xu[:-l]
        S += w * (G + G.T)
    V = inv @ S @ inv
    return {"mean": b, "V": V, "n": X.sum(axis=0)}


def nw_cells(y, zkey, lags, scale=1.0):
    r = nw_means(y, zkey, KEYS, lags)
    out = {}
    for j, k in enumerate(KEYS):
        m, se = r["mean"][j] * scale, math.sqrt(r["V"][j, j]) * scale
        out[k] = {"mean": float(m), "se": float(se), "t": float(m / se) if se else None,
                  "lo": float(m - 1.645 * se), "hi": float(m + 1.645 * se), "n": int(r["n"][j])}
    return out


def nw_gap(y, z, lags, scale=1.0):
    """Low third minus high third, with Newey-West t-stat; plus the three means."""
    r = nw_means(y, z, [0, 1, 2], lags)
    b, V = r["mean"] * scale, r["V"] * scale ** 2
    se = math.sqrt(V[0, 0] + V[2, 2] - 2 * V[0, 2])
    return {"means": [float(x) for x in b],
            "ses": [float(math.sqrt(V[j, j])) for j in range(3)],
            "gap": float(b[0] - b[2]), "t": float((b[0] - b[2]) / se)}


def max_dd_forward(r, start_shift, n):
    """Worst peak-to-trough fall within months t+s … t+s+n-1 (measured from the level at the start)."""
    lr = np.log1p(r.values)
    out = np.full(len(r), np.nan)
    for t in range(len(r)):
        a, b = t + start_shift, t + start_shift + n
        if b > len(r):
            break
        path = np.concatenate([[0.0], np.cumsum(lr[a:b])])
        out[t] = np.exp((path - np.maximum.accumulate(path)).min()) - 1
    return pd.Series(out, index=r.index)


class Market:
    """Forward-looking series aligned to reading months (with the release lag)."""

    def __init__(self, mkt, index, cfg, data_dir):
        s = cfg["release_lag"] + 1           # first month after the data is out
        full = pd.period_range(index.min(), max(index.max(), mkt.index.max()) + 14, freq="M")
        m = mkt.reindex(full)
        R = lambda x: x.reindex(index)
        self.nxt = R(m["mkt"].shift(-s))
        self.cash = R(m["rf"].shift(-s))
        self.ex = self.nxt - self.cash
        self.f6 = R(fwd_compound(m["mkt"], s, 6))
        self.f12 = R(fwd_compound(m["mkt"], s, 12))
        self.f6_ex = self.f6 - R(fwd_compound(m["rf"], s, 6))
        self.f12_ex = self.f12 - R(fwd_compound(m["rf"], s, 12))
        f6 = fwd_compound(m["mkt"], s, 6)
        self.rel6 = {k: R(fwd_compound(m[k], s, 6) - f6) for k in INDUSTRY_LABELS}
        cyc, dfn = m[cfg["cyclicals"]].mean(axis=1), m[cfg["defensives"]].mean(axis=1)
        self.cvd6 = R(fwd_compound(cyc, s, 6) - fwd_compound(dfn, s, 6))
        # yields and policy rate: change from the month the data are out (t+1) to six months later
        for name, sid in (("gs10", "GS10"), ("gs2", "GS2"), ("ff", "FEDFUNDS")):
            x = load_fred(data_dir, sid).reindex(full)
            setattr(self, name + "_6m", R(x.shift(-(s + 5)) - x.shift(-(s - 1))))
        self.dd12 = R(max_dd_forward(m["mkt"].fillna(np.nan), s, 12))
        cpi = load_fred(data_dir, "CPIAUCSL").reindex(full)
        self.cpi_yoy = R((cpi / cpi.shift(12) - 1))


def summarize(mk, zkey, zr, zc, first, cfg):
    """All per-cell statistics."""
    ret = nw_cells(mk.nxt, zkey, 2, 12)
    ex = nw_cells(mk.ex, zkey, 2, 12)
    f12 = nw_cells(mk.f12, zkey, 12)
    f12x = nw_cells(mk.f12_ex, zkey, 12)
    gs = nw_cells(mk.gs10_6m, zkey, 6)
    gs2 = nw_cells(mk.gs2_6m, zkey, 6)
    ff = nw_cells(mk.ff_6m, zkey, 6)
    cvd = nw_cells(mk.cvd6, zkey, 6)
    rel = {k: nw_cells(v, zkey, 6) for k, v in mk.rel6.items()}
    tot = (zkey.notna()).sum()
    out = {}
    for k in KEYS:
        r, c = int(k[0]), int(k[1])
        sel = zkey == k
        nx = mk.nxt[sel].dropna()
        f = mk.f12[sel].dropna()
        short, long_ = ZONE_NAMES[(r, c)]
        h = lambda ser, m: float(ser[m].mean() * 12) if ser[m].notna().sum() else None
        out[k] = {
            "row": r, "col": c, "name": short, "long": long_,
            "months": int(sel.sum()), "share": float(sel.sum() / tot),
            "years": int(len(set(p.year for p in zkey[sel].index))),
            "ret": ret[k], "ex": ex[k],
            "ret_h1": h(mk.nxt, sel & first), "ret_h2": h(mk.nxt, sel & ~first),
            "ex_h1": h(mk.ex, sel & first), "ex_h2": h(mk.ex, sel & ~first),
            "n_h1": int(mk.nxt[sel & first].notna().sum()), "n_h2": int(mk.nxt[sel & ~first].notna().sum()),
            "hit": float((nx > 0).mean()), "vol": float(nx.std() * math.sqrt(12)), "worst_month": float(nx.min()),
            "f12": f12[k], "f12_ex": f12x[k], "f12_med": float(f.median()),
            "f12_h1": float(mk.f12[sel & first].mean()) if mk.f12[sel & first].notna().any() else None,
            "f12_h2": float(mk.f12[sel & ~first].mean()) if mk.f12[sel & ~first].notna().any() else None,
            "f12_n": int(f.size),
            "f12_p10": float(f.quantile(.1)), "f12_p90": float(f.quantile(.9)), "f12_pos": float((f > 0).mean()),
            "gs10_6m": gs[k], "gs2_6m": gs2[k], "ff_6m": ff[k], "cyc6": cvd[k],
            "sectors": {ind: {"mean": rel[ind][k]["mean"], "t": rel[ind][k]["t"]} for ind in INDUSTRY_LABELS},
            "cpi_level": float(mk.cpi_yoy[sel].mean()),
            "fall10": float((mk.dd12[sel].dropna() <= -0.10).mean()), "fall20": float((mk.dd12[sel].dropna() <= -0.20).mean()),
            "dd_med": float(mk.dd12[sel].dropna().median()), "dd_n": int(mk.dd12[sel].notna().sum()),
        }
    return out


def axis_tests(mk, z, first):
    out = {"horizons": {}}
    for lab, y, ye, lags, scale in (("1m", mk.nxt, mk.ex, 2, 12), ("6m", mk.f6, mk.f6_ex, 6, 2), ("12m", mk.f12, mk.f12_ex, 12, 1)):
        out["horizons"][lab] = {"total": nw_gap(y, z, lags, scale), "excess": nw_gap(ye, z, lags, scale),
                                "h1": nw_gap(y[first], z[first], lags, scale)["means"],
                                "h2": nw_gap(y[~first], z[~first], lags, scale)["means"]}
    out["months"] = [int((z == j).sum()) for j in range(3)]
    out["cpi_level"] = [float(mk.cpi_yoy[z == j].mean()) for j in range(3)]
    out["fall10"] = [float((mk.dd12[z == j].dropna() <= -0.10).mean()) for j in range(3)]
    return out


def transitions(zr, zc, ahead=6):
    z = zr * 3 + zc
    nxt = z.shift(-ahead)
    out = {}
    for k in range(9):
        sel = nxt[(z == k) & nxt.notna()]
        out[f"{k // 3}{k % 3}"] = [float((sel == j).mean()) if len(sel) else 0.0 for j in range(9)]
    return out


def episodes(zkey, mk):
    runs = (zkey != zkey.shift()).cumsum()
    out = {k: [] for k in KEYS}
    for _, grp in zkey.groupby(runs):
        k = grp.iloc[0]
        f = mk.f12.get(grp.index[0])
        out[k].append({"start": str(grp.index[0]), "end": str(grp.index[-1]), "months": len(grp),
                       "f12": None if pd.isna(f) else round(float(f), 4)})
    return out


# --------------------------------------------------------------------------
# trading test, today's sensitivity, plain facts, RenMac comparison
# --------------------------------------------------------------------------
def backtest(zr_rt, mkt, cfg, seed=0):
    """Hold the market, except hold T-bills when the real-time inflation row is 'heating'.
    A month-t reading is acted on in month t+2."""
    sig = zr_rt.dropna()
    pos = (sig != 2).astype(float)
    pos.index = pos.index + (cfg["release_lag"] + 1)
    idx = pos.index.intersection(mkt.index)
    p, bh, cash = pos[idx], mkt["mkt"][idx], mkt["rf"][idx]
    rule = p * bh + (1 - p) * cash
    mix = p.mean() * bh + (1 - p.mean()) * cash

    def st(r):
        w = (1 + r).cumprod()
        ex = r - cash.reindex(r.index)
        yr = (1 + r).groupby(r.index.year).prod() - 1
        return {"cagr": float(w.iloc[-1] ** (12 / len(r)) - 1), "vol": float(r.std() * math.sqrt(12)),
                "maxdd": float((w / w.cummax() - 1).min()), "sharpe": float(ex.mean() / ex.std() * math.sqrt(12)),
                "worst_year": float(yr.min()), "worst_year_at": int(yr.idxmin()), "growth_of_1": float(w.iloc[-1])}

    rng = np.random.default_rng(seed)
    pv, bv, cv = p.values, bh.values, cash.values
    plc = []
    for _ in range(2000):
        q = np.roll(pv, rng.integers(24, len(pv) - 24))
        e = q * bv + (1 - q) * cv - cv
        plc.append(e.mean() / e.std() * math.sqrt(12))
    plc = np.array(plc)
    edge = np.log1p(rule) - np.log1p(bh)
    first = idx < pd.Period(cfg["split"], "M")
    outs = []
    for _, grp in p.groupby((p != p.shift()).cumsum()):
        if grp.iloc[0] == 0:
            a, b = grp.index[0], grp.index[-1]
            outs.append({"start": str(a), "end": str(b), "months": len(grp),
                         "stocks": float((1 + bh[a:b]).prod() - 1), "cash": float((1 + cash[a:b]).prod() - 1)})
    ex0809 = ~((idx.year >= 2008) & (idx.year <= 2009))
    return {
        "start": str(idx[0]), "end": str(idx[-1]),
        "time_in_stocks": float(p.mean()), "switches": int((p.diff().abs() > 0).sum()),
        "buy_hold": st(bh), "rule": st(rule), "mix": st(mix),
        "halves": {"h1": {"bh": st(bh[first])["cagr"], "rule": st(rule[first])["cagr"]},
                   "h2": {"bh": st(bh[~first])["cagr"], "rule": st(rule[~first])["cagr"]}},
        "ex_2008_09": {"bh": st(bh[ex0809])["cagr"], "rule": st(rule[ex0809])["cagr"]},
        "placebo_better": float((plc >= st(rule)["sharpe"]).mean()),
        "edge_by_5y": {str(k): float(v) for k, v in edge.groupby((idx.year // 5) * 5).sum().items()},
        "out_periods": outs,
        "curve": [{"m": str(k), "bh": round(float(a), 4), "rule": round(float(b), 4)}
                  for k, a, b in zip(idx, (1 + bh).cumprod(), (1 + rule).cumprod())],
        "in_cash_next": bool(sig.iloc[-1] == 2), "signal_month": str(sig.index[-1]),
    }


def sensitivity(data_dir, cfg, last):
    """Would today's cell change under reasonable alternative choices?"""
    G, I = cfg["growth"], cfg["inflation"]
    rows = []
    base = current_zone(data_dir, cfg, G, I, last)
    rows.append({"label": "As built", "zone": base[0], "gx": base[1], "iy": base[2]})
    sv = set(cfg["survey_ids"])
    z = current_zone(data_dir, cfg, [x for x in G if x[0] not in sv], I, last)
    rows.append({"label": "Without the factory survey", "zone": z[0], "gx": z[1], "iy": z[2]})
    for sm in (1, 6):
        z = current_zone(data_dir, cfg, G, I, last, smooth=sm)
        rows.append({"label": f"{sm}-month smoothing instead of {cfg['smooth_months']}", "zone": z[0], "gx": z[1], "iy": z[2]})
    for spec, which in ((G, "growth"), (I, "inflation")):
        for x in spec:
            gs = [y for y in G if y[0] != x[0]] if which == "growth" else G
            is_ = [y for y in I if y[0] != x[0]] if which == "inflation" else I
            z = current_zone(data_dir, cfg, gs, is_, last)
            rows.append({"label": f"Drop {x[4].replace(' (inverted)', '')}", "zone": z[0], "gx": z[1], "iy": z[2], "dropped": which})
    return rows


def headline_facts(data_dir, last):
    f = lambda sid: load_fred(data_dir, sid)
    yoy = lambda s: (s / s.shift(12) - 1) * 100

    def pick(s, p):
        s = s.dropna()
        s = s[s.index <= p]
        return (str(s.index[-1]), round(float(s.iloc[-1]), 2)) if len(s) else (None, None)

    rows = []
    for key, label, ser, unit in [
        ("cpi", "Consumer price inflation", yoy(f("CPIAUCSL")), "% y/y"),
        ("core", "Core inflation (ex food & energy)", yoy(f("CPILFESL")), "% y/y"),
        ("oil", "Crude oil (WTI)", f("WTISPLC"), "$/bbl"),
        ("unemp", "Unemployment rate", f("UNRATE"), "%"),
        ("jobs", "Jobs added, 3-month average", f("PAYEMS").diff().rolling(3).mean(), "k/mo"),
        ("y10", "10-year Treasury yield", f("GS10"), "%"),
        ("ffr", "Fed funds rate", f("FEDFUNDS"), "%"),
    ]:
        m_now, v_now = pick(ser, last)
        _, v_6 = pick(ser, last - 6)
        rows.append({"key": key, "label": label, "unit": unit, "now": v_now, "asof": m_now, "ago6": v_6})
    oil = f("WTISPLC")
    rows[2]["yoy"] = round(float((oil.get(last) / oil.get(last - 12) - 1) * 100), 1)
    early = []
    for sid, label in [("GACDFSA066MSFRBPHI", "Philly Fed factory activity"),
                       ("NOCDFSA066MSFRBPHI", "Philly Fed new orders"),
                       ("ICSA", "Jobless claims, weekly avg")]:
        s = f(sid).dropna()
        if s.index[-1] > last:
            early.append({"id": sid, "label": label, "month": str(s.index[-1]),
                          "value": round(float(s.iloc[-1]), 1), "prev": round(float(s.get(last)), 1)})
    return {"rows": rows, "early": early}


def renmac_checks(zone_by_month, pct_by_month):
    rows = []
    for m, said, cell, src in RENMAC_CHECKS:
        mine = zone_by_month.get(m)
        note = []
        if mine:
            if mine[1] != cell[1]:
                note.append("this clock reads growth " + ("stronger" if mine[1] > cell[1] else "weaker"))
            if mine[0] != cell[0]:
                note.append("this clock reads inflation " + ("hotter" if mine[0] > cell[0] else "cooler"))
        rows.append({"m": m, "said": said, "cell": cell, "src": src, "mine": mine,
                     "same_cell": mine == cell, "same_row": bool(mine) and mine[0] == cell[0],
                     "gx": pct_by_month.get(m, (None, None))[0], "iy": pct_by_month.get(m, (None, None))[1],
                     "note": "; ".join(note)})
    return rows


# --------------------------------------------------------------------------
# main
# --------------------------------------------------------------------------
def run(data_dir="data", out_json="output/clock.json"):
    cfg = CONFIG
    start = pd.Period(cfg["start"], "M")

    g, gparts, gfresh = build_hand(data_dir, cfg["growth"], cfg)
    i, iparts, ifresh = build_hand(data_dir, cfg["inflation"], cfg)
    idx = g.index.intersection(i.index)
    g, i = g[idx], i[idx]
    keep = (idx >= start) & g.notna().values & i.notna().values
    g, i = g[keep], i[keep]
    need_g, need_i = math.ceil(len(cfg["growth"]) / 2), math.ceil(len(cfg["inflation"]) / 2)
    last = g.index.max()
    while gfresh.get(last, 0) < need_g or ifresh.get(last, 0) < need_i:
        last -= 1
    g, i = g[:last], i[:last]

    gx, zc, gcut = rank_thirds(g)
    iy, zr, icut = rank_thirds(i)
    zkey = (zr.astype(int).astype(str) + zc.astype(int).astype(str))
    first = pd.Series(g.index < pd.Period(cfg["split"], "M"), index=g.index)

    mkt = load_market(data_dir)
    mk = Market(mkt, g.index, cfg, data_dir)
    zones = summarize(mk, zkey, zr, zc, first, cfg)

    # real-time rebuild: expanding z-scores and expanding cut points, from the earliest usable data
    rts = pd.Period(cfg["rt_start"], "M")
    g_rt, _, _ = build_hand(data_dir, cfg["growth"], cfg, realtime=True)
    i_rt, _, _ = build_hand(data_dir, cfg["inflation"], cfg, realtime=True)
    ridx = g_rt.index.intersection(i_rt.index)
    g_rt, i_rt = g_rt[ridx], i_rt[ridx]
    ok = (ridx >= rts) & (ridx <= last) & g_rt.notna().values & i_rt.notna().values
    g_rt, i_rt = g_rt[ok], i_rt[ok]
    zc_rt = expanding_terciles(g_rt, cfg["oos_min_months"])
    zr_rt = expanding_terciles(i_rt, cfg["oos_min_months"])
    m_rt = zc_rt.notna() & zr_rt.notna()
    zkey_rt = (zr_rt[m_rt].astype(int).astype(str) + zc_rt[m_rt].astype(int).astype(str))
    mk_rt = Market(mkt, zkey_rt.index, cfg, data_dir)
    rt_cells = nw_cells(mk_rt.nxt, zkey_rt, 2, 12)
    rt_f12 = nw_cells(mk_rt.f12, zkey_rt, 12)
    rt_all = float(mk_rt.nxt.mean() * 12)
    for k in KEYS:
        zones[k]["ret_rt"] = rt_cells[k]["mean"]
        zones[k]["f12_rt"] = rt_f12[k]["mean"]
        zones[k]["months_rt"] = rt_cells[k]["n"]
    common = zkey.index.intersection(zkey_rt.index)
    agree = float((zkey[common] == zkey_rt[common]).mean())
    rt_axis = {"1m": nw_gap(mk_rt.nxt, zr_rt[m_rt], 2, 12), "12m": nw_gap(mk_rt.f12, zr_rt[m_rt], 12, 1)}

    first_rt = pd.Series(zkey_rt.index < pd.Period(cfg["split"], "M"), index=zkey_rt.index)
    bt = backtest(zr_rt, mkt, cfg)
    usrec = load_fred(data_dir, "USREC")

    series = [{"m": str(p), "g": round(float(g[p]), 3), "i": round(float(i[p]), 3),
               "x": round(float(gx[p]), 4), "y": round(float(iy[p]), 4),
               "z": zkey[p], "rec": int(usrec.get(p, 0) or 0)} for p in g.index]

    def latest_inputs(parts, spec):
        rows = []
        for sid, _, _, _, label, desc in spec:
            s = parts[sid]
            asof = load_fred(data_dir, sid).index.max()
            rows.append({"id": sid, "label": label, "desc": desc, "asof": str(min(asof, last)),
                         "carried": bool(asof < last), "z": round(float(s.get(last)), 2),
                         "z_6m": round(float(s.get(last - 6)), 2)})
        return rows

    ret_all = nw_means(mk.nxt, pd.Series(0, index=mk.nxt.index), [0], 2)
    se_all = math.sqrt(ret_all["V"][0, 0]) * 12
    all_ = {"ret": {"mean": float(mk.nxt.mean() * 12), "lo": float(mk.nxt.mean() * 12 - 1.645 * se_all),
                    "hi": float(mk.nxt.mean() * 12 + 1.645 * se_all)},
            "ex": float(mk.ex.mean() * 12), "cash": float(mk.cash.mean() * 12),
            "hit": float((mk.nxt.dropna() > 0).mean()), "vol": float(mk.nxt.std() * math.sqrt(12)),
            "f12_mean": float(mk.f12.mean()), "f12_med": float(mk.f12.median()),
            "fall10": float((mk.dd12.dropna() <= -0.10).mean()), "fall20": float((mk.dd12.dropna() <= -0.20).mean()),
            "gs10_6m": float(mk.gs10_6m.mean()), "gs2_6m": float(mk.gs2_6m.mean()), "ff_6m": float(mk.ff_6m.mean()),
            "months": int(len(g)), "rt_ret": rt_all, "rt_f12": float(mk_rt.f12.mean()),
            "f12_ex": float(mk.f12_ex.mean()),
            "f12_h1": float(mk.f12[first].mean()), "f12_h2": float(mk.f12[~first].mean()),
            "h1": float(mk.nxt[first].mean() * 12), "h2": float(mk.nxt[~first].mean() * 12)}

    cur = series[-1]
    out = {
        "generated": pd.Timestamp.now(tz="UTC").strftime("%Y-%m-%d"),
        "latest_month": cur["m"], "current": cur,
        "returns_through": str(mkt.index.max()),
        "cutpoints": {"growth": gcut, "inflation": icut},
        "all": all_, "zones": zones,
        "axes": {"inflation": axis_tests(mk, zr, first), "growth": axis_tests(mk, zc, first)},
        "realtime": {"agree": agree, "start": str(zkey_rt.index[0]), "inflation_axis": rt_axis,
                     "current": zkey_rt.get(last)},
        "transitions_6m": transitions(zr, zc),
        "episodes": episodes(zkey, mk),
        "spell_mean": float(zkey.groupby((zkey != zkey.shift()).cumsum()).size().mean()),
        "backtest": bt,
        "sensitivity": sensitivity(data_dir, cfg, last),
        "facts": headline_facts(data_dir, last),
        "checks": renmac_checks({r["m"]: r["z"] for r in series}, {r["m"]: (r["x"], r["y"]) for r in series}),
        "inputs": {"growth": latest_inputs(gparts, cfg["growth"]),
                   "inflation": latest_inputs(iparts, cfg["inflation"])},
        "industries": INDUSTRY_LABELS, "cyclicals": cfg["cyclicals"], "defensives": cfg["defensives"],
        "series": series,
        "config": {k: v for k, v in cfg.items() if k not in ("growth", "inflation")},
    }
    os.makedirs(os.path.dirname(out_json) or ".", exist_ok=True)
    with open(out_json, "w") as f:
        json.dump(out, f, separators=(",", ":"))
    return out


if __name__ == "__main__":
    d = sys.argv[1] if len(sys.argv) > 1 else "data"
    o = sys.argv[2] if len(sys.argv) > 2 else "output/clock.json"
    res = run(d, o)
    c = res["current"]
    z = res["zones"][c["z"]]
    pc = lambda v: "  n/a" if v is None else f"{v * 100:5.1f}%"
    print(f"\nLatest reading {c['m']}: {z['name']} ({z['long']}); growth pct {c['x']:.0%}, inflation pct {c['y']:.0%}")
    for lab, f in (("Next 12 months, average", lambda z: z["f12"]["mean"]), ("Next month, yearly pace", lambda z: z["ret"]["mean"])):
        print(f"\n{lab} (all months {pc(res['all']['f12_mean'] if 'Next 12' in lab else res['all']['ret']['mean'])}):")
        for r in (2, 1, 0):
            print("  " + " | ".join(f"{res['zones'][f'{r}{k}']['name'][:18]:>18s} {pc(f(res['zones'][f'{r}{k}']))}" for k in range(3)))
    for ax in ("inflation", "growth"):
        h = res["axes"][ax]["horizons"]
        print(f"{ax:9s} low-minus-high: " + "  ".join(
            f"{k} {h[k]['total']['gap'] * 100:+.1f}% (t {h[k]['total']['t']:.2f}), over cash t {h[k]['excess']['t']:.2f}"
            for k in h))
    b = res["backtest"]
    print(f"\nReal-time zones from {res['realtime']['start']}; agree {res['realtime']['agree']:.0%}. "
          f"Rule {b['start']}..{b['end']}: {b['rule']['cagr']:.1%} vs {b['buy_hold']['cagr']:.1%}, "
          f"drawdown {b['rule']['maxdd']:.0%} vs {b['buy_hold']['maxdd']:.0%}, placebo {b['placebo_better']:.1%}")
    print("Sensitivity of today's cell:", ", ".join(f"{s['label']}={s['zone']}" for s in res["sensitivity"][:4]),
          "| leave-one-out cells:", sorted(set(s["zone"] for s in res["sensitivity"][4:])))
    ch = res["checks"]
    print(f"RenMac: same cell {sum(c['same_cell'] for c in ch)}/{len(ch)}, same row {sum(c['same_row'] for c in ch)}/{len(ch)}")
