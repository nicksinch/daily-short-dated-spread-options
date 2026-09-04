# Data & Signal Layer Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fetch SPY market data from Alpaca, compute four features (spot, RV20, spot/SMA20, spot/SMA50, ATM IV), each carrying an explicit data-quality status, and print them from a `--dry-run` entrypoint that cannot place orders.

**Architecture:** Four modules. `config.py` holds every tunable in one frozen dataclass. `data.py` talks to Alpaca over raw REST and returns plain records — it never imports `Status` or `Feature`. `features.py` splits into pure arithmetic (floats in, floats out, hand-testable) and feature builders that attach timestamps and status. `main.py` wires them together for the dry run.

**Tech Stack:** Python 3.14.5, `requests`, `pytest`. No pandas, no numpy, no SDK.

**Spec:** `docs/superpowers/specs/2026-09-03-data-signal-layer-design.md`

## Global Constraints

- **Simplicity is a project constraint** (`CLAUDE.md`): prefer simple code, do not overengineer, do not add features that were not asked for.
- **Scope restriction:** no order construction, strike selection, strategy construction, position sizing, order submission, portfolio management, backtesting, optimization, or extra indicators. `main.py` must have no code path to any order-placing endpoint.
- **No magic numbers in function bodies.** Every constant lives in `FeatureConfig`.
- **Never substitute zero for missing market data.** Absent IV/greeks return `missing`.
- **Typed results for expected market conditions; exceptions only for HTTP, network, and programming errors.**
- Use the in-project venv: `.venv/bin/python`, `.venv/bin/pytest`.
- Traded expiry is **1DTE** — Alpaca returns no IV or greeks at 0DTE on any feed.
- Stock feed for recent quotes/trades is **`iex`** (SIP recent data is 403 on this plan). Historical daily bars use **`sip`**.
- Option feed is **`indicative`** (OPRA returns 403 "agreement is not signed").

---

## Corrections to the design document

Two errors in the committed spec, found while writing this plan. This plan is correct; the spec is amended in Task 9.

**1. A single staleness threshold cannot serve both bars and quotes.** The spec applies `staleness_threshold` (60s) to every feature. But the newest *settled* daily bar is always ~18+ hours old during a session, so RV20 and the SMA ratios would be marked `stale` on every single run. Fixed by splitting into two rules: `staleness_threshold` for intraday observations (spot, option quotes) and `max_bar_age_days` for settled daily bars.

**2. `sma_windows: tuple[int, ...]` contradicts the named fields `spot_over_sma20` / `spot_over_sma50`.** If the tuple were changed to `(10, 30)` the field names would lie. The source spec mandates exactly SMA20 and SMA50, so this plan uses two scalar fields, `sma_short_window` and `sma_long_window`.

---

## File structure

| File | Responsibility |
| --- | --- |
| `config.py` | `FeatureConfig` — every tunable, no logic |
| `features.py` | `Status`, `Feature`, `FeatureSet`, pure arithmetic, feature builders. No network, no I/O |
| `data.py` | `AlpacaClient` and plain record types. All HTTP lives here |
| `main.py` | `--dry-run` entrypoint. Wiring and printing only |
| `tests/test_features.py` | Arithmetic fixtures and status logic |
| `tests/test_data.py` | Parsing and the pagination guard, against stubbed responses |
| `requirements.txt` | `requests`, `pytest` |

---

## Task 1: Project scaffolding and configuration

**Files:**
- Create: `requirements.txt`, `config.py`, `tests/__init__.py`, `tests/test_config.py`

**Interfaces:**
- Consumes: nothing
- Produces: `FeatureConfig` — frozen dataclass consumed by every later task

- [ ] **Step 1: Create the dependency manifest**

`requirements.txt`:

```
requests==2.34.2
pytest==9.0.1
```

Install them:

```bash
.venv/bin/pip install -r requirements.txt
```

- [ ] **Step 2: Write the failing test**

`tests/test_config.py`:

```python
from datetime import timedelta

from config import FeatureConfig


def test_defaults_match_the_design():
    cfg = FeatureConfig()
    assert cfg.rv_window == 20
    assert cfg.sma_short_window == 20
    assert cfg.sma_long_window == 50
    assert cfg.trading_days_per_year == 252
    assert cfg.staleness_threshold == timedelta(seconds=60)
    assert cfg.max_bar_age_days == 5
    assert cfg.underlying == "SPY"
    assert cfg.expiry_offset_sessions == 1
    assert cfg.option_feed == "indicative"
    assert cfg.stock_feed == "iex"
    assert cfg.bar_feed == "sip"


def test_config_is_frozen():
    import dataclasses
    import pytest

    cfg = FeatureConfig()
    with pytest.raises(dataclasses.FrozenInstanceError):
        cfg.rv_window = 5
```

- [ ] **Step 3: Run it to make sure it fails**

Run: `.venv/bin/pytest tests/test_config.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'config'`

- [ ] **Step 4: Write the implementation**

`config.py`:

```python
"""Every tunable for the data and signal layer, in one place.

Kept deliberately free of logic so the feature calculations can be tested
against hand-computed values without constructing anything elaborate.
"""

from dataclasses import dataclass
from datetime import timedelta


@dataclass(frozen=True)
class FeatureConfig:
    # Feature arithmetic
    rv_window: int = 20
    sma_short_window: int = 20
    sma_long_window: int = 50
    trading_days_per_year: int = 252

    # Data quality. Two rules, because a settled daily bar is always hours
    # old during a session while a quote is expected to be seconds old.
    staleness_threshold: timedelta = timedelta(seconds=60)
    max_bar_age_days: int = 5

    # Market and instrument
    underlying: str = "SPY"
    expiry_offset_sessions: int = 1  # 1 = next expiry after today (1DTE)
    strike_band_dollars: int = 8  # dollars each side of spot
    delta_target: float = 0.30  # unused here; strike selection is a later session

    # Alpaca feeds. Historical bars may use SIP; recent quotes may not
    # (403 on this plan). Options are indicative; OPRA is not signed, and
    # would not help anyway since 0DTE has no greeks on any feed.
    bar_feed: str = "sip"
    stock_feed: str = "iex"
    option_feed: str = "indicative"

    # Fetch sizing. 120 calendar days comfortably covers 50 sessions.
    bar_lookback_days: int = 120
```

- [ ] **Step 5: Run the tests and make sure they pass**

Run: `.venv/bin/pytest tests/test_config.py -v`
Expected: 2 passed

- [ ] **Step 6: Commit**

```bash
git add requirements.txt config.py tests/
git commit -m "Add feature configuration and dependency manifest"
```

---

## Task 2: Pure arithmetic

The functions here know nothing about markets — no bars, no timestamps, no config, no `Feature`. That is what makes the fixtures one-liners. If `math.log` ever appears in a `*_feature` function, this separation has leaked.

**Files:**
- Create: `features.py`, `tests/test_features.py`

**Interfaces:**
- Consumes: nothing
- Produces:
  - `log_returns(closes: Sequence[float]) -> list[float]`
  - `annualized_vol(rets: Sequence[float], periods_per_year: int) -> float`
  - `simple_moving_average(closes: Sequence[float], window: int) -> float`

- [ ] **Step 1: Write the failing tests**

`tests/test_features.py`:

