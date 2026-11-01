# Open Cycle Clock

An open-source rebuild of the **Market Cycle Clock** that Renaissance Macro Research (RenMac) publishes monthly. Public data only, every input and setting in one config block, MIT licensed. Not affiliated with RenMac. Not investment advice.

## In one paragraph

Every month since mid-1968 is placed on a 3×3 grid by two questions: is the economy growing fast or slowly (left to right), and is inflation speeding up or slowing down (bottom to top)? Each cell then shows what US stocks, cash, industries, bond yields and the Fed did afterwards. **Cooling inflation has been followed by better stock returns than heating inflation**, most clearly over one to six months, and a simple real-time rule that stepped into cash when inflation heated up matched buy-and-hold returns with a much smaller worst fall. Over a full year, weak growth has been followed by better returns than strong growth about as reliably. Few individual results clear a 2-standard-error bar, the inflation effect is far stronger since 1995 than before, and single-cell averages are noisy.

## What RenMac has said about its clock

| Feature | What's public | Source |
|---|---|---|
| Axes | Inflation vertical, growth horizontal | Mauldin Economics interview, Dec 2025 |
| Zones | Each axis divided into thirds → nine zones | EFG *Beyond the Benchmark*, Dec 2024 |
| History | Zone statistics back to 1964 | RenMac Tech deep dive, May 2022 |
| Cadence | Updated monthly | RenMac on X, Oct 2025 |
| Inflation input | Not CPI-driven; energy prices are a large component; **direction** matters more than level | EFG, Dec 2024 |
| Input selection | Inputs judged by their effect on the S&P 500, not on the economy | EFG, Dec 2024 |
| Growth vs inflation | Inflation matters more to stock returns than growth; weak growth often precedes better returns because it invites policy support | Mauldin Economics, Dec 2025 |
| Outputs | Annualized S&P returns per zone, 6-month sector returns, 10-year yield behaviour, cyclicals vs defensives | Various |

## How this version works

**Growth hand.** The average z-score of nine inputs: industrial production, payrolls, real income excluding transfers, real business sales and housing starts (each year-over-year); the 12-month change in unemployment and in jobless claims (both inverted); and the Philadelphia Fed manufacturing survey's activity and new-orders readings (3-month average).

**Inflation hand.** This measures direction, not level. It is the average z-score of:
- WTI crude oil, year-over-year
- the 6-month change in the year-over-year rate of PPI commodities, headline CPI, core CPI and hourly earnings

**Data handling**
- Each input is z-scored and winsorised at ±4σ.
- Every input has a publication lag. Real business sales publish about two months late, so they enter a month late.
- When an input hasn't reported yet, its last value carries forward for up to two months. The latest month is scored only when at least half of each hand's inputs are fresh.
- Each hand is smoothed over 3 months and then rescaled.
- Cells are the hand's historical thirds. They are assigned by rank, so a month's dot and its cell always agree.

**Scoring**
- Stocks are the CRSP value-weighted total return; cash is the 1-month T-bill. Both come from the Ken French library.
- A month-*t* reading is treated as known at the end of *t+1*, so its returns start in *t+2*.
- For each cell the model reports:
  - next-12-month average, median and 10th/90th percentiles
  - return over cash
  - next-month return at a yearly pace
  - industry returns relative to the market
  - cyclicals minus defensives
  - 6-month changes in the 10-year yield and the Fed funds rate
  - a 6-month transition matrix
  - past stretches in the cell
- Every average over overlapping windows carries a Newey-West standard error.

**Robustness**
- **Axis tests** at 1, 6 and 12 months (low third minus high third, Newey-West t), split before and after 1995.
- **Real-time rebuild** from 1971. It uses expanding-window z-scores and expanding cut points, so there is no hindsight.
- **Trading test.** Hold stocks, but switch to T-bills when the real-time inflation row reads "heating". It is compared against:
  - buy-and-hold
  - a fixed mix with the same average stock weight
  - 2,000 placebo runs that shift the rule's in/out timing
- **Sensitivity check on today's cell.** The latest month is rebuilt with each input dropped, without the survey, and with 1- or 6-month smoothing.

## Results snapshot (data through Sep 2026; run `clock.py` for current figures)

US stock return over the next 12 months (all months: 12.2%):

| | Weak growth | Average growth | Strong growth |
|---|---|---|---|
| **Inflation heating** | 15.4% Stagflation | 8.0% Late-cycle squeeze | 3.7% Overheating |
| **Inflation steady** | 12.5% Soft patch | 16.0% Steady state | 12.3% Expansion |
| **Inflation cooling** | 18.1% Cooling slowdown | 12.7% Goldilocks | 11.7% Cool boom |

Low third minus high third, % a year (Newey-West t):

