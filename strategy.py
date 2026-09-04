"""Stance to sized spread. Deterministic, and performs no I/O.

Everything consequential about a trade is decided here: which structure a
stance implies, which strikes it lands on, what it can lose and how many
contracts fit the risk budget. The stance itself is an argument -- the layer
that produces it does not exist yet, and this module does not care how it is
produced.

Standing aside is a value, not an exception: `Decision` carries a reason in
both directions so a quiet day is as explainable as a busy one.
"""

import math
from collections.abc import Sequence
from dataclasses import dataclass
from enum import Enum

from config import StrategyConfig
from data import OptionQuote
from features import FeatureSet


class Stance(str, Enum):
    """A directional view. The only thing a later LLM layer will choose."""

    BULLISH = "bullish"
    BEARISH = "bearish"
    NEUTRAL = "neutral"


PUT_CREDIT = "put_credit"
CALL_CREDIT = "call_credit"
CALL, PUT = "C", "P"
SELL, BUY = "sell", "buy"


@dataclass(frozen=True)
class SpreadLeg:
    symbol: str  # OCC symbol, ready for the order layer
    strike: float
    right: str  # "C" or "P"
    side: str  # "sell" or "buy"
    # These three come straight from OptionQuote's float | None fields, and
    # net_credit's docstring says explicitly that short.ask and long.bid play
    # no part in the credit; select_long_leg never inspects delta either. So
    # only the sides the credit actually demands (short.bid, short.delta,
    # long.ask) are guaranteed present.
    delta: float | None
    bid: float | None
    ask: float | None


@dataclass(frozen=True)
class SpreadProposal:
    structure: str
    short_leg: SpreadLeg
    long_leg: SpreadLeg
    credit: float  # per share
    max_loss_per_contract: float  # dollars
    quantity: int  # contracts
    total_risk: float  # dollars
    risk_budget: float  # dollars


@dataclass(frozen=True)
class Decision:
    stance: Stance
    proposal: SpreadProposal | None
    reason: str

    @property
    def will_trade(self) -> bool:
        return self.proposal is not None


def structure_for(stance: Stance) -> str | None:
    """The credit spread a stance implies, or None to stand aside.

    A credit spread sells the side the view is against: a bullish view sells
    puts below the market, a bearish view sells calls above it.
    """
    if stance is Stance.BULLISH:
        return PUT_CREDIT
    if stance is Stance.BEARISH:
        return CALL_CREDIT
    return None


def select_short_leg(
    chain: Sequence[OptionQuote], right: str, delta_target: float
) -> OptionQuote | None:
    """The contract whose absolute delta is nearest `delta_target`.

    A delta of zero is treated as absent rather than as a real reading: at
    0DTE Alpaca omits greeks and the CLI synthesises zeros, and a genuine 0.00
    would otherwise rank as the furthest strike from the money.

    Ties go to the lower absolute delta -- the further out of the money of two
    equally-distant strikes, so the arbitrary case is consistently arbitrary.
    """
    candidates = [
        q for q in chain if q.right == right and q.delta is not None and q.delta != 0
    ]
    if not candidates:
        return None
    return min(candidates, key=lambda q: (abs(abs(q.delta) - delta_target), abs(q.delta)))


def select_long_leg(
    chain: Sequence[OptionQuote], right: str, short_strike: float, width: float
) -> OptionQuote | None:
    """The protective wing, one width further out of the money.

    Exact float comparison is safe: strikes are integer thousandths divided by
    1000, and a $5.00 offset from any strike on SPY's grid is exact. Returns
    None when that strike is not in the fetched band.
    """
    wanted = short_strike - width if right == PUT else short_strike + width
    return next((q for q in chain if q.right == right and q.strike == wanted), None)


def net_credit(
    short: OptionQuote, long: OptionQuote, width: float
) -> float | None:
    """Credit received per share, or None if the quotes cannot support one.

    The short leg's bid and the long leg's ask are the sides actually
    received and paid; the other two play no part and are not demanded.

    A credit at or above the width is rejected rather than returned: it would
    make max loss zero or negative, and a spread that cannot lose is a
    quoting fault, not an opportunity.
    """
    if not short.bid or not long.ask:
        return None
    credit = short.bid - long.ask
    if credit <= 0 or credit >= width:
        return None
    return credit