```python
import math

from features import annualized_vol, log_returns, simple_moving_average

R = math.log(1.01)


def test_log_returns_length_is_one_less_than_closes():
    assert len(log_returns([100.0] * 21)) == 20


def test_log_returns_of_flat_series_are_zero():
    assert log_returns([100.0] * 21) == [0.0] * 20


def test_flat_series_has_zero_volatility():
    assert annualized_vol(log_returns([100.0] * 21), 252) == 0.0


def test_constant_growth_has_zero_volatility():
    # Every return is identical (ln 1.01), so the dispersion is zero even
    # though the returns themselves are not. Catches an implementation that
    # measures deviation from zero instead of from the mean.
    closes = [100 * 1.01**i for i in range(21)]
    assert annualized_vol(log_returns(closes), 252) == pytest.approx(0.0, abs=1e-12)


def test_alternating_returns_match_hand_computation():
    # 21 closes alternating 100, 100e^r -> 10 returns of +r and 10 of -r.
    # Mean is 0, sample stdev is r*sqrt(20/19), annualised by sqrt(252).
    closes = [100.0 if i % 2 == 0 else 100 * math.exp(R) for i in range(21)]
    expected = R * math.sqrt(20 / 19) * math.sqrt(252)
    assert annualized_vol(log_returns(closes), 252) == pytest.approx(expected)
    assert annualized_vol(log_returns(closes), 252) == pytest.approx(0.16206006, abs=1e-8)


def test_annualized_vol_uses_sample_stdev_not_population():
    # Sample (n-1) runs 2.60% above population (n) at n=20. Pinned so the
    # convention cannot drift silently.
    closes = [100.0 if i % 2 == 0 else 100 * math.exp(R) for i in range(21)]
    rets = log_returns(closes)
    population = math.sqrt(sum(x * x for x in rets) / len(rets)) * math.sqrt(252)
    assert annualized_vol(rets, 252) / population == pytest.approx(1.0260, abs=1e-4)


def test_sma20_of_1_through_20():
    assert simple_moving_average(list(range(1, 21)), 20) == 10.5


def test_sma50_of_1_through_50():
    assert simple_moving_average(list(range(1, 51)), 50) == 25.5


def test_sma_uses_only_the_newest_window():
    # 50 closes but a 20-wide window -> mean of 31..50.
    assert simple_moving_average(list(range(1, 51)), 20) == 40.5
```

Add the import at the top of the file:

```python
import pytest
```

- [ ] **Step 2: Run them to make sure they fail**

Run: `.venv/bin/pytest tests/test_features.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'features'`

- [ ] **Step 3: Write the implementation**

`features.py`:

```python
"""Feature calculations for the SPY spread agent.

Two groups live here, split by whether they know anything about markets:

* pure arithmetic -- floats in, floats out, no timestamps and no config;
* feature builders -- shape, timestamps and data-quality status, delegating
  every calculation to the arithmetic above.

Nothing in this module performs I/O.
"""

import math
import statistics
from collections.abc import Sequence


def log_returns(closes: Sequence[float]) -> list[float]:
    """Natural log returns. n closes produce n-1 returns."""
    return [math.log(b / a) for a, b in zip(closes, closes[1:])]


def annualized_vol(rets: Sequence[float], periods_per_year: int) -> float:
    """Annualised volatility from log returns, using sample (n-1) stdev."""
    return statistics.stdev(rets) * math.sqrt(periods_per_year)


def simple_moving_average(closes: Sequence[float], window: int) -> float:
    """Mean of the newest `window` closes."""
    return sum(closes[-window:]) / window
```

- [ ] **Step 4: Run the tests and make sure they pass**

Run: `.venv/bin/pytest tests/test_features.py -v`
Expected: 9 passed

- [ ] **Step 5: Commit**

```bash
git add features.py tests/test_features.py
git commit -m "Add pure feature arithmetic with hand-computed fixtures"
```

---

## Task 3: Result types and freshness rules

**Files:**
- Modify: `features.py`
- Modify: `tests/test_features.py`

**Interfaces:**
- Consumes: `FeatureConfig` from Task 1
- Produces:
  - `Status` enum with `OK`, `MISSING`, `STALE`
  - `Feature(value, timestamp, status, detail)` with `.ok()`, `.missing()`, `.stale()`, `.usable`
  - `quote_freshness(timestamp, now, cfg, market_open) -> tuple[Status, str | None]`
  - `bar_freshness(newest, today, cfg) -> tuple[Status, str | None]`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_features.py`:

```python
from datetime import date, datetime, timedelta, timezone

from config import FeatureConfig
from features import Feature, Status, bar_freshness, quote_freshness

NOW = datetime(2026, 9, 3, 15, 30, tzinfo=timezone.utc)


def test_ok_feature_is_usable():
    f = Feature.ok(1.5, NOW)
    assert f.status is Status.OK
    assert f.value == 1.5
    assert f.usable


def test_missing_feature_has_no_value_and_is_not_usable():
    f = Feature.missing("no quote")
    assert f.status is Status.MISSING
    assert f.value is None
    assert f.timestamp is None
    assert f.detail == "no quote"
    assert not f.usable


def test_stale_feature_keeps_its_value_but_is_not_usable():
    # The caller must be able to see the number and decide for itself.
    f = Feature.stale(1.5, NOW, "300s old")
    assert f.status is Status.STALE
    assert f.value == 1.5
    assert not f.usable


def test_fresh_quote_is_ok_while_market_open():
    cfg = FeatureConfig()
    status, detail = quote_freshness(NOW - timedelta(seconds=3), NOW, cfg, market_open=True)
    assert status is Status.OK
    assert detail is None


def test_old_quote_is_stale_while_market_open():
    cfg = FeatureConfig()
    status, detail = quote_freshness(NOW - timedelta(minutes=5), NOW, cfg, market_open=True)
    assert status is Status.STALE
    assert "300s old" in detail


def test_old_quote_is_ok_while_market_closed():
    # Last session's data is the freshest that exists. "Market is closed" is
    # a trading decision, not a data-quality verdict.
    cfg = FeatureConfig()
    status, detail = quote_freshness(NOW - timedelta(hours=11), NOW, cfg, market_open=False)
    assert status is Status.OK
    assert detail is None


def test_yesterdays_bar_is_fresh():
    # The critical case: during a session the newest settled bar is always
    # ~18h old, and must not be marked stale.
    cfg = FeatureConfig()
    status, _ = bar_freshness(date(2026, 9, 2), date(2026, 9, 3), cfg)
    assert status is Status.OK


def test_bar_older_than_the_limit_is_stale():
    cfg = FeatureConfig()
    status, detail = bar_freshness(date(2026, 8, 20), date(2026, 9, 3), cfg)
    assert status is Status.STALE
    assert "14 days old" in detail
```

- [ ] **Step 2: Run them to make sure they fail**

Run: `.venv/bin/pytest tests/test_features.py -v`
Expected: FAIL with `ImportError: cannot import name 'Feature' from 'features'`

- [ ] **Step 3: Write the implementation**

Add to `features.py`, after the imports and before the arithmetic:

```python
from dataclasses import dataclass
from datetime import date, datetime
from enum import Enum

from config import FeatureConfig


class Status(str, Enum):
    """Data-quality verdict. Never a trading verdict."""

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
    def ok(cls, value: float, timestamp: datetime, detail: str | None = None) -> "Feature":
        return cls(value, timestamp, Status.OK, detail)

    @classmethod
    def missing(cls, detail: str) -> "Feature":
        return cls(None, None, Status.MISSING, detail)

    @classmethod
    def stale(cls, value: float, timestamp: datetime, detail: str) -> "Feature":
        return cls(value, timestamp, Status.STALE, detail)

    @property
    def usable(self) -> bool:
        return self.status is Status.OK


def quote_freshness(
    timestamp: datetime,
    now: datetime,
    cfg: FeatureConfig,
    market_open: bool,
) -> tuple[Status, str | None]:
    """Freshness of an intraday observation.

    While the market is closed the last session's data is the freshest that
    exists, so the threshold is not applied.
    """
    if not market_open:
        return Status.OK, None
    age = now - timestamp
    if age > cfg.staleness_threshold:
        return Status.STALE, (
            f"{age.total_seconds():.0f}s old, "
            f"threshold {cfg.staleness_threshold.total_seconds():.0f}s"
        )
    return Status.OK, None


def bar_freshness(
    newest: date,
    today: date,
    cfg: FeatureConfig,
) -> tuple[Status, str | None]:
    """Freshness of the newest settled daily bar.

    Separate from `quote_freshness` because a settled bar is legitimately
    hours or days old; only a gap larger than a long weekend is suspicious.
    """
    age = (today - newest).days
    if age > cfg.max_bar_age_days:
        return Status.STALE, f"newest settled bar {newest} is {age} days old"
    return Status.OK, None
