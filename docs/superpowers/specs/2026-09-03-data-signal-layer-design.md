# Data & Signal Layer — Design

**Date:** 2026-09-03
**Status:** Approved, ready for implementation planning
**Source spec:** `docs/start_point_spec.md`

## Scope

The data and signal layer only: fetch market data from Alpaca, compute four features,
report them with explicit data-quality status, and print them from a `--dry-run`
entrypoint.

Out of scope, deferred to later sessions: order construction, strike selection,
strategy construction, position sizing, order submission, portfolio management,
backtesting, optimization, and any additional indicators.

## Investigation findings

These findings changed the design and are recorded because they are not obvious from
Alpaca's API surface.

### Greeks and implied volatility are unavailable at 0DTE, on every feed

The `greeks` block on a same-day-expiry contract is all zeros and the
`impliedVolatility` key is absent entirely. This is not a weekend artifact and not a
feed entitlement problem.

Measured 2026-09-03 pre-market, `indicative` feed, SPY calls near $765:

| Expiry | DTE | Greeks | `impliedVolatility` |
| --- | --- | --- | --- |
| 2026-09-03 | 0 | all zero | key absent |
| 2026-09-04 | 1 | δ 0.6489, γ 0.0519, θ −1.3739, ν 0.1486 | 0.1784 |
| 2026-09-18 | 15 | δ 0.5854 | 0.1194 |
| 2026-10-16 | 43 | δ 0.5815 | 0.1239 |
| 2026-12-18 | 106 | δ 0.5785 | 0.1375 |

Alpaca's Market Data FAQ states the cause: Black-Scholes places days-to-expiry in a
denominator, so at T=0 the calculation is undefined. Their documented preconditions are
a non-zero bid *and* ask, a recent SIP trade on the underlying, an expiration after
today, and implied-volatility convergence within 100 iterations.

Two corollaries:

- Greeks and IV work on the **free `indicative` feed**. No subscription is needed.
- An OPRA subscription would **not** fix 0DTE. `--feed opra` currently returns
  `403 "OPRA agreement is not signed"`, but the 0DTE limitation is model-level, not
  entitlement-level.

The prior observation of zeroed greeks (recorded in `docs/alpaca-paper-spread-smoke-test.md`)
was taken on Monday 2026-08-31 at 10:01 ET — during regular trading hours, on a 0DTE
contract. The 0DTE expiry, not the day of week, explains it.

### Stock quotes are IEX-only and can be one-sided

Recent SIP data returns `403 "subscription does not permit querying recent SIP data"`.
The default and only available stock feed is IEX, roughly 2–3% of consolidated volume.

Measured 2026-09-03 pre-market, `latest-quote SPY` returned `bp: 764.34, ap: 0`. A
naive mid of `(bid + ask) / 2` yields **382.17** — a wrong value that looks entirely
plausible and would silently corrupt every downstream ratio. Guarding against one-sided
quotes is a correctness requirement, not defensive polish.

Historical daily bars are unaffected: the SIP restriction covers only recent data, and
a 2026-04-01 start returned 107 daily bars.

### SPY strike grid and chain pagination

- Strike interval near the money is uniformly **$1.00**. A 740–790 pull for the near
  expiry returned 51 strikes with a spacing histogram of `{1.0: 50}`.
- `strike_price_gte` and `strike_price_lte` **can** be supplied together in one call.
- Pagination is a live hazard. The `limit` parameter counts **contracts, not strikes**,
  and defaults to **100** (maximum 1000). A 740–790 both-types pull hit the cap and
  returned a `next_page_token` mid-chain. A ±25 strike band across calls and puts is 102
  contracts and would silently truncate.

### Expiry availability

SPY has an expiration on every trading day. Confirmed for 2026-09-03 through 2026-09-18;
2026-09-07 is absent because it is Labor Day. The next trading session's expiry is
therefore always available.

## Decisions

| Decision | Choice | Rationale |
| --- | --- | --- |
| Traded expiry | **1DTE** — next trading session | Real greeks and IV on the free feed; no fallback IV estimator needed; `delta_target` stays meaningful for later sessions |
| Transport | **Raw REST via `requests`** | One dependency; `data.py` returns plain records, keeping the seam to `features.py` thin and mockable |
| Bars used | **Completed sessions only** | Features are reproducible across the session; hand-computed fixtures stay meaningful |
| Staleness | **Market-aware** | Closed market is a trading condition, not a data defect; keeps off-hours dry runs useful |

