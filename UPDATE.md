# Weekly update runbook

## Normal route: GitHub Actions

Repository: https://github.com/mrtylerhorn-lgtm/mrtylerhorn-lgtm-open-cycle-clock

`.github/workflows/weekly.yml` runs every Saturday at 00:47 UTC (09:47 Tokyo). Run it by hand from the Actions
tab with "Run workflow". It downloads everything directly (`fetch_data.py`), rebuilds the clock, audit and page,
runs `tools/check_outputs.py`, and commits `data/`, `output/` and `docs/index.html`. The GitHub Pages site is
served from `docs/`. If too many downloads fail, it commits only `output/fetch_log.txt` and marks the run failed.

A Claude scheduled task runs an hour later. It clones the repository, publishes `output/cycle_clock.html` and the
data to the Claude page, and reports what changed. It never fetches data itself.

## Fallback route: cloud only, no GitHub (no computer needed)

The clock is refreshed weekly. Monthly inputs land at different times (jobs: first Friday; CPI and industrial
production: mid-month; Philly Fed: third Thursday; income and spending: month-end), and jobless claims are weekly.
A new monthly *reading* appears once at least half of each hand's inputs for that month are out, usually late in
the following month. In between, the weekly run picks up revisions, early data and the "since last update" notes.

## How data gets in

| Source | Route | Notes |
|---|---|---|
| FRED, monthly/quarterly series | WebFetch `https://fred.stlouisfed.org/data/<ID>.txt`, last 18 lines | `tools/merge_fred_tail.py` folds them into the stored history |
| FRED, weekly jobless claims (ICSA) | WebFetch `https://fred.stlouisfed.org/series/ICSA` (latest 5 weeks) | the .txt file is too long to read whole |
| Ken French stock returns | `python fetch_data.py` if the shell can reach it; otherwise keep last week's file | monthly, about a month behind; only affects historical return statistics, not the reading |

If the account's allowed-domains list includes `fred.stlouisfed.org` and `mba.tuck.dartmouth.edu`, `python fetch_data.py`
downloads everything directly and WebFetch is not needed.

## Safety checks in the merge

The WebFetch tool reads pages with a helper model, which can occasionally drop a digit or shift a column by a row.
`merge_fred_tail.py` refuses a series if:
- its dates have gaps that FRED doesn't have, or are out of order;
- it ends before the stored data (a cut-off page);
- it matches the stored data better when shifted by one row (a misread);
- its decimals differ from the series' usual format;
- a revision is implausibly large;
- more than 3 values are revised at once, unless an identical second read confirms them (`--confirmed`).

Refused series keep last week's data. The clock carries an input forward for up to two months, so a missed series
rarely changes the reading.

## Steps

1. Read the artifact's `src/*`, `src/web/template.html`, `src/tools/*`, `data/clock.json` and `data/**` into
   `occ/`; keep `data/clock.json` as `prev_clock.json`.
2. Try `python fetch_data.py data`. If it is blocked, run WebFetch per the table and write all blocks to `tails.txt`
   (`## <ID>` then `DATE VALUE` lines, copied exactly), then `python -I tools/merge_fred_tail.py tails.txt data`.
   For any series refused with "read the series again", fetch it a second time into `tails2.txt` and rerun with
   `--confirmed tails2.txt`. For a misread or cut-off refusal, fetch once more and retry; if it still fails, leave it.
3. `python clock.py data output/clock.json && python audit.py data output/audit.json && python build_page.py --prev prev_clock.json`.
4. Check the latest month did not go backwards, input `asof` dates are within 3 months, the page renders with no errors.
5. Publish to the same artifact URL with the refreshed `data/` files.
6. Report in 2–3 sentences: the cell, whether it moved, which data arrived, anything refused.