| Hand | 1 month | 6 months | 12 months |
|---|---|---|---|
| Inflation (cooling − heating) | +9.5 (1.7) | +9.6 (2.1) | +6.6 (1.6) |
| Growth (weak − strong) | +3.9 (0.7) | +6.2 (1.4) | +7.3 (1.6) |

Real-time trading rule, Aug 1971 to Sep 2026:

| | Return per year | Worst fall from a peak |
|---|---|---|
| Rule | 12.2% | −38% |
| Buy-and-hold | 11.3% | −50% |

- Only 0.5% of the placebo runs did as well.
- Most of the edge comes from three cash spells: 1973–75, 2008 and 2021–22. Excluding 2008–09, the rule earned 12.1% vs 12.2% for buy-and-hold.

**Latest reading (Sep 2026): Late-cycle squeeze.** Inflation is heating (76th percentile) and growth is average (54th).
- 15 of 17 alternative builds agree.
- Without the Philadelphia Fed survey, the reading becomes Stagflation.
- Without PPI, it becomes Steady state.

## Practitioner's audit (v4)

`audit.py` runs the tests a quantitative analyst would demand before trusting the clock, mostly by comparing it with **placebo clocks**: the same zone history slid to 2,000 random start dates, so zones keep their lengths but lose their timing. Results (data through Sep 2026):

| Grade | Test | Result |
|---|---|---|
| Strong | Crash risk when inflation heats | A 10%+ fall within a year followed 53% of heating months vs 30% of cooling ones (placebo p 0.004); holds before 1995 (61/20), since (46/39) and in real time (52/34) |
| Strong | Real-time rule robustness | Smaller worst fall than buy-and-hold in 12 of 12 rebuilds (lag × smoothing × start); return matched or beat in 12 of 12; worst placebo p 0.015 |
| Confirmed | Direction vs level | Inflation-direction correlation with next-year returns −0.11 vs −0.04 for the CPI level, as RenMac says |
| Good | Input signs | All 14 inputs have the "bad news is good news" sign; 10 keep it in both halves |
| Mixed | Growth hand | Weak growth → better next year (rank correlation −0.23, p 0.02), but driven by 1968–74; from 1975 t ≈ 0.6 |
| Weak | Three inflation rows vs random timing | Placebo p 0.12 at 12 months, 0.07 at 6 |
| Fails | Nine cells beyond the rows | Placebo p 0.27: single-cell returns are colour, not signal |
| Fails | Return forecasting | Out-of-sample R² −5.7% (12 months) vs the historical mean: a risk gauge, not a return forecaster |
| Fixed | Cyclical/defensive groups | Retail tracks GDP *negatively* (−0.18), so it was removed from the cyclicals |
| Open | First-release data | Needs a free FRED API key: `FRED_API_KEY=… python fetch_data.py --vintage`, then `audit.py` adds the test |

**Changes made from the audit:** crash odds (10%+ fall within a year) per cell and row, now on the dial, in the cell readout and in the headline; a report-card section on the page; the 2-year Treasury yield (which deGraaf watches more than the Fed funds rate); cyclicals re-chosen by relative return vs forward real GDP; first-release data support; a weekly update with a "since the last update" log.

## Data, sources and the professional standard (v5 audit)

A Wall Street buyer's checks, run in `clock.py` (`backtest.vs`) and `audit.py` (`benchmarks`, `data`), and shown on the page:

| Check | Result |
|---|---|
| Data integrity | No duplicate dates or stray gaps; every large jump is a real event (1974 oil, 2013 income shift, 2020). Oct 2025 CPI, core CPI and unemployment were never collected (shutdown); single-month holes are now filled by log-linear interpolation and flagged on the page. |
| vs a 10-month moving-average trend rule, after 0.10% per switch | Inflation rule 12.1% a year, Sharpe 0.64, worst fall −38%; trend rule 10.2%, 0.52, −24%; buy-and-hold 11.3%, 0.49, −50%. Cash if either warns: 9.9%, worst fall −18%. |
| Does inflation add to trend? | With the trend *up*, a 10%+ fall within a year followed 49% of heating months vs 27% of others (placebo p ≈ 0.001). Only 37% of the rule's cash months overlap the trend rule's. |
| Growth hand vs Chicago Fed National Activity Index | Correlation 0.92 with its 12-month average, 0.56 with its 3-month average: right signal, slower at turns. |
| Recessions | 94% of NBER recession months fall in the weak-growth third; none in the strong third. |
| Survey breadth | The Empire State survey is fetched to check whether the Philadelphia survey is representative. |