Because the traded expiry is 1DTE, the ATM-IV fallback comparison requested in the
source spec (straddle-mid proxy versus Black-Scholes inversion) is moot. Alpaca supplies
a real IV at 1DTE. Recorded for the future: were 0DTE revisited, the **straddle-mid
proxy** (σ ≈ straddle_mid / (0.3989 · S · √T)) is preferable to BS inversion — it is
closed-form with no solver to diverge, and BS inversion needs the same collapsing √T
plus a rate input plus a root-finder whose convergence is exactly what Alpaca's own
100-iteration limit already fails. Neither is trustworthy at T→0; the honest fix is the
expiry choice, not the estimator.

## Module layout

```
config.py            FeatureConfig
features.py          Status, Feature, pure math, feature builders
data.py              AlpacaClient and record types
main.py              --dry-run entrypoint
tests/test_features.py
requirements.txt
```

`data.py` returns plain records and `None`; it never imports `Status` or `Feature`.
There is therefore no circular dependency and no shared-types module.

## Configuration

One frozen dataclass in `config.py`, independent of the calculations. No numeric
literal appears inside a function body.

```python
@dataclass(frozen=True)
class FeatureConfig:
    rv_window: int = 20
    staleness_threshold: timedelta = timedelta(seconds=60)
    strike_band_width: int = 8            # strikes each side of spot
    delta_target: float = 0.30
    sma_windows: tuple[int, ...] = (20, 50)
    trading_days_per_year: int = 252
    underlying: str = "SPY"
    option_feed: str = "indicative"
    stock_feed: str = "iex"
    expiry_offset_sessions: int = 1       # 1DTE
    bar_lookback_days: int = 120          # >= 50 sessions plus holidays and weekends
```

`strike_band_width` counts strikes, not dollars. On SPY's uniform $1 grid the two
coincide today, but a count survives a grid change. At 8 each side the chain request is
34 contracts, safely under the 100-contract cap.

`delta_target` is unused this session. It is retained because 1DTE greeks are real, so
it will be used by strike selection in a later session.

## Result types

```python
class Status(str, Enum):
    OK = "ok"
    MISSING = "missing"
    STALE = "stale"

@dataclass(frozen=True)
class Feature:
    value: float | None
    timestamp: datetime | None
    status: Status
    detail: str | None = None

    @classmethod
    def ok(cls, value: float, timestamp: datetime) -> "Feature": ...
    @classmethod
    def missing(cls, detail: str) -> "Feature": ...
    @classmethod
    def stale(cls, value: float, timestamp: datetime, detail: str) -> "Feature": ...

    @property
    def usable(self) -> bool:
        return self.status is Status.OK
```

Rules:

- A `stale` Feature **keeps its value**, so the caller can see the number and decide.
- `detail` explains a non-`ok` status in plain language, e.g. `"one-sided quote: ask=0"`.
- Market context (`is_open`, `next_open`, `next_close`) lives on the enclosing
  `FeatureSet`, never on an individual Feature. Data quality and trading decisions stay
  separate.
- Typed results cover expected market-data conditions. Exceptions are reserved for
  HTTP, network, and programming errors.

The four distinctions the source spec requires map as follows:

| Caller needs to distinguish | Representation |
| --- | --- |
| No data / missing data | `status == MISSING` |
| Stale data | `status == STALE`, value retained |
| Valid data | `status == OK` |
| Valid data meaning "don't trade" | `status == OK`; the interpretation belongs to a later layer |

```python
@dataclass(frozen=True)
class FeatureSet:
    spot: Feature
    rv20: Feature
    spot_over_sma20: Feature
    spot_over_sma50: Feature
    atm_iv: Feature
    market_open: bool
    next_open: datetime | None
    next_close: datetime | None
    expiry: date
    as_of: datetime
```

## `features.py`

Two groups of functions in one module, split by whether they know anything about
markets.

### Pure math — floats in, floats out

No bars, no timestamps, no config, no `Feature`. These are what the hand-computed
fixtures test.

```python
def log_returns(closes: Sequence[float]) -> list[float]
def annualized_vol(rets: Sequence[float], periods_per_year: int) -> float
def simple_moving_average(closes: Sequence[float], window: int) -> float
```