```

- [ ] **Step 4: Run the tests and make sure they pass**

Run: `.venv/bin/pytest tests/test_features.py -v`
Expected: 17 passed

- [ ] **Step 5: Commit**

```bash
git add features.py tests/test_features.py
git commit -m "Add Feature result type and the two freshness rules"
```

---

## Task 4: Alpaca client — clock, bars, quote, trade

Response shapes below were captured from the live API on 2026-09-03 and are exact.

**Files:**
- Create: `data.py`, `tests/test_data.py`

**Interfaces:**
- Consumes: `FeatureConfig` from Task 1
- Produces:
  - `MarketClock(is_open, timestamp, next_open, next_close)`
  - `DailyBar(date, open, high, low, close, volume)`
  - `StockQuote(bid, ask, bid_size, ask_size, timestamp)`
  - `StockTrade(price, size, timestamp)`
  - `AlpacaClient(key_id, secret_key, cfg, session=None)` with `from_env(cfg)`, `get_clock()`, `get_daily_bars(symbol, today)`, `get_latest_quote(symbol)`, `get_latest_trade(symbol)`

- [ ] **Step 1: Write the failing tests**

`tests/test_data.py`:

```python
from datetime import date, datetime, timezone

import pytest

from config import FeatureConfig
from data import AlpacaClient


class StubSession:
    """Stands in for requests.Session. Returns a canned payload per URL suffix."""

    def __init__(self, routes):
        self.routes = routes
        self.headers = {}
        self.calls = []

    def get(self, url, params=None, timeout=None):
        self.calls.append((url, params))
        for suffix, payload in self.routes.items():
            if url.endswith(suffix):
                return StubResponse(payload)
        raise AssertionError(f"unexpected URL {url}")


class StubResponse:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self._payload


def make_client(routes):
    return AlpacaClient("key", "secret", FeatureConfig(), session=StubSession(routes))


def test_get_clock_parses_offsets():
    client = make_client({
        "/v2/clock": {
            "is_open": True,
            "next_close": "2026-09-03T16:00:00-04:00",
            "next_open": "2026-09-04T09:30:00-04:00",
            "timestamp": "2026-09-03T11:27:12.614966328-04:00",
        }
    })
    clock = client.get_clock()
    assert clock.is_open is True
    assert clock.next_close.hour == 16


def test_daily_bars_drop_the_forming_bar_for_today():
    # The whole point: a bar dated today is still forming, and its "close"
    # is merely the last trade so far.
    client = make_client({
        "/v2/stocks/SPY/bars": {
            "bars": [
                {"t": "2026-09-01T04:00:00Z", "o": 1, "h": 2, "l": 0.5, "c": 761.78, "v": 10},
                {"t": "2026-09-02T04:00:00Z", "o": 1, "h": 2, "l": 0.5, "c": 765.16, "v": 10},
                {"t": "2026-09-03T04:00:00Z", "o": 1, "h": 2, "l": 0.5, "c": 772.40, "v": 3},
            ],
            "next_page_token": None,
            "symbol": "SPY",
        }
    })
    bars = client.get_daily_bars("SPY", date(2026, 9, 3))
    assert [b.date for b in bars] == [date(2026, 9, 1), date(2026, 9, 2)]
    assert bars[-1].close == 765.16


def test_daily_bars_parse_nanosecond_timestamps():
    client = make_client({
        "/v2/stocks/SPY/bars": {
            "bars": [{"t": "2026-09-02T04:00:00Z", "o": 1, "h": 2, "l": 0.5, "c": 765.16, "v": 10}],
            "symbol": "SPY",
        }
    })
    assert client.get_daily_bars("SPY", date(2026, 9, 3))[0].date == date(2026, 9, 2)


def test_empty_bars_return_empty_list_not_an_error():
    client = make_client({"/v2/stocks/SPY/bars": {"bars": None, "symbol": "SPY"}})
    assert client.get_daily_bars("SPY", date(2026, 9, 3)) == []


def test_latest_quote_parses_both_sides():
    client = make_client({
        "/v2/stocks/SPY/quotes/latest": {
            "quote": {"ap": 772.76, "as": 280, "bp": 772.73, "bs": 40,
                      "t": "2026-09-03T15:36:04.545901081Z"},
            "symbol": "SPY",
        }
    })
    quote = client.get_latest_quote("SPY")
    assert quote.bid == 772.73
    assert quote.ask == 772.76
    assert quote.timestamp == datetime(2026, 9, 3, 15, 36, 4, 545901, tzinfo=timezone.utc)


def test_latest_quote_returns_none_when_absent():
    client = make_client({"/v2/stocks/SPY/quotes/latest": {"symbol": "SPY"}})
    assert client.get_latest_quote("SPY") is None


def test_latest_trade_parses():
    client = make_client({
        "/v2/stocks/SPY/trades/latest": {
            "trade": {"p": 772.4, "s": 40, "t": "2026-09-03T15:27:07.910075029Z"},
            "symbol": "SPY",
        }
    })
    trade = client.get_latest_trade("SPY")
    assert trade.price == 772.4
    assert trade.size == 40


def test_credentials_go_in_headers_and_never_in_params():
    session = StubSession({"/v2/clock": {"is_open": False, "timestamp": "2026-09-03T07:00:00-04:00",
                                         "next_open": "2026-09-03T09:30:00-04:00",
                                         "next_close": "2026-09-03T16:00:00-04:00"}})
    client = AlpacaClient("key-id", "secret-key", FeatureConfig(), session=session)
    client.get_clock()
    assert session.headers["APCA-API-KEY-ID"] == "key-id"
    _, params = session.calls[0]
    assert "secret-key" not in str(params)
```

- [ ] **Step 2: Run them to make sure they fail**

Run: `.venv/bin/pytest tests/test_data.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'data'`

- [ ] **Step 3: Write the implementation**

`data.py`:

```python
"""Alpaca REST access.

Returns plain records and `None`. This module deliberately does not import
`Status` or `Feature` -- classifying data quality is the feature layer's job,
which keeps the seam between fetching and calculating thin enough to test
either side without the other.

Network and HTTP problems raise. Absent market data returns `None` or an
empty list for the feature layer to classify.
"""

import os
from dataclasses import dataclass
from datetime import date, datetime, timedelta

import requests

from config import FeatureConfig


@dataclass(frozen=True)
class MarketClock:
    is_open: bool
    timestamp: datetime
    next_open: datetime
    next_close: datetime


@dataclass(frozen=True)
class DailyBar:
    date: date
    open: float
    high: float
    low: float
    close: float
    volume: float


@dataclass(frozen=True)
class StockQuote:
    bid: float
    ask: float
    bid_size: float
    ask_size: float
    timestamp: datetime


@dataclass(frozen=True)
class StockTrade:
    price: float
    size: float
    timestamp: datetime


def _timestamp(raw: str) -> datetime:
    """Parse Alpaca's RFC-3339 timestamps.

    Alpaca emits nanosecond precision; fromisoformat truncates to
    microseconds, which is far finer than anything here needs.
    """
    return datetime.fromisoformat(raw)


