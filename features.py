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
from datetime import date, datetime
from enum import Enum

from config import FeatureConfig
from data import StockQuote, StockTrade


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