`annualized_vol` uses **sample** standard deviation (`statistics.stdev`, n−1), the
finance convention. This is pinned by test, since it changes RV by roughly 2.6% at
n=20 against the population form.

### Feature builders — shape, timestamps, status

No network. Every calculation delegates to the pure functions above. If `math.log`
appears inside a builder, the separation has leaked.

```python
def spot_feature(quote, trade, cfg, now, clock) -> Feature
def realized_vol_feature(bars, cfg) -> Feature
def sma_ratio_feature(bars, spot: Feature, window: int, cfg) -> Feature
def atm_iv_feature(chain, spot: Feature, cfg, now, clock) -> Feature
def build_feature_set(...) -> FeatureSet
```

### Feature semantics

**Spot.** Prefer the mid of a two-sided quote. Reject any quote with `bid <= 0` or
`ask <= 0` and fall back to the last trade; if neither is available, `missing`.
Timestamp is that of the observation actually used.

**RV20.** Requires `rv_window + 1` completed closes — 20 returns need 21 prices. Fewer
than that is `missing`, not an exception. Timestamp is the date of the newest bar used.

**SMA ratios.** `spot / SMA(window)` over completed closes. Timestamp carried is the
**spot** timestamp, because the ratio moves with spot and spot is the input that can go
stale; the newest bar date is recorded in `detail`. This departs from the source spec's
"timestamp of the newest close used" and is a deliberate, documented choice.

**ATM IV.** Take the strike nearest to spot, read the call IV and the put IV at that
strike, return their arithmetic mean. If either side is absent, zero, or non-positive,
return `missing` — never substitute zero. Timestamp is the option market-data timestamp.
At 1DTE an occasional `missing` is a genuine market condition, not a defect.

The averaging is not a formality. Measured at the 766 strike for the 2026-09-04 expiry
with spot at 766.46, the call showed IV 0.1317 and the put 0.2123 — a 61% relative gap
at the same strike and expiry. Put-call IV skew on the indicative feed is large, so the
mean sits well away from either leg. The source spec mandates the average and this design
follows it, but the spread between the two legs is worth carrying into the later signal
work rather than discarding.

Deltas are populated alongside IV at this expiry (766C +0.5627, 766P −0.4597),
confirming that delta-targeted strike selection is viable at 1DTE in a later session.

**Cascade.** If `spot` is not `ok`, then both SMA ratios and ATM IV are `missing`,
carrying a `detail` that names spot as the cause. RV20 does not depend on spot and is
computed regardless.

**Staleness.** When the market is open, a feature whose timestamp is older than
`staleness_threshold` is `stale`. When the market is closed, the last session's data is
the freshest that exists, so the threshold is not applied and the status stays `ok`;
the closed market is reported through `FeatureSet.market_open` and `next_open`.

## `data.py`

Frozen record types, all plain data:

```python
@dataclass(frozen=True)
class DailyBar:      date, open, high, low, close, volume
@dataclass(frozen=True)
class StockQuote:    bid, ask, bid_size, ask_size, timestamp
@dataclass(frozen=True)
class StockTrade:    price, size, timestamp
@dataclass(frozen=True)
class OptionQuote:   symbol, strike, right, bid, ask, iv, delta, timestamp
@dataclass(frozen=True)
class MarketClock:   is_open, timestamp, next_open, next_close
```

```python
class AlpacaClient:
    def __init__(self, key_id: str, secret_key: str, cfg: FeatureConfig): ...
    def get_clock(self) -> MarketClock
    def get_daily_bars(self, symbol: str, lookback_days: int) -> list[DailyBar]
    def get_latest_quote(self, symbol: str) -> StockQuote | None
    def get_latest_trade(self, symbol: str) -> StockTrade | None
    def resolve_expiry(self, symbol: str, on: date) -> date
    def get_option_chain(self, symbol, expiry, strike_lo, strike_hi) -> list[OptionQuote]
```

Behaviour required by the investigation findings:

- `get_daily_bars` **drops any bar dated on or after today**, so only settled sessions
  reach the feature functions.
- `get_option_chain` sends an explicit `limit` and raises if the response carries a
  non-empty `next_page_token`, rather than computing on a truncated chain.
- `resolve_expiry` asks Alpaca for contracts with expiration after today and takes the
  earliest, rather than assuming tomorrow's calendar date is a trading day.