class AlpacaClient:
    DATA_URL = "https://data.alpaca.markets"
    TRADING_URL = "https://paper-api.alpaca.markets"

    def __init__(
        self,
        key_id: str,
        secret_key: str,
        cfg: FeatureConfig,
        session: object | None = None,
    ):
        self._cfg = cfg
        self._session = session if session is not None else requests.Session()
        self._session.headers.update(
            {"APCA-API-KEY-ID": key_id, "APCA-API-SECRET-KEY": secret_key}
        )

    @classmethod
    def from_env(cls, cfg: FeatureConfig) -> "AlpacaClient":
        try:
            key_id = os.environ["ALPACA_API_KEY_ID"]
            secret_key = os.environ["ALPACA_API_SECRET_KEY"]
        except KeyError as exc:
            raise RuntimeError(
                "Set ALPACA_API_KEY_ID and ALPACA_API_SECRET_KEY in the environment."
            ) from exc
        return cls(key_id, secret_key, cfg)

    def _get(self, base: str, path: str, params: dict | None = None) -> dict:
        response = self._session.get(f"{base}{path}", params=params, timeout=10)
        response.raise_for_status()
        return response.json()

    def get_clock(self) -> MarketClock:
        payload = self._get(self.TRADING_URL, "/v2/clock")
        return MarketClock(
            is_open=payload["is_open"],
            timestamp=_timestamp(payload["timestamp"]),
            next_open=_timestamp(payload["next_open"]),
            next_close=_timestamp(payload["next_close"]),
        )

    def get_daily_bars(self, symbol: str, today: date) -> list[DailyBar]:
        """Settled daily bars only.

        Any bar dated today is still forming -- its close is just the last
        trade so far -- so it is dropped. Including it would make every
        feature drift through the session.
        """
        start = today - timedelta(days=self._cfg.bar_lookback_days)
        payload = self._get(
            self.DATA_URL,
            f"/v2/stocks/{symbol}/bars",
            {
                "timeframe": "1Day",
                "start": start.isoformat(),
                "adjustment": "split",
                "feed": self._cfg.bar_feed,
                "limit": 10000,
            },
        )
        bars = [
            DailyBar(
                date=_timestamp(b["t"]).date(),
                open=b["o"],
                high=b["h"],
                low=b["l"],
                close=b["c"],
                volume=b["v"],
            )
            for b in (payload.get("bars") or [])
        ]
        return [b for b in bars if b.date < today]

    def get_latest_quote(self, symbol: str) -> StockQuote | None:
        payload = self._get(
            self.DATA_URL,
            f"/v2/stocks/{symbol}/quotes/latest",
            {"feed": self._cfg.stock_feed},
        )
        quote = payload.get("quote")
        if not quote:
            return None
        return StockQuote(
            bid=quote["bp"],
            ask=quote["ap"],
            bid_size=quote["bs"],
            ask_size=quote["as"],
            timestamp=_timestamp(quote["t"]),
        )

    def get_latest_trade(self, symbol: str) -> StockTrade | None:
        payload = self._get(
            self.DATA_URL,
            f"/v2/stocks/{symbol}/trades/latest",
            {"feed": self._cfg.stock_feed},
        )
        trade = payload.get("trade")
        if not trade:
            return None
        return StockTrade(
            price=trade["p"], size=trade["s"], timestamp=_timestamp(trade["t"])
        )
```

- [ ] **Step 4: Run the tests and make sure they pass**

Run: `.venv/bin/pytest tests/test_data.py -v`
Expected: 8 passed

- [ ] **Step 5: Commit**

```bash
git add data.py tests/test_data.py
git commit -m "Add Alpaca client for clock, settled daily bars, quote and trade"
```

---

## Task 5: Alpaca client — expiry resolution and option chain

Two hazards are guarded here, both found on live data. The `limit` parameter counts **contracts, not strikes**, and defaults to 100 — a wide band silently truncates mid-chain. And the raw API **omits** `greeks` and `impliedVolatility` at 0DTE rather than zeroing them, so the parser must treat them as optional.

**Files:**
- Modify: `data.py`
- Modify: `tests/test_data.py`

**Interfaces:**
- Consumes: `AlpacaClient` from Task 4
- Produces:
  - `OptionQuote(symbol, strike, right, bid, ask, iv, delta, timestamp)` where `right` is `"C"` or `"P"`
  - `AlpacaClient.resolve_expiry(symbol, today) -> date`
  - `AlpacaClient.get_option_chain(symbol, expiry, strike_lo, strike_hi) -> list[OptionQuote]`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_data.py`:

```python
def test_resolve_expiry_takes_the_earliest_after_today():
    # Selecting the earliest date is order-independent, so it stays correct
    # whatever order the endpoint returns rows in. (Live 2026-09-03 the order
    # is expiration_date then strike; min() does not depend on that holding.)
    client = make_client({
        "/v2/options/contracts": {
            "option_contracts": [
                {"expiration_date": "2026-09-10"},
                {"expiration_date": "2026-09-04"},
                {"expiration_date": "2026-09-08"},
            ],
            "next_page_token": None,
        }
    })
    assert client.resolve_expiry("SPY", date(2026, 9, 3)) == date(2026, 9, 4)


def test_resolve_expiry_raises_when_no_contracts_exist():
    client = make_client({"/v2/options/contracts": {"option_contracts": []}})
    with pytest.raises(RuntimeError, match="no option contracts"):
        client.resolve_expiry("SPY", date(2026, 9, 3))


def test_option_chain_parses_strike_and_right_from_the_occ_symbol():
    client = make_client({
        "/v1beta1/options/snapshots/SPY": {
            "snapshots": {
                "SPY260904C00772000": {
                    "impliedVolatility": 0.1534,
                    "greeks": {"delta": 0.5341},
                    "latestQuote": {"bp": 2.69, "ap": 2.78,
                                    "t": "2026-09-03T15:36:04.545901081Z"},
                },
                "SPY260904P00772000": {
                    "impliedVolatility": 0.2123,
                    "greeks": {"delta": -0.4597},
                    "latestQuote": {"bp": 2.10, "ap": 2.15,
                                    "t": "2026-09-03T15:36:04.545901081Z"},
                },
            },
            "next_page_token": "",
        }
    })
    chain = client.get_option_chain("SPY", date(2026, 9, 4), 771, 773)
    by_right = {q.right: q for q in chain}
    assert by_right["C"].strike == 772.0
    assert by_right["C"].iv == 0.1534
    assert by_right["P"].delta == -0.4597


def test_option_chain_treats_absent_greeks_as_none_not_zero():
    # At 0DTE the raw API omits both keys entirely. Reading them as zero
    # would fabricate data that the API never returned.
    client = make_client({
        "/v1beta1/options/snapshots/SPY": {
            "snapshots": {
                "SPY260903C00772000": {
                    "latestQuote": {"bp": 1.25, "ap": 1.30,
                                    "t": "2026-09-03T15:36:04.545901081Z"},
                },
            },
            "next_page_token": "",
        }
    })
    quote = client.get_option_chain("SPY", date(2026, 9, 3), 771, 773)[0]
    assert quote.iv is None
    assert quote.delta is None
    assert quote.bid == 1.25


def test_option_chain_raises_rather_than_silently_truncating():
    client = make_client({
        "/v1beta1/options/snapshots/SPY": {
            "snapshots": {
                "SPY260904C00772000": {
                    "latestQuote": {"bp": 2.69, "ap": 2.78,
                                    "t": "2026-09-03T15:36:04.545901081Z"},
                },
            },
            "next_page_token": "U1BZMjYwOTAzUDAwNzg4MDAw",
        }
    })
    with pytest.raises(RuntimeError, match="truncated"):
        client.get_option_chain("SPY", date(2026, 9, 4), 700, 900)


def test_option_chain_sends_an_explicit_limit():
    session = StubSession({
        "/v1beta1/options/snapshots/SPY": {"snapshots": {}, "next_page_token": ""}
    })
    client = AlpacaClient("k", "s", FeatureConfig(), session=session)
    client.get_option_chain("SPY", date(2026, 9, 4), 764, 780)
    _, params = session.calls[0]
    assert params["limit"] == 1000
    assert params["feed"] == "indicative"
```

- [ ] **Step 2: Run them to make sure they fail**

Run: `.venv/bin/pytest tests/test_data.py -v`
Expected: FAIL with `AttributeError: 'AlpacaClient' object has no attribute 'resolve_expiry'`

- [ ] **Step 3: Write the implementation**

Add the record to `data.py`, after `StockTrade`:

```python
@dataclass(frozen=True)
class OptionQuote:
    symbol: str
    strike: float
    right: str  # "C" or "P"
    bid: float | None
    ask: float | None
    iv: float | None
    delta: float | None
    timestamp: datetime | None
```

Add the OCC parser beside `_timestamp`:

```python
def _parse_occ(symbol: str) -> tuple[float, str]:
    """Split an OCC symbol into strike and right.

    SPY260904C00772000 -> (772.0, "C"): the last eight digits are the strike
    in thousandths, and the character before them is the right.
    """
    return int(symbol[-8:]) / 1000, symbol[-9]
```

