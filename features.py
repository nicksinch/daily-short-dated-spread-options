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
from dataclasses import dataclass
from datetime import date, datetime, timezone
from enum import Enum

from config import FeatureConfig
from data import DailyBar, MarketClock, OptionQuote, StockQuote, StockTrade


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


def log_returns(closes: Sequence[float]) -> list[float]:
    """Natural log returns. n closes produce n-1 returns."""
    return [math.log(b / a) for a, b in zip(closes, closes[1:])]


def annualized_vol(rets: Sequence[float], periods_per_year: int) -> float:
    """Annualised volatility from log returns, using sample (n-1) stdev."""
    return statistics.stdev(rets) * math.sqrt(periods_per_year)


def simple_moving_average(closes: Sequence[float], window: int) -> float:
    """Mean of the newest `window` closes."""
    return sum(closes[-window:]) / window


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
