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
UA = {"User-Agent": "open-market-cycle-clock/1.0"}


def _get(url: str) -> bytes:
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=60) as r:
        return r.read()


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


def fetch_fred(outdir: str) -> None:
    os.makedirs(os.path.join(outdir, "fred"), exist_ok=True)
    for sid in FRED_IDS:
        url = f"https://fred.stlouisfed.org/graph/fredgraph.csv?id={sid}&cosd=1955-01-01"
        txt = _get(url).decode()
        with open(os.path.join(outdir, "fred", f"{sid}.csv"), "w") as f:
            f.write(txt.strip() + "\n")
        print(f"  FRED {sid:9s} {len(txt.splitlines()) - 1:5d} obs")


def fetch_french(outdir: str) -> None:
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
    print(f"  French   {len(lines) - 1} months ({lines[1][:6]}–{lines[-1][:6]})")


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
    out = sys.argv[1] if len(sys.argv) > 1 else "data"
    print("Fetching FRED…"); fetch_fred(out)
    print("Fetching Ken French library…"); fetch_french(out)
    print("Done.")