Add both methods to `AlpacaClient`:

```python
    def resolve_expiry(self, symbol: str, today: date) -> date:
        """The expiry `expiry_offset_sessions` sessions ahead.

        Asks Alpaca which contracts exist rather than assuming tomorrow is a
        trading day -- SPY expires every trading day, but holidays leave gaps
        (2026-09-07 is Labor Day).

        The response is capped at 100 rows and normally carries a
        next_page_token: one expiry's strike list alone exceeds the cap, so a
        blanket truncation raise here would fire on every call. Selection is
        safe because the endpoint orders by (expiration_date, strike), so the
        earliest expiry is always on the first page.
        """
        payload = self._get(
            self.TRADING_URL,
            "/v2/options/contracts",
            {
                "underlying_symbols": symbol,
                "expiration_date_gte": (today + timedelta(days=1)).isoformat(),
                "type": "call",
                "limit": 100,
            },
        )
        expiries = sorted(
            {c["expiration_date"] for c in (payload.get("option_contracts") or [])}
        )
        if not expiries:
            raise RuntimeError(f"no option contracts for {symbol} after {today}")
        index = self._cfg.expiry_offset_sessions - 1
        if index >= len(expiries):
            raise RuntimeError(
                f"only {len(expiries)} expiries available after {today}, "
                f"need offset {self._cfg.expiry_offset_sessions}"
            )
        return date.fromisoformat(expiries[index])

    def get_option_chain(
        self, symbol: str, expiry: date, strike_lo: float, strike_hi: float
    ) -> list[OptionQuote]:
        """Snapshots for one expiry across a strike band.

        `limit` counts contracts, not strikes, and defaults to 100 -- a wide
        band truncates mid-chain. An explicit limit is sent and a returned
        page token is treated as an error rather than computed upon.
        """
        payload = self._get(
            self.DATA_URL,
            f"/v1beta1/options/snapshots/{symbol}",
            {
                "expiration_date": expiry.isoformat(),
                "strike_price_gte": strike_lo,
                "strike_price_lte": strike_hi,
                "feed": self._cfg.option_feed,
                "limit": 1000,
            },
        )
        if payload.get("next_page_token"):
            raise RuntimeError(
                f"option chain for {symbol} {expiry} was truncated; "
                f"narrow the strike band or follow the page token"
            )
        chain = []
        for occ, snapshot in (payload.get("snapshots") or {}).items():
            strike, right = _parse_occ(occ)
            quote = snapshot.get("latestQuote") or {}
            greeks = snapshot.get("greeks") or {}
            chain.append(
                OptionQuote(
                    symbol=occ,
                    strike=strike,
                    right=right,
                    bid=quote.get("bp"),
                    ask=quote.get("ap"),
                    iv=snapshot.get("impliedVolatility"),
                    delta=greeks.get("delta"),
                    timestamp=_timestamp(quote["t"]) if quote.get("t") else None,
                )
            )
        return chain
```

- [ ] **Step 4: Run the tests and make sure they pass**

Run: `.venv/bin/pytest tests/test_data.py -v`
Expected: 14 passed

- [ ] **Step 5: Commit**

```bash
git add data.py tests/test_data.py
git commit -m "Add expiry resolution and option chain with truncation guard"
```

---

## Task 6: Spot feature

The one-sided quote guard is the reason this task exists separately. On 2026-09-03 pre-market the live IEX quote was `bid 764.34, ask 0`; a naive mid yields **382.17**, a wrong number that looks entirely plausible and would corrupt both SMA ratios and the ATM strike selection.

**Files:**
- Modify: `features.py`
- Modify: `tests/test_features.py`

**Interfaces:**
- Consumes: `StockQuote`, `StockTrade` from Task 4; `Feature`, `quote_freshness` from Task 3
- Produces: `spot_feature(quote, trade, cfg, now, market_open) -> Feature`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_features.py`:

```python
from data import StockQuote, StockTrade
from features import spot_feature


def quote(bid, ask, ts=NOW):
    return StockQuote(bid=bid, ask=ask, bid_size=10, ask_size=10, timestamp=ts)


def trade(price, ts=NOW):
    return StockTrade(price=price, size=10, timestamp=ts)


def test_spot_is_the_mid_of_a_two_sided_quote():
    f = spot_feature(quote(772.73, 772.76), trade(772.40), FeatureConfig(), NOW, True)
    assert f.value == pytest.approx(772.745)
    assert f.status is Status.OK


def test_one_sided_quote_falls_back_to_the_last_trade():
    # Real case: bid 764.34 / ask 0 would give a mid of 382.17.
    f = spot_feature(quote(764.34, 0.0), trade(766.46), FeatureConfig(), NOW, True)
    assert f.value == 766.46
    assert f.status is Status.OK
    assert "one-sided" in f.detail


def test_missing_quote_falls_back_to_the_last_trade():
    f = spot_feature(None, trade(766.46), FeatureConfig(), NOW, True)
    assert f.value == 766.46


def test_no_quote_and_no_trade_is_missing():
    f = spot_feature(None, None, FeatureConfig(), NOW, True)
    assert f.status is Status.MISSING
    assert f.value is None


def test_stale_spot_keeps_its_value():
    old = NOW - timedelta(minutes=5)
    f = spot_feature(quote(772.73, 772.76, old), None, FeatureConfig(), NOW, True)
    assert f.status is Status.STALE
    assert f.value == pytest.approx(772.745)


def test_spot_is_not_stale_when_the_market_is_closed():
    old = NOW - timedelta(hours=11)
    f = spot_feature(quote(772.73, 772.76, old), None, FeatureConfig(), NOW, False)
    assert f.status is Status.OK
```

- [ ] **Step 2: Run them to make sure they fail**

Run: `.venv/bin/pytest tests/test_features.py -v`
Expected: FAIL with `ImportError: cannot import name 'spot_feature' from 'features'`

- [ ] **Step 3: Write the implementation**

Add to `features.py`:

```python
def spot_feature(
    quote: "StockQuote | None",
    trade: "StockTrade | None",
    cfg: FeatureConfig,
    now: datetime,
    market_open: bool,
) -> Feature:
    """Current underlying price.

    Prefers the mid of a two-sided quote. A one-sided quote is rejected
    outright: the IEX feed covers a small share of volume and genuinely
    publishes quotes with a zero side outside regular hours, where a naive
    mid produces a plausible-looking but badly wrong price.
    """
    detail = None
    if quote is not None and quote.bid > 0 and quote.ask > 0:
        value = (quote.bid + quote.ask) / 2
        timestamp = quote.timestamp
    elif trade is not None:
        value = trade.price
        timestamp = trade.timestamp
        detail = (
            "one-sided quote, used last trade"
            if quote is not None
            else "no quote, used last trade"
        )
    else:
        return Feature.missing("no two-sided quote and no trade")

    status, freshness_detail = quote_freshness(timestamp, now, cfg, market_open)
    if status is Status.STALE:
        return Feature.stale(value, timestamp, freshness_detail)
    return Feature.ok(value, timestamp, detail)
```

Add the import at the top of `features.py`:

```python
from data import StockQuote, StockTrade
```

- [ ] **Step 4: Run the tests and make sure they pass**

Run: `.venv/bin/pytest tests/test_features.py -v`
Expected: 23 passed

- [ ] **Step 5: Commit**

```bash
git add features.py tests/test_features.py
git commit -m "Add spot feature with one-sided quote rejection"
```

---

## Task 7: Realized volatility and SMA ratio features

**Files:**
- Modify: `features.py`
- Modify: `tests/test_features.py`

**Interfaces:**
- Consumes: `DailyBar` from Task 4; arithmetic from Task 2; `Feature`, `bar_freshness` from Task 3; `spot_feature` output from Task 6
- Produces:
  - `realized_vol_feature(bars, cfg, today) -> Feature`
  - `sma_ratio_feature(bars, spot, window, cfg, today) -> Feature`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_features.py`:

