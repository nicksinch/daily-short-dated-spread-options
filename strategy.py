"""Stance to sized spread. Deterministic, and performs no I/O.

Everything consequential about a trade is decided here: which structure a
stance implies, which strikes it lands on, what it can lose and how many
contracts fit the risk budget. The stance itself is an argument -- the layer
that produces it does not exist yet, and this module does not care how it is
produced.

Standing aside is a value, not an exception: `Decision` carries a reason in
both directions so a quiet day is as explainable as a busy one.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from enum import Enum

from data import OptionQuote


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
    delta: float
    bid: float
    ask: float


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
