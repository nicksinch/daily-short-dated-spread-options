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
    # Dollar half-width around spot, which equals a strike count only on a $1
    # grid (SPY's grid today). $20 leaves room for a $5 protective wing beyond
    # a short strike a few points out of the money.
    strike_band_dollars: int = 20

    # Alpaca feeds. Historical bars may use SIP; recent quotes may not
    # (403 on this plan). Options are indicative; OPRA is not signed, and
    # would not help anyway since 0DTE has no greeks on any feed.
    bar_feed: str = "sip"
    stock_feed: str = "iex"
    option_feed: str = "indicative"

    # Fetch sizing. 120 calendar days comfortably covers 50 sessions.
    bar_lookback_days: int = 120


@dataclass(frozen=True)
class StrategyConfig:
    """Tunables for turning a stance into a sized spread.

    Separate from FeatureConfig because that class is about computing
    features, not about what to do with them.
    """

    delta_target: float = 0.30          # absolute delta of the short leg
    spread_width_dollars: float = 5.0   # long leg this far further OTM
    risk_fraction: float = 0.01         # max risk as a share of account equity
    min_credit_fraction: float = 0.10   # reject credit below this share of width
    contract_multiplier: int = 100      # US equity options, shares per contract