```python
from data import DailyBar
from features import realized_vol_feature, sma_ratio_feature

TODAY = date(2026, 9, 3)


def bars_from(closes, end=date(2026, 9, 2)):
    """Daily bars ending on `end`, one calendar day apart."""
    return [
        DailyBar(
            date=end - timedelta(days=len(closes) - 1 - i),
            open=c, high=c, low=c, close=c, volume=1,
        )
        for i, c in enumerate(closes)
    ]


def test_rv20_of_a_flat_series_is_zero():
    f = realized_vol_feature(bars_from([100.0] * 21), FeatureConfig(), TODAY)
    assert f.value == 0.0
    assert f.status is Status.OK


def test_rv20_timestamp_is_the_newest_bar_used():
    f = realized_vol_feature(bars_from([100.0] * 21), FeatureConfig(), TODAY)
    assert f.timestamp.date() == date(2026, 9, 2)


def test_rv20_needs_21_closes_for_20_returns():
    # The off-by-one: 20 returns require 21 prices.
    f = realized_vol_feature(bars_from([100.0] * 20), FeatureConfig(), TODAY)
    assert f.status is Status.MISSING
    assert "21" in f.detail


def test_rv20_uses_only_the_newest_window():
    closes = [1.0] * 30 + [100.0 if i % 2 == 0 else 100 * math.exp(R) for i in range(21)]
    f = realized_vol_feature(bars_from(closes), FeatureConfig(), TODAY)
    expected = R * math.sqrt(20 / 19) * math.sqrt(252)
    assert f.value == pytest.approx(expected)


def test_rv20_is_stale_when_bars_are_old():
    f = realized_vol_feature(
        bars_from([100.0] * 21, end=date(2026, 8, 20)), FeatureConfig(), TODAY
    )
    assert f.status is Status.STALE
    assert f.value == 0.0


def test_sma_ratio_divides_spot_by_the_average():
    bars = bars_from([float(c) for c in range(1, 21)])
    f = sma_ratio_feature(bars, Feature.ok(21.0, NOW), 20, FeatureConfig(), TODAY)
    assert f.value == pytest.approx(2.0)


def test_sma_ratio_carries_the_spot_timestamp():
    # The ratio moves with spot, and spot is the input that can go stale.
    bars = bars_from([float(c) for c in range(1, 21)])
    f = sma_ratio_feature(bars, Feature.ok(21.0, NOW), 20, FeatureConfig(), TODAY)
    assert f.timestamp == NOW
    assert "2026-09-02" in f.detail


def test_sma_ratio_is_missing_when_spot_is_missing():
    bars = bars_from([float(c) for c in range(1, 21)])
    f = sma_ratio_feature(bars, Feature.missing("no quote"), 20, FeatureConfig(), TODAY)
    assert f.status is Status.MISSING
    assert "spot" in f.detail


def test_sma_ratio_is_missing_with_too_few_bars():
    bars = bars_from([float(c) for c in range(1, 11)])
    f = sma_ratio_feature(bars, Feature.ok(21.0, NOW), 20, FeatureConfig(), TODAY)
    assert f.status is Status.MISSING
```

- [ ] **Step 2: Run them to make sure they fail**

Run: `.venv/bin/pytest tests/test_features.py -v`
Expected: FAIL with `ImportError: cannot import name 'realized_vol_feature' from 'features'`

- [ ] **Step 3: Write the implementation**

Add to `features.py`:

```python
def realized_vol_feature(
    bars: Sequence["DailyBar"], cfg: FeatureConfig, today: date
) -> Feature:
    """Annualised realised volatility over `rv_window` returns.

    Needs one more close than the window: 20 returns require 21 prices.
    """
    needed = cfg.rv_window + 1
    if len(bars) < needed:
        return Feature.missing(f"need {needed} settled closes, got {len(bars)}")

    window = bars[-needed:]
    value = annualized_vol(
        log_returns([b.close for b in window]), cfg.trading_days_per_year
    )
    newest = window[-1].date
    timestamp = datetime.combine(newest, datetime.min.time(), tzinfo=timezone.utc)

    status, detail = bar_freshness(newest, today, cfg)
    if status is Status.STALE:
        return Feature.stale(value, timestamp, detail)
    return Feature.ok(value, timestamp)


def sma_ratio_feature(
    bars: Sequence["DailyBar"],
    spot: Feature,
    window: int,
    cfg: FeatureConfig,
    today: date,
) -> Feature:
    """spot / SMA(window) over settled closes.

    Carries the spot timestamp rather than the newest close, because the
    ratio moves with spot and spot is the input that can go stale. The bar
    date is preserved in `detail`.
    """
    if not spot.usable:
        return Feature.missing(f"spot unusable ({spot.status.value}): {spot.detail}")
    if len(bars) < window:
        return Feature.missing(f"need {window} settled closes, got {len(bars)}")

    average = simple_moving_average([b.close for b in bars], window)
    value = spot.value / average
    newest = bars[-1].date
    detail = f"sma{window}={average:.2f} over closes to {newest}"

    status, stale_detail = bar_freshness(newest, today, cfg)
    if status is Status.STALE:
        return Feature.stale(value, spot.timestamp, f"{detail}; {stale_detail}")
    return Feature.ok(value, spot.timestamp, detail)
```

Extend the datetime import at the top of `features.py`:

```python
from datetime import date, datetime, timezone
```

And add `DailyBar` to the `data` import:

```python
from data import DailyBar, StockQuote, StockTrade
```

- [ ] **Step 4: Run the tests and make sure they pass**

Run: `.venv/bin/pytest tests/test_features.py -v`
Expected: 32 passed

- [ ] **Step 5: Commit**

```bash
git add features.py tests/test_features.py
git commit -m "Add realized volatility and SMA ratio features"
```

---

## Task 8: ATM implied volatility and the assembled feature set

**Files:**
- Modify: `features.py`
- Modify: `tests/test_features.py`

**Interfaces:**
- Consumes: `OptionQuote`, `MarketClock` from Tasks 4–5; every feature builder from Tasks 6–7
- Produces:
  - `atm_iv_feature(chain, spot, cfg, now, market_open) -> Feature`
  - `FeatureSet` dataclass
  - `build_feature_set(bars, quote, trade, chain, clock, expiry, cfg, now, today) -> FeatureSet`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_features.py`:

```python
from data import MarketClock, OptionQuote
from features import FeatureSet, atm_iv_feature, build_feature_set


def opt(strike, right, iv, ts=NOW):
    return OptionQuote(
        symbol=f"SPY260904{right}{int(strike * 1000):08d}",
        strike=strike, right=right, bid=1.0, ask=1.1,
        iv=iv, delta=0.5, timestamp=ts,
    )


def test_atm_iv_averages_the_call_and_put_at_the_nearest_strike():
    chain = [opt(772, "C", 0.1317), opt(772, "P", 0.2123),
             opt(775, "C", 0.9), opt(775, "P", 0.9)]
    f = atm_iv_feature(chain, Feature.ok(772.4, NOW), FeatureConfig(), NOW, True)
    assert f.value == pytest.approx(0.1720)
    assert f.status is Status.OK


def test_atm_iv_picks_the_strike_nearest_spot_not_the_first():
    chain = [opt(770, "C", 0.5), opt(770, "P", 0.5),
             opt(772, "C", 0.10), opt(772, "P", 0.20)]
    f = atm_iv_feature(chain, Feature.ok(772.4, NOW), FeatureConfig(), NOW, True)
    assert f.value == pytest.approx(0.15)


def test_atm_iv_is_missing_when_one_side_has_no_iv():
    # Never substitute zero for an absent IV.
    chain = [opt(772, "C", 0.1317), opt(772, "P", None)]
    f = atm_iv_feature(chain, Feature.ok(772.4, NOW), FeatureConfig(), NOW, True)
    assert f.status is Status.MISSING
    assert f.value is None


def test_atm_iv_is_missing_when_iv_is_zero():
    chain = [opt(772, "C", 0.1317), opt(772, "P", 0.0)]
    f = atm_iv_feature(chain, Feature.ok(772.4, NOW), FeatureConfig(), NOW, True)
    assert f.status is Status.MISSING