- Credentials are read from the environment (`ALPACA_API_KEY_ID`,
  `ALPACA_API_SECRET_KEY`) and never logged. The `alpaca` CLI keeps its own credentials
  in `~/.config/alpaca/profiles/`; this project does not read that store.
- HTTP and network errors raise. Absent or malformed market data returns `None` or an
  empty list for the feature layer to classify.

## `main.py` — dry run

`python main.py --dry-run` fetches, computes, prints, and exits. It constructs no
orders and submits nothing. There is no code path from this entrypoint to any
order-placing endpoint.

Output shape, using values actually measured on 2026-09-03 pre-market:

```
SPY  as of 2026-09-03T12:40:00Z   market_open=False  next_open=2026-09-03T09:30-04:00
expiry (1DTE target): 2026-09-04

spot              766.46   ok      2026-09-03T12:37:42Z  (last trade; quote one-sided, ask=0)
rv20                0.0738 ok      2026-09-02            (20 returns over 21 closes)
spot/sma20          0.9967 ok      2026-09-03T12:37:42Z  (sma20=768.98 over closes to 2026-09-02)
spot/sma50          1.0147 ok      2026-09-03T12:37:42Z  (sma50=755.34 over closes to 2026-09-02)
atm_iv              0.1720 ok      2026-09-02T19:59:59Z  (mean of 766C 0.1317, 766P 0.2123)
```

Note the spot line: the IEX quote was one-sided (`bid 764.34, ask 0`), so the value came
from the last trade instead, and `detail` records why. This is the guard described above
firing on real data, not a hypothetical.

## Testing

Pure functions are tested against arithmetic computable by hand, with no Alpaca access
and no network.

| Fixture | Input | Expected |
| --- | --- | --- |
| Flat series | `[100.0] * 21` | all returns 0 → RV20 = 0; SMA20 = 100; ratio at spot 100 = 1.0 |
| Constant growth | `100 * 1.01**i`, i = 0..20 | all 20 returns equal `ln(1.01)` → stdev 0 → RV20 = 0 |
| Alternating | closes alternating `100`, `100·e^r`, 21 values, `r = ln(1.01)` | 10 returns `+r`, 10 returns `−r`, mean 0 → sample stdev `r·√(20/19)` → RV20 = `r·√(20/19)·√252` = **0.16206006** |
| SMA20 | closes `1..20` | 10.5 |
| SMA50 | closes `1..50` | 25.5 |
| Ratio | spot 21, SMA20 10.5 | 2.0 |
| Window shorter than data | closes `1..50`, window 20 | mean of `31..50` = 40.5 |
| Too few closes | 15 closes, `rv_window` 20 | `Feature.missing`, no exception |

The constant-growth fixture is the one that catches an annualization or mean-handling
bug that the flat series would not: a non-zero but constant return must still produce
zero volatility.

Status-logic tests are separate from the arithmetic tests and cover: one-sided quote
rejection, trade fallback, stale-when-open, not-stale-when-closed, the spot cascade into
SMA ratios and ATM IV, and ATM IV missing when one side of the straddle lacks IV.

## Repository housekeeping

Folded into the first implementation commit:

- `git init` has been run; the repository had no history before this design document.
- `requirements.txt` is created with `requests` and `pytest`. No manifest existed.
- `main.py` currently holds the untouched PyCharm `print_hi` template and is replaced by
  the dry-run entrypoint.
- `CLAUDE.md` describes an IBKR MCP server and an empty scaffold. It is rewritten to
  describe this Alpaca project, retaining the existing simplicity constraint and the
  existing rule that order-placing surfaces require explicit confirmation.

## Departures from the source spec

Recorded so they are visible rather than silent:

1. **1DTE instead of 0DTE.** Forced by the finding that 0DTE carries no greeks or IV.
2. **No IV fallback implemented or needed.** The comparison is recorded above for the
   record; at 1DTE Alpaca supplies a real IV.
3. **Config carries more than four fields.** `252`, the SMA windows, the feeds, and the
   symbol are magic numbers and the spec forbids magic numbers in function bodies.
4. **SMA-ratio timestamp is the spot timestamp**, not the newest close, because the
   ratio moves with spot. The bar date is preserved in `detail`.
5. **A `usable` property and `ok`/`missing`/`stale` constructors** are added to `Feature`
   as call-site convenience. The status enum itself is exactly the three the spec names.