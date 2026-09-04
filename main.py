"""Entrypoint for the daily SPY spread agent.

Fetches market data, computes the features, decides, and either prints the
order it would place (--dry-run) or places it (--submit). Exactly one of
those flags is required: there is no default, and no bare invocation that
trades.

Only `broker.py` can reach an order endpoint, and the --dry-run path returns
before a Broker is constructed.
"""

import argparse
import sys
from datetime import datetime
from zoneinfo import ZoneInfo

from config import FeatureConfig, StrategyConfig
from data import AlpacaClient
from features import FeatureSet, build_feature_set, spot_feature
from strategy import Decision, Stance, build_decision

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


def format_decision(decision: Decision) -> str:
    """The decision, and either its legs or the reason there are none."""
    lines = [f"stance: {decision.stance.value}"]
    if decision.proposal is None:
        lines.append(f"decision: stand aside — {decision.reason}")
        return "\n".join(lines)

    p = decision.proposal
    lines.append(f"decision: trade {p.structure}")
    for leg in (p.short_leg, p.long_leg):
        # short.ask, long.bid and long.delta play no part in net_credit (see
        # its docstring) and select_long_leg never inspects greeks, so those
        # three -- and only those three -- are not guaranteed non-None.
        delta = "-" if leg.delta is None else f"{leg.delta:+.4f}"
        bid = "-" if leg.bid is None else f"{leg.bid:.2f}"
        ask = "-" if leg.ask is None else f"{leg.ask:.2f}"
        lines.append(
            f"  {leg.side:<5} {leg.symbol}  {leg.strike}{leg.right}  "
            f"delta {delta}  bid {bid}  ask {ask}"
        )
    lines.append(
        f"  credit {p.credit:.2f}  max loss/contract ${p.max_loss_per_contract:.2f}  "
        f"quantity {p.quantity}  total risk ${p.total_risk:.2f} "
        f"of ${p.risk_budget:.2f} budget"
    )
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="SPY data and signal layer")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument(
        "--dry-run",
        action="store_true",
        help="fetch, compute, print the decision and the order it would place, exit",
    )
    mode.add_argument(
        "--submit",
        action="store_true",
        help="do all of --dry-run, then actually place the order",
    )
    parser.add_argument(
        "--stance",
        required=True,
        type=Stance,
        choices=list(Stance),
        metavar="{bullish,bearish,neutral}",  # choices renders the enum repr otherwise
        help="directional view; supplied by hand until the LLM layer exists",
    )
    args = parser.parse_args(argv)

    cfg = FeatureConfig()
    strategy_cfg = StrategyConfig()
    client = AlpacaClient.from_env(cfg)

    now = datetime.now(tz=EASTERN)
    today = now.date()

    clock = client.get_clock()
    bars = client.get_daily_bars(cfg.underlying, today)
    quote = client.get_latest_quote(cfg.underlying)
    trade = client.get_latest_trade(cfg.underlying)
    expiry = client.resolve_expiry(cfg.underlying, today)
    account = client.get_account()

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
    print()
    decision = build_decision(features, chain, args.stance, account.equity, strategy_cfg)
    print(format_decision(decision))
    return 0


if __name__ == "__main__":
    sys.exit(main())