def test_atm_iv_is_missing_at_0dte_when_the_api_omits_greeks():
    # The real 0DTE case: no IV on either leg.
    chain = [opt(772, "C", None), opt(772, "P", None)]
    f = atm_iv_feature(chain, Feature.ok(772.4, NOW), FeatureConfig(), NOW, True)
    assert f.status is Status.MISSING


def test_atm_iv_is_missing_when_spot_is_missing():
    chain = [opt(772, "C", 0.1317), opt(772, "P", 0.2123)]
    f = atm_iv_feature(chain, Feature.missing("no quote"), FeatureConfig(), NOW, True)
    assert f.status is Status.MISSING
    assert "spot" in f.detail


def test_atm_iv_is_missing_on_an_empty_chain():
    f = atm_iv_feature([], Feature.ok(772.4, NOW), FeatureConfig(), NOW, True)
    assert f.status is Status.MISSING


def test_feature_set_cascades_a_missing_spot():
    clock = MarketClock(
        is_open=True, timestamp=NOW,
        next_open=NOW + timedelta(days=1), next_close=NOW + timedelta(hours=4),
    )
    fs = build_feature_set(
        bars=bars_from([100.0] * 51),
        quote=None, trade=None,
        chain=[opt(772, "C", 0.13), opt(772, "P", 0.21)],
        clock=clock, expiry=date(2026, 9, 4),
        cfg=FeatureConfig(), now=NOW, today=TODAY,
    )
    assert fs.spot.status is Status.MISSING
    assert fs.spot_over_sma20.status is Status.MISSING
    assert fs.spot_over_sma50.status is Status.MISSING
    assert fs.atm_iv.status is Status.MISSING
    # RV20 does not depend on spot and is still computed.
    assert fs.rv20.status is Status.OK
    assert fs.market_open is True
    assert fs.expiry == date(2026, 9, 4)
```

- [ ] **Step 2: Run them to make sure they fail**

Run: `.venv/bin/pytest tests/test_features.py -v`
Expected: FAIL with `ImportError: cannot import name 'atm_iv_feature' from 'features'`

- [ ] **Step 3: Write the implementation**

Add to `features.py`:

```python
def atm_iv_feature(
    chain: Sequence["OptionQuote"],
    spot: Feature,
    cfg: FeatureConfig,
    now: datetime,
    market_open: bool,
) -> Feature:
    """Mean of the call and put implied volatility at the nearest strike.

    An absent IV is reported as missing, never replaced with zero. At 0DTE
    Alpaca omits implied volatility entirely, so this returns missing for
    every same-day contract regardless of feed.

    The two legs can diverge widely -- 0.1317 against 0.2123 was measured at
    the same strike and expiry -- so the mean sits well away from either.
    """
    if not spot.usable:
        return Feature.missing(f"spot unusable ({spot.status.value}): {spot.detail}")
    if not chain:
        return Feature.missing("empty option chain")

    nearest = min(chain, key=lambda q: abs(q.strike - spot.value)).strike
    at_strike = {q.right: q for q in chain if q.strike == nearest}
    call, put = at_strike.get("C"), at_strike.get("P")

    for label, leg in (("call", call), ("put", put)):
        if leg is None:
            return Feature.missing(f"no {label} at strike {nearest}")
        if leg.iv is None or leg.iv <= 0:
            return Feature.missing(f"no implied volatility on the {nearest} {label}")

    value = (call.iv + put.iv) / 2
    timestamp = call.timestamp or put.timestamp
    detail = f"mean of {nearest}C {call.iv:.4f} and {nearest}P {put.iv:.4f}"
    if timestamp is None:
        return Feature.missing(f"no quote timestamp at strike {nearest}")

    status, stale_detail = quote_freshness(timestamp, now, cfg, market_open)
    if status is Status.STALE:
        return Feature.stale(value, timestamp, f"{detail}; {stale_detail}")
    return Feature.ok(value, timestamp, detail)


@dataclass(frozen=True)
class FeatureSet:
    """All features plus the market context they were observed in.

    Market context lives here rather than on individual features so that
    data quality and trading decisions stay separate: a closed market makes
    no feature defective.
    """

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


def build_feature_set(
    bars: Sequence["DailyBar"],
    quote: "StockQuote | None",
    trade: "StockTrade | None",
    chain: Sequence["OptionQuote"],
    clock: "MarketClock",
    expiry: date,
    cfg: FeatureConfig,
    now: datetime,
    today: date,
) -> FeatureSet:
    """Assemble every feature. Pure: all data is passed in."""
    spot = spot_feature(quote, trade, cfg, now, clock.is_open)
    return FeatureSet(
        spot=spot,
        rv20=realized_vol_feature(bars, cfg, today),
        spot_over_sma20=sma_ratio_feature(bars, spot, cfg.sma_short_window, cfg, today),
        spot_over_sma50=sma_ratio_feature(bars, spot, cfg.sma_long_window, cfg, today),
        atm_iv=atm_iv_feature(chain, spot, cfg, now, clock.is_open),
        market_open=clock.is_open,
        next_open=clock.next_open,
        next_close=clock.next_close,
        expiry=expiry,
        as_of=now,
    )
```

Add `MarketClock` and `OptionQuote` to the `data` import:

```python
from data import DailyBar, MarketClock, OptionQuote, StockQuote, StockTrade
```

- [ ] **Step 4: Run the tests and make sure they pass**

Run: `.venv/bin/pytest -v`
Expected: 56 passed (2 config + 40 features + 14 data)

- [ ] **Step 5: Commit**

```bash
git add features.py tests/test_features.py
git commit -m "Add ATM implied volatility feature and feature set assembly"
```

---

## Task 9: Dry-run entrypoint and documentation

**Files:**
- Modify: `main.py` (replaces the PyCharm `print_hi` template entirely)
- Modify: `CLAUDE.md`
- Modify: `docs/superpowers/specs/2026-09-03-data-signal-layer-design.md`
- Create: `tests/test_main.py`

**Interfaces:**
- Consumes: everything from Tasks 1–8
- Produces: `format_feature_set(fs, symbol) -> str`, `main(argv) -> int`

- [ ] **Step 1: Write the failing test**

`tests/test_main.py`:

```python
from datetime import date, datetime, timedelta, timezone

from config import FeatureConfig
from features import Feature, FeatureSet
from main import format_feature_set

NOW = datetime(2026, 9, 3, 15, 36, tzinfo=timezone.utc)


def a_feature_set():
    return FeatureSet(
        spot=Feature.ok(766.46, NOW, "one-sided quote, used last trade"),
        rv20=Feature.ok(0.0738, NOW),
        spot_over_sma20=Feature.ok(0.9967, NOW, "sma20=768.98 over closes to 2026-09-02"),
        spot_over_sma50=Feature.ok(1.0147, NOW),
        atm_iv=Feature.missing("no implied volatility on the 772.0 call"),
        market_open=True,
        next_open=NOW + timedelta(days=1),
        next_close=NOW + timedelta(hours=4),
        expiry=date(2026, 9, 4),
        as_of=NOW,
    )


def test_output_shows_every_feature_with_its_status():
    text = format_feature_set(a_feature_set(), "SPY")
    for name in ("spot", "rv20", "spot/sma20", "spot/sma50", "atm_iv"):
        assert name in text
    assert "766.46" in text
    assert "ok" in text


def test_missing_feature_shows_its_reason_and_no_value():
    text = format_feature_set(a_feature_set(), "SPY")
    line = next(l for l in text.splitlines() if l.startswith("atm_iv"))
    assert "missing" in line
    assert "no implied volatility" in line


def test_market_context_is_reported_separately_from_features():
    text = format_feature_set(a_feature_set(), "SPY")
    assert "market_open=True" in text
    assert "expiry" in text


