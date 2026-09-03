I'm building an autonomous options trading agent for a hackathon submission. It trades one defined-risk options spread per day on SPY in Alpaca's paper-trading environment.

This session covers **only the data and signal layer** — no order construction, no strike selection, no position sizing, no order submission. **Do not implement those.**

Context:

* Python
* Alpaca CLI and MCP are wired and tested
* Paper account is funded at $100k
* SPY is currently around $765
* U.S. equity options are quoted per share with a 100x contract multiplier

End goal of the full system (for context only; don't build it):

```text
market data
    ↓
compute features
    ↓
ask LLM for directional stance
    ↓
deterministically map stance to credit-spread structure
    ↓
select strikes by delta
    ↓
size by max loss
    ↓
submit multi-leg limit order
    ↓
log decision
```

The LLM chooses only a label from a fixed enum. All consequential trading logic will be deterministic code.

## What I want from this session

Create:

* `features.py` exposing pure functions over input data, with no network calls
* `data.py` responsible for fetching data from Alpaca

Keep these separate so the mathematical feature calculations can be tested against hand-computed fixtures without Alpaca or network dependencies.

## Metrics to compute

### 1. Realized volatility

20-day annualized realized volatility.

Use:

```text
log_return_t = ln(close_t / close_{t-1})
RV20 = stdev(last 20 log returns) * sqrt(252)
```

Take daily bars as input.

The timestamp of the resulting feature should be the timestamp of the newest bar used in the calculation.

### 2. Moving averages

Compute SMA20 and SMA50 over closing prices.

Expose:

```text
spot / SMA20
spot / SMA50
```

rather than raw SMA levels.

The timestamp should correspond to the newest close used.

### 3. ATM implied volatility

From the option chain:

* find the strike nearest to spot
* obtain the ATM call IV and ATM put IV
* return their average

Do not silently replace missing IV with zero.

If either side is unavailable, return an explicit unavailable/missing-data status.

The timestamp should correspond to the market-data timestamp used for the option IV values.

### 4. Spot

Return:

* spot price
* timestamp of the spot observation

## Investigate before writing code

Before proposing implementations, investigate and report:

### A. Alpaca option Greeks / IV

Does the option-chain response populate `greeks.delta` and implied volatility during market hours for my configured Alpaca data plan/feed?

I've only seen zeros so far, but that was from a weekend pull.

Check Alpaca's current documentation and explain:

* which feed is being used
* whether Greeks/IV are expected to be populated
* the conditions under which Alpaca calculates them
* whether the weekend observation could explain the zeros
* a minimal live-market test I can run to verify this

If usable IV/Greeks are unavailable, propose a fallback.

Compare:

1. ATM straddle-mid proxy
2. Black-Scholes inversion from the option mid

Recommend one for this hackathon exercise and explain why.

Do not implement the fallback yet.

### B. SPY strike filtering

Confirm:

* the typical strike interval near the money for near-dated SPY options
* whether `--strike-price-gte` and `--strike-price-lte` can be supplied together in one Alpaca call
* whether there are any relevant limitations or pagination concerns

Again, report the findings before writing implementation code.

## Data-quality requirements

Every feature must carry:

```text
value
timestamp
status
```

At minimum, distinguish:

```text
ok
missing
stale
```

If the newest required market data is older than a configurable staleness threshold, mark the result as `stale`.

Prefer typed/sentinel result values for expected market-data conditions such as missing or stale data. Reserve exceptions for unexpected failures such as API/network errors or programming errors.

The caller must be able to distinguish:

* no data / missing data
* stale data
* valid data
* valid data whose eventual trading interpretation happens to be "don't trade"

Do not conflate data quality with trading decisions.

## Configuration

Use one configuration object containing:

* realized-volatility window
* staleness threshold
* strike-band width
* delta target

No magic numbers inside function bodies.

Keep the configuration independent from the actual feature calculations.

## Dry run

Provide a `--dry-run` entrypoint that:

1. fetches the required Alpaca data
2. computes the features
3. prints the feature/result structure
4. exits

It must not submit or construct any orders.

## Testing

Before implementation, propose a small set of unit-test fixtures for the pure feature functions.

For example, use hand-computable close-price sequences to verify:

* log returns
* RV20
* SMA20
* SMA50
* spot/SMA ratios

Do not rely on live Alpaca data for these mathematical tests.

## Important scope restriction

Do not implement:

* order construction
* strike selection by delta
* options strategy construction
* position sizing
* order submission
* portfolio management
* backtesting
* optimization
* additional indicators

Those belong to later sessions.

## First response

**Do not write implementations yet.**

First:

1. report your findings from the Alpaca investigation above
2. propose the module layout
3. propose the configuration object
4. propose the result/status types
5. propose function signatures
6. propose the unit-test fixtures
7. identify anything genuinely underspecified

Then stop and wait for my approval before writing implementation code.