def max_loss_per_contract(width: float, credit: float, multiplier: int) -> float:
    """Worst case per contract, in dollars. The credit is already received."""
    return (width - credit) * multiplier


def position_size(equity: float, risk_fraction: float, max_loss: float) -> int:
    """Whole contracts that fit the risk budget.

    Zero is a legitimate answer: the budget cannot fund one contract.
    `max_loss` is positive by construction -- `net_credit` rejects any credit
    at or above the width -- so there is no division by zero to guard.
    """
    return math.floor(equity * risk_fraction / max_loss)


FEATURE_NAMES = ("spot", "rv20", "spot_over_sma20", "spot_over_sma50", "atm_iv")


def _unusable_features(features: FeatureSet) -> list[str]:
    """Names and reasons for every feature that is not ok."""
    faults = []
    for name in FEATURE_NAMES:
        feature = getattr(features, name)
        if not feature.usable:
            faults.append(f"{name} ({feature.status.value}: {feature.detail})")
    return faults


def _leg(quote: OptionQuote, side: str) -> SpreadLeg:
    return SpreadLeg(
        symbol=quote.symbol,
        strike=quote.strike,
        right=quote.right,
        side=side,
        delta=quote.delta,
        bid=quote.bid,
        ask=quote.ask,
    )


def build_decision(
    features: FeatureSet,
    chain: Sequence[OptionQuote],
    stance: Stance,
    equity: float,
    cfg: StrategyConfig,
) -> Decision:
    """Turn a stance into a sized spread, or explain why not.

    The gates run in a deliberate order. The stance is checked first, so a
    neutral day does not report a data problem it never depended on. The
    features are checked next, before any strike work, because an unusable
    spot invalidates the selection that would follow it.
    """
    structure = structure_for(stance)
    if structure is None:
        return Decision(stance, None, "neutral stance: no directional edge")

    faults = _unusable_features(features)
    if faults:
        return Decision(stance, None, f"features not ok: {', '.join(faults)}")

    right = PUT if structure == PUT_CREDIT else CALL
    short = select_short_leg(chain, right, cfg.delta_target)
    if short is None:
        return Decision(
            stance, None, f"no {right} contract with a usable delta in the chain"
        )

    width = cfg.spread_width_dollars
    long = select_long_leg(chain, right, short.strike, width)
    if long is None:
        wanted = short.strike - width if right == PUT else short.strike + width
        return Decision(
            stance, None, f"no {right} at strike {wanted} to protect the {short.strike} short"
        )

    credit = net_credit(short, long, width)
    if credit is None:
        return Decision(
            stance,
            None,
            f"no usable credit from the {short.strike}/{long.strike} {right} spread",
        )

    minimum = cfg.min_credit_fraction * width
    if credit < minimum:
        return Decision(
            stance,
            None,
            f"credit {credit:.2f} below minimum {minimum:.2f} "
            f"({cfg.min_credit_fraction:.0%} of {width:.2f} width)",
        )

    max_loss = max_loss_per_contract(width, credit, cfg.contract_multiplier)
    budget = equity * cfg.risk_fraction
    quantity = position_size(equity, cfg.risk_fraction, max_loss)
    if quantity < 1:
        return Decision(
            stance,
            None,
            f"max loss {max_loss:.2f} per contract exceeds the {budget:.2f} risk budget",
        )

    proposal = SpreadProposal(
        structure=structure,
        short_leg=_leg(short, SELL),
        long_leg=_leg(long, BUY),
        credit=credit,
        max_loss_per_contract=max_loss,
        quantity=quantity,
        total_risk=quantity * max_loss,
        risk_budget=budget,
    )
    return Decision(
        stance,
        proposal,
        f"sell {short.strike}{right} / buy {long.strike}{right} "
        f"for {credit:.2f}, {quantity} contract(s)",
    )