def test_main_module_contains_no_order_placing_code():
    # Scope guard: this session builds data and signal only.
    # Tokens are specific to order placement. "submit" is deliberately not
    # among them: it collides with the module docstring's "submits nothing".
    source = (__import__("pathlib").Path(__file__).parent.parent / "main.py").read_text()
    for forbidden in ("/v2/orders", "order_class", "mleg", "position_intent"):
        assert forbidden not in source
```

- [ ] **Step 2: Run it to make sure it fails**

Run: `.venv/bin/pytest tests/test_main.py -v`
Expected: FAIL with `ImportError: cannot import name 'format_feature_set' from 'main'`

- [ ] **Step 3: Write the implementation**

Replace the whole of `main.py`:

```python
"""Dry-run entrypoint for the data and signal layer.

Fetches market data, computes the features, prints them and exits. It
constructs no orders and submits nothing; there is no code path from here to
any order-placing endpoint.
"""

import argparse
import sys
from datetime import datetime
from zoneinfo import ZoneInfo

from config import FeatureConfig
from data import AlpacaClient
from features import FeatureSet, build_feature_set, spot_feature

EASTERN = ZoneInfo("America/New_York")


def format_feature_set(fs: FeatureSet, symbol: str) -> str:
    """One line per feature: name, value, status, timestamp, detail."""
    lines = [
        f"{symbol}  as of {fs.as_of.isoformat()}  "
        f"market_open={fs.market_open}  next_open={fs.next_open.isoformat()}",
        f"expiry: {fs.expiry}",
        "",
    ]
    rows = [
        ("spot", fs.spot),
        ("rv20", fs.rv20),
        ("spot/sma20", fs.spot_over_sma20),
        ("spot/sma50", fs.spot_over_sma50),
        ("atm_iv", fs.atm_iv),
    ]
    for name, feature in rows:
        value = "-" if feature.value is None else f"{feature.value:.4f}"
        timestamp = "-" if feature.timestamp is None else feature.timestamp.isoformat()
        detail = f"  ({feature.detail})" if feature.detail else ""
        lines.append(
            f"{name:<12}{value:>12}  {feature.status.value:<8}{timestamp}{detail}"
        )
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="SPY data and signal layer")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        required=True,
        help="fetch data, compute features, print them, exit (the only mode)",
    )
    parser.parse_args(argv)

    cfg = FeatureConfig()
    client = AlpacaClient.from_env(cfg)

    now = datetime.now(tz=EASTERN)
    today = now.date()

    clock = client.get_clock()
    bars = client.get_daily_bars(cfg.underlying, today)
    quote = client.get_latest_quote(cfg.underlying)
    trade = client.get_latest_trade(cfg.underlying)
    expiry = client.resolve_expiry(cfg.underlying, today)

    spot = spot_feature(quote, trade, cfg, now, clock.is_open)
    if spot.value is None:
        chain = []
    else:
        band = cfg.strike_band_dollars
        chain = client.get_option_chain(
            cfg.underlying, expiry, round(spot.value) - band, round(spot.value) + band
        )

    features = build_feature_set(
        bars=bars, quote=quote, trade=trade, chain=chain, clock=clock,
        expiry=expiry, cfg=cfg, now=now, today=today,
    )
    print(format_feature_set(features, cfg.underlying))
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 4: Run the whole suite**

Run: `.venv/bin/pytest -v`
Expected: 60 passed (2 config + 40 features + 14 data + 4 main)

- [ ] **Step 5: Run the dry run against the live API**

```bash
export ALPACA_API_KEY_ID=...
export ALPACA_API_SECRET_KEY=...
.venv/bin/python main.py --dry-run
```

Expected: five feature lines. During a session all five should read `ok`. Outside a session `spot` may show `one-sided quote, used last trade`, which is correct behaviour, not a fault. Confirm `atm_iv` is populated — if it reads `missing`, check that `expiry` is not today's date.

- [ ] **Step 6: Update `CLAUDE.md`**

Replace the "Current state" and "Domain context" sections. Keep the Environment and Constraints sections as they are.

```markdown
## Current state

The data and signal layer for a daily SPY option-spread agent, trading in
Alpaca's paper environment.

- `config.py` — `FeatureConfig`, every tunable in one frozen dataclass.
- `data.py` — Alpaca REST access. Returns plain records; raises on HTTP and
  network errors, returns `None` for absent market data.
- `features.py` — pure arithmetic plus feature builders that attach
  timestamps and a data-quality `Status`. No I/O.
- `main.py --dry-run` — fetches, computes, prints, exits. Places no orders.
- `tests/` — pytest. Run with `.venv/bin/pytest`.

Credentials come from `ALPACA_API_KEY_ID` and `ALPACA_API_SECRET_KEY`.

## Domain context

A daily defined-risk option spread on SPY. Order construction, strike
selection, sizing and submission are deliberately not implemented yet.

Two findings constrain the design; both are documented in
`docs/superpowers/specs/2026-09-03-data-signal-layer-design.md`:

- **Alpaca returns no greeks and no implied volatility for 0DTE contracts on
  any feed.** The strategy therefore targets 1DTE, where both are populated
  on the free indicative feed. Note that the `alpaca` CLI displays
  `greeks: {delta: 0, ...}` at 0DTE while the REST API omits the key
  entirely — those zeros are synthesised by the CLI, not returned by Alpaca.
- **Stock quotes come from IEX and can be one-sided.** A bid with a zero ask
  is normal outside regular hours; taking a naive mid produces a plausible
  but badly wrong price, so `spot_feature` rejects one-sided quotes.
```

- [ ] **Step 7: Amend the design document**

In `docs/superpowers/specs/2026-09-03-data-signal-layer-design.md`, apply the two corrections listed at the top of this plan:

1. In the Configuration section, replace `sma_windows: tuple[int, ...] = (20, 50)` with `sma_short_window: int = 20` and `sma_long_window: int = 50`, and add `max_bar_age_days: int = 5` and `bar_feed: str = "sip"`.
2. In the Feature semantics section, replace the single staleness paragraph with the two-rule version: `staleness_threshold` governs intraday observations, `max_bar_age_days` governs settled daily bars. Note that a single threshold would mark RV20 and the SMA ratios stale on every intraday run.

Add to the Investigation findings section:

```markdown
### The CLI synthesises zeroed greeks; the REST API omits them

Verified live during market hours on 2026-09-03 at 11:30 ET with tight
two-sided quotes (771C bid 1.97 / ask 1.99):

- `GET /v1beta1/options/snapshots/SPY` for the 0DTE expiry returns snapshots
  whose keys are `dailyBar, latestQuote, latestTrade, minuteBar, prevDailyBar`
  — no `greeks`, no `impliedVolatility`.
- `alpaca data option chain` for the same contract adds
  `greeks: {delta: 0, gamma: 0, rho: 0, theta: 0, vega: 0}`.

The zeros are a CLI artifact. The REST API is honest about absence, which is
one more reason the transport is raw REST.
```

- [ ] **Step 8: Commit**

```bash
git add main.py CLAUDE.md tests/test_main.py docs/
git commit -m "Add dry-run entrypoint and correct the design document"
```

---

## Self-review

**Spec coverage.** Each spec section maps to a task: module layout → Tasks 1–9; configuration → Task 1; result types → Task 3; `data.py` → Tasks 4–5; the four features → Tasks 6–8 (spot, RV20, SMA ratios, ATM IV); the four caller distinctions → Task 3 plus the cascade test in Task 8; dry run → Task 9; test fixtures → Task 2 (all eight from the spec's table appear); housekeeping → Tasks 1 and 9. The IV fallback is not implemented, per the spec and the 1DTE decision.

**Type consistency.** `Feature`, `Status`, `FeatureConfig`, `DailyBar`, `StockQuote`, `StockTrade`, `OptionQuote`, `MarketClock`, `FeatureSet` are each defined once and referenced with identical field names throughout. `sma_short_window` / `sma_long_window` are used consistently in Tasks 1, 7 and 8. `quote_freshness` and `bar_freshness` keep the same signatures from Task 3 onward.

**Dependency direction.** `data.py` never imports from `features.py`; `features.py` imports records from `data.py` and `FeatureConfig` from `config.py`. No cycle.
