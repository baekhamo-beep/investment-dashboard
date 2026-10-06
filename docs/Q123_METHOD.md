# Q123 canonical v2

This repository uses `q123_engine.py` for both historical results and live signals.
The static case study and performance tables are generated, not independently maintained.

## Rules

| Item | Definition |
| --- | --- |
| Positions | BEAR = QQQ; NORMAL = QLD; BOOST = TQQQ; 100% allocation |
| Indicators | Unadjusted QQQ closing prices; SMA50/200; C[t]/C[t-63]-1 and C[t]/C[t-126]-1 |
| BEAR entry | Close < SMA200, priority over every other rule |
| BEAR exit | Five consecutive closes strictly above SMA200 and Close > SMA50 > SMA200 |
| BOOST entry | Alignment plus RET63 >= 10% and RET126 >= 0; no cooldown |
| Direct entry | BEAR → BOOST when both recovery and BOOST conditions hold |
| BOOST exit | Close < SMA50, close <= 92% of entry-period closing peak, or 63 holding sessions |
| Holding count | Entry session is day 1; day 63 close signals exit at day 64 open |
| Peak | Starts with entry session close; no pre-entry close or intraday high |
| Cooldown | Any BOOST exit, including exit to BEAR, starts cooldown at execution session E |
| Reentry | No TQQQ during E+1 through E+21; signal permitted at E+21 close, execution at E+22 open |
| Execution | Signal on completed session D close; switch at next actual session D+1 open |
| Initialization | NORMAL at each simulation start; no pre-start strategy position |
| Equality | Close = SMA200 does not enter BEAR and does not extend the recovery streak |

The E+21 close/E+22 open convention explicitly resolves the prior phrase “21 days after exit.”
It can differ by one session from an engine that prohibits signals through E+21.
Do not reuse figures calculated with that alternative convention.

## Data and return calculation

- User-supplied Investing CSVs, cleaned against `exchange_calendars` XNYS sessions.
- `data/*_price.csv` preserve open and close, with split scaling already present in the supplied ETF prices.
- Weekend/holiday rows are removed; missing session data, duplicates and nonpositive prices fail the build.
- Early ETF prices have only two decimal places, limiting precision for low-priced leveraged funds.
- QQQ warmup uses unadjusted Close (not Adj Close) from [xjcarter/data/QQQ.csv](https://github.com/xjcarter/data/blob/main/QQQ.csv), blob `0b60cfa05a0117194440efd686c2df28f656f022`. Only pre-2010 rows are used.
- Prices exclude dividends. These results are **price returns**, not total returns. No FX or contributions.
- Initial NAV is KRW 100m under a fixed FX assumption. Fractional shares are allowed.
- Each buy pays `units * open * (1 + bps/10000)`; each sell receives `units * open * (1 - bps/10000)`.
- Equity is measured at daily closes, including the initial NAV when calculating drawdown. Overnight gaps on the old holding belong to the old holding.
- No forced sale in the main account result. Initial buy and two orders per executed switch define order count.
- CAGR uses actual calendar days / 365.25. Calmar = each scenario's CAGR / absolute MDD.
- Twelve starts: 2010-02-11 and the first actual trading day of each year 2011–2021; common end 2026-05-15.
- Reported averages are arithmetic averages of individual scenario metrics, including final NAV and Calmar. Average CAGR cannot be used to derive average final NAV.

## Validation interpretation

Period windows use fixed rules and reset to NORMAL; they are historical segmented tests, not parameter selection carried out prospectively. OOS labels in the JSON refer to the later segment only.
The seven sensitivity settings vary RET63 (8/10/12%), holding period (42/63/84) and peak stop (6/8/10%).
Bootstrap resamples the already realized 2010-start strategy daily returns in circular blocks of 21 sessions, 500 draws, seed 20261006. It does not rerun signals on a synthetic market path and is not a future probability forecast.
No regenerated regime-switching, shock, pre-2010 strategy or recovery-time test is claimed.

## Tax model

This is a simplified scenario model, not a tax filing calculation. Annual realized gains and losses are netted; tax liability is `max(annual P/L - 2.5m, 0) * 22%`, with no carryforward and fixed FX. Acquisition cost includes buy fees; sale proceeds are net of sale fees.
Tax is booked as an external liability and never withdrawn from invested account NAV. Current-year amounts are accrued estimates, not payment records.
Economic net NAV = account NAV minus summed annual liabilities. Its terminal-value CAGR excludes timing of external cashflows, so it is not XIRR.
Final hypothetical sale nets the remaining holding gain/loss into the final year's realized P/L, recomputing that year's allowance and liability (which can decrease after a loss). It also includes a final sell fee.
The old 31.52% account-tax-withdrawal result is not used as an interchangeable measure.

## Live operation

`fetch_data.py` downloads QQQ raw closing history from 2008 onward, validates every XNYS session, excludes incomplete sessions using scheduled UTC close times, and replays the same engine from 2010-02-11.
`data.json.q123` stores the resulting completed-session position, pending next-open target, counters and precise indicator values. Refreshing the workflow/page cannot advance the counters.
The dated `q123_state.json` seed is used only until the next workflow embeds a live canonical state. Its as-of date is always displayed.
Order generation uses the engine target, never a stateless momentum approximation. It stops if validated state is unavailable or the signal's next scheduled open has already passed.
Current-mode information describes the model replay, not proof of the user's actual account holding. Intraday data for other dashboard pages remains separate.

## Reproduction

```sh
pip install -r requirements-q123.txt
python scripts/build_q123.py
python -m unittest discover -s tests -v
node tests/test_q123_ui.cjs
```

Build outputs: `q123_results.json`, `q123_state.json`, `q123_transitions.csv` and generated sections in `q123.html`.
Input hashes and exact settings are included in the JSON. The original upload format can be re-imported with `--source-dir DIR`.
