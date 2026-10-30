"""Download the public inputs for the open-source Market Cycle Clock.

Sources (no API keys needed):
  * FRED (St. Louis Fed) series via the public fredgraph.csv endpoint
  * Ken French Data Library: market factor (CRSP value-weighted total return)
    and 12 value-weighted industry portfolios

Usage:  python fetch_data.py            # writes into ./data/
"""
from __future__ import annotations

import io
import os
import sys
import urllib.request
import zipfile

FRED_IDS = [
    # growth
    "INDPRO", "PAYEMS", "UNRATE", "W875RX1", "CMRMTSPL", "HOUST", "ICSA",
    "GACDFSA066MSFRBPHI", "NOCDFSA066MSFRBPHI",   # Philly Fed manufacturing survey
    # inflation
    "CPIAUCSL", "CPILFESL", "PPIACO", "WTISPLC", "AHETPI",
    # rates / context
    "GS10", "GS2", "FEDFUNDS", "USREC", "GDPC1",
]
FRENCH = {
    "factors": "https://mba.tuck.dartmouth.edu/pages/faculty/ken.french/ftp/F-F_Research_Data_Factors_CSV.zip",
    "industries": "https://mba.tuck.dartmouth.edu/pages/faculty/ken.french/ftp/12_Industry_Portfolios_CSV.zip",
}
INDUSTRIES = ["NoDur", "Durbl", "Manuf", "Enrgy", "Chems", "BusEq", "Telcm",
              "Utils", "Shops", "Hlth", "Money", "Other"]
UA = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36 open-cycle-clock",
      "Accept": "text/csv,text/plain,application/zip,*/*"}
LOG = []


def log(msg):
    print(msg, flush=True)
    LOG.append(msg)


def _get(url: str, tries: int = 4) -> bytes:
    import time
    last = None
    for k in range(tries):
        try:
            req = urllib.request.Request(url, headers=UA)
            with urllib.request.urlopen(req, timeout=60) as r:
                return r.read()
        except Exception as e:  # network hiccup, 429, 5xx
            last = e
            time.sleep(3 * (k + 1))
    raise last


def _valid_csv(txt: str, sid: str) -> bool:
    lines = txt.strip().splitlines()
    return len(lines) > 20 and lines[0].lower().startswith(("observation_date", "date")) and lines[-1][:4].isdigit()


def _fred_txt_to_csv(txt: str, sid: str) -> str:
    """https://fred.stlouisfed.org/data/<ID>.txt → the same CSV layout as fredgraph.csv."""
    import re
    rows = [f"observation_date,{sid}"]
    for line in txt.splitlines():
        m = re.match(r"^(\d{4}-\d{2}-\d{2})\s+(\S+)\s*$", line.strip())
        if m and m.group(1) >= "1955-01-01":
            rows.append(f"{m.group(1)},{'' if m.group(2) == '.' else m.group(2)}")
    return "\n".join(rows)


def _first_monthly_block(text: str) -> list[list[str]]:
    """French CSVs hold several tables; the first YYYYMM block is monthly."""
    rows, started = [], False
    for line in text.splitlines():
        head = line.split(",")[0].strip()
        if len(head) == 6 and head.isdigit():
            rows.append([c.strip() for c in line.split(",")])
            started = True
        elif started:
            break
    return rows


def fetch_fred(outdir: str) -> int:
    """Download each series, trying three routes. A series that fails keeps its previous file."""
    import json as _json
    os.makedirs(os.path.join(outdir, "fred"), exist_ok=True)
    key = os.environ.get("FRED_API_KEY")
    failed = 0
    for sid in FRED_IDS:
        routes = []
        if key:
            routes.append(("api", f"https://api.stlouisfed.org/fred/series/observations?series_id={sid}"
                                  f"&api_key={key}&file_type=json&observation_start=1955-01-01"))
        routes += [("csv", f"https://fred.stlouisfed.org/graph/fredgraph.csv?id={sid}&cosd=1955-01-01"),
                   ("txt", f"https://fred.stlouisfed.org/data/{sid}.txt")]
        ok, errs = False, []
        for name, url in routes:
            try:
                raw = _get(url).decode("utf-8", "replace")
                if name == "api":
                    obs = _json.loads(raw)["observations"]
                    txt = f"observation_date,{sid}\n" + "\n".join(
                        f"{o['date']},{'' if o['value'] == '.' else o['value']}" for o in obs)
                elif name == "txt":
                    txt = _fred_txt_to_csv(raw, sid)
                else:
                    txt = raw
                if not _valid_csv(txt, sid):
                    raise ValueError(f"unexpected content: {raw[:80]!r}")
                with open(os.path.join(outdir, "fred", f"{sid}.csv"), "w") as f:
                    f.write(txt.strip() + "\n")
                log(f"  FRED {sid:9s} {len(txt.splitlines()) - 1:5d} obs via {name}")
                ok = True
                break
            except Exception as e:
                errs.append(f"{name}: {type(e).__name__}: {str(e)[:120]}")
        if not ok:
            failed += 1
            log(f"  FRED {sid:9s} FAILED, kept previous file ({'; '.join(errs)})")
    return failed


