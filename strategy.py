"""Stance to sized spread. Deterministic, and performs no I/O.

Everything consequential about a trade is decided here: which structure a
stance implies, which strikes it lands on, what it can lose and how many
contracts fit the risk budget. The stance itself is an argument -- the layer
that produces it does not exist yet, and this module does not care how it is
produced.

Standing aside is a value, not an exception: `Decision` carries a reason in
both directions so a quiet day is as explainable as a busy one.
"""

from dataclasses import dataclass
from enum import Enum


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
