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

    # Centre the strike band on the same spot the features will use, rather
    # than restating the usable-quote rule here. No spot means no band worth
    # requesting: never invent a reference price for absent market data.
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