def fetch_french(outdir: str) -> int:
    try:
        _fetch_french(outdir)
        return 0
    except Exception as e:
        log(f"  French FAILED, kept previous file ({type(e).__name__}: {str(e)[:160]})")
        return 1


def _fetch_french(outdir: str) -> None:
    def unzip(url):
        z = zipfile.ZipFile(io.BytesIO(_get(url)))
        return z.read(z.namelist()[0]).decode("latin-1")

    ff = {r[0]: r for r in _first_monthly_block(unzip(FRENCH["factors"]))}
    ind = _first_monthly_block(unzip(FRENCH["industries"]))
    lines = ["ym,mkt,rf," + ",".join(INDUSTRIES)]
    for r in ind:
        if int(r[0]) < 195901 or r[0] not in ff:
            continue
        f = ff[r[0]]
        mkt = float(f[1]) + float(f[4])          # Mkt-RF + RF = total market return
        lines.append(",".join([r[0], f"{mkt:.2f}", f[4]] + r[1:13]))
    with open(os.path.join(outdir, "french_monthly.csv"), "w") as fh:
        fh.write("\n".join(lines) + "\n")
    log(f"  French   {len(lines) - 1} months ({lines[1][:6]}-{lines[-1][:6]})")


VINTAGE_IDS = ["PAYEMS", "INDPRO", "HOUST", "UNRATE", "W875RX1", "CMRMTSPL", "AHETPI",
               "CPIAUCSL", "CPILFESL", "PPIACO", "GACDFSA066MSFRBPHI", "NOCDFSA066MSFRBPHI"]


def fetch_vintage(outdir: str, key: str) -> None:
    """First-release ("initial print") values via the FRED API (output_type=4). Needs a free key:
    https://fred.stlouisfed.org/docs/api/api_key.html . Saved to data/vintage/<ID>.csv and used by audit.py."""
    import json as _json
    os.makedirs(os.path.join(outdir, "vintage"), exist_ok=True)
    for sid in VINTAGE_IDS:
        url = ("https://api.stlouisfed.org/fred/series/observations?series_id=" + sid +
               f"&api_key={key}&file_type=json&output_type=4&observation_start=1955-01-01")
        try:
            obs = _json.loads(_get(url))["observations"]
        except Exception as e:  # some series have no vintage history
            print(f"  vintage {sid}: skipped ({e})"); continue
        rows = ["observation_date," + sid] + [f"{o['date']},{o['value']}" for o in obs if o["value"] != "."]
        with open(os.path.join(outdir, "vintage", f"{sid}.csv"), "w") as f:
            f.write("\n".join(rows) + "\n")
        print(f"  vintage {sid:9s} {len(rows) - 1:5d} first releases")


if __name__ == "__main__":
    if "--vintage" in sys.argv:
        key = os.environ.get("FRED_API_KEY")
        if not key:
            sys.exit("Set FRED_API_KEY to download first-release data.")
        fetch_vintage(sys.argv[1] if len(sys.argv) > 2 and not sys.argv[1].startswith("-") else "data", key)
        sys.exit()
    out = sys.argv[1] if len(sys.argv) > 1 and not sys.argv[1].startswith("-") else "data"
    from datetime import datetime, timezone
    log(f"Fetch run {datetime.now(timezone.utc):%Y-%m-%d %H:%M UTC}")
    log("Fetching FRED"); nf = fetch_fred(out)
    log("Fetching Ken French library"); nk = fetch_french(out)
    log(f"Done: {len(FRED_IDS) - nf}/{len(FRED_IDS)} FRED series, French {'ok' if not nk else 'failed'}")
    os.makedirs("output", exist_ok=True)
    with open(os.path.join("output", "fetch_log.txt"), "w") as fh:
        fh.write("\n".join(LOG) + "\n")
    sys.exit(2 if nf > 5 else 0)   # many failures: stop the run; a few: carry on with previous files