**Commercial readiness.** FRED's terms allow personal, non-commercial use, and its WTI series (`WTISPLC`) is copyrighted; Ken French returns are CRSP-derived. A commercial version would (1) test on first-release data (add a free `FRED_API_KEY` repository secret; the workflow and `audit.py` then run it), (2) fetch each series from its original agency, use EIA's public WTI price (1986 on) plus licensed earlier history, and license CRSP or S&P returns, (3) average several regional Fed surveys, (4) build a live track record (the weekly commits are timestamped from Oct 2026), and (5) be positioned as a crash-risk gauge, not a return forecaster.

## Weekly updates

**GitHub Actions does the work.** `.github/workflows/weekly.yml` runs every Saturday at 09:47 Tokyo time (00:47 UTC), after the week's US releases, and can also be run by hand from the Actions tab. It:
1. downloads every FRED series and the Ken French stock returns directly with `fetch_data.py`, plus first-release data if a `FRED_API_KEY` repository secret is set;
2. reruns `clock.py`, `audit.py` and `build_page.py`, with a "since the last update" log;
3. runs `tools/check_outputs.py`, which stops the run if the latest month goes backwards, an input is more than three months stale, or the history looks truncated;
4. commits the new `data/`, `output/` and `docs/index.html`. `docs/` is the GitHub Pages site.

The site is at https://mrtylerhorn-lgtm.github.io/mrtylerhorn-lgtm-open-cycle-clock/ once Pages is switched on (Settings → Pages → Deploy from a branch → `main`, `/docs`). A small Claude scheduled task (Saturday 11:13 Tokyo) then copies the latest build from the repository to the Claude page. If the Action didn't run, the task falls back to the cloud-only route below. RenMac publishes its own clock monthly. The weekly run catches each monthly input as it lands, plus weekly jobless claims. A new monthly *reading* appears once at least half of each hand's inputs are out.

**Fallback.** If GitHub is unavailable, `UPDATE.md` describes a cloud-only route that reads FRED's text pages with WebFetch and merges them with `tools/merge_fred_tail.py`.

## Audit log

**v3**
- **Inflation-axis claim corrected.** The effect is much stronger after 1995. Before 1995, heating-inflation months had about average next-month returns. The page text is now generated from the data.
- **Newey-West standard errors** on every overlapping-window statistic, including the industry bars, cyclicals vs defensives, yields and the Fed rate. Results within the range of luck are faded.
- **Headline horizon is now the next 12 months.** It is shown with its mean, median, 10th/90th percentiles and a confidence range. The next-month figure is labelled as a yearly pace.
- **Excess returns over cash** added. Heating-inflation months beat cash by only about 1% a year at the 1-month horizon.
- **Axis tests at 1, 6 and 12 months.** These show the growth hand matters at 12 months.
- **Release lag set per input** (real business sales lag a month). Hands are rescaled after averaging. Scoring starts in 1968-07, when every input exists. Zones are assigned by rank, so a dot and its cell always agree.
- **Real-time rebuild extended back to 1971.** Its column is now coloured against its own average, and it has its own axis test.
- **Trading test added**, with a fixed-mix comparison, placebo runs and an episode breakdown.
- **Sensitivity check on today's reading.** It found that the current cell depends on the factory survey.
- **RenMac comparison computed per check, with the direction of each miss.** The earlier hardcoded claim that "RenMac reads growth hotter" was wrong. The misses run in both directions.
- **Cell blurbs no longer contain data claims.** All numeric copy is generated.
- **Accessibility:** the dial is a group of buttons, a single live region replaces the whole-panel announcement, the slider reads out the month, and ▲/▼ markers sit alongside colour.
- **Layout:** the dial labels sit above the trail, the history chart's y-axis stays pinned, the axis charts resize on phones, and the RenMac checks are shown as cards.

**v2**
- Removed hindsight from the real-time test, fixed the ragged edge and a smoothing bug, and added confidence intervals and a split-half test.
- Added the Philadelphia Fed survey. It moved this clock closer to RenMac's public readings, which is a mild form of fitting, so it is disclosed on the page.
- Corrected the rotation direction (counterclockwise in this layout).

## Run it

```bash
pip install -r requirements.txt
python fetch_data.py        # FRED + Ken French → ./data
python clock.py             # → output/clock.json; prints the reading and tests
python audit.py             # → output/audit.json; the practitioner's report card (~30 s)
python build_page.py --prev last_week_clock.json   # → output/cycle_clock.html, with a change log
```

Change inputs, publication lags, smoothing, the release lag or the split year in `CONFIG` at the top of `clock.py`. `tools/shoot.py` renders the page with Playwright for visual checks.

## Caveats

- This is history, not a forecast.
- The data are today's revised figures, not the first-published vintages. ALFRED would allow a true real-time test.
- The main view's cut points use hindsight. The real-time column and the trading test do not.
- Nine cells, three horizons and two hands make dozens of comparisons, so some results will look strong by chance.

Data: FRED (Federal Reserve Bank of St. Louis); Kenneth R. French Data Library.
