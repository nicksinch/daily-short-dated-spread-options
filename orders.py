"""Order construction and the vocabulary for what happens to an order.

Pure: no I/O, no requests, no sleeping. `broker.py` performs the calls and
returns the records defined here, which keeps the judging side -- what a
status means, whether the account is already positioned -- testable without
a socket.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime
from enum import Enum

from config import OrderConfig
from strategy import SpreadProposal

MLEG = "mleg"
LIMIT = "limit"
RATIO_ONE = "1"

# Both legs open new exposure; closing is not this layer's business.
OPEN_INTENT = {"sell": "sell_to_open", "buy": "buy_to_open"}


def build_order(proposal: SpreadProposal, cfg: OrderConfig) -> dict:
    """The mleg payload that opens `proposal`.

    `limit_price` is negative because this is a credit spread: Alpaca
    expresses an mleg net price as positive for a debit and negative for a
    credit. A positive price here would not be rejected -- receiving 0.65
    satisfies "pay at most 0.65" -- it would merely also permit paying 0.64,
    inverting the trade while the fill still reads as normal.

    `qty` is the number of spreads. For an mleg order Alpaca defines qty as
    the number of units of the strategy, not the number of contracts. Both
    legs carry ratio_qty 1: a 1:1 vertical, and Alpaca requires the greatest
    common divisor across legs to be 1.

    Taking the whole proposal rather than loose arguments means there is no
    way to build a payload for a spread that never passed build_decision.
    """
    return {
        "order_class": MLEG,
        "qty": str(proposal.quantity),
        "type": LIMIT,
        "limit_price": f"{-proposal.credit:.2f}",
        "time_in_force": cfg.time_in_force,
        "legs": [
            {
                "symbol": leg.symbol,
                "ratio_qty": RATIO_ONE,
                "side": leg.side,
                "position_intent": OPEN_INTENT[leg.side],
            }
            for leg in (proposal.short_leg, proposal.long_leg)
        ],
    }


class OrderState(str, Enum):
    """What the program does about a status, not what the status is called."""

    FILLED = "filled"    # done, we are on
    WORKING = "working"  # not terminal; keep polling
    DEAD = "dead"        # terminal without a fill


_FILLED = {"filled"}
_DEAD = {"canceled", "expired", "rejected", "suspended", "done_for_day", "replaced"}


@dataclass(frozen=True)
class OrderRecord:
    id: str
    status: str  # Alpaca's own string, preserved rather than normalised
    state: OrderState
    filled_qty: float
    filled_avg_price: float | None
    submitted_at: datetime | None


def classify(status: str) -> OrderState:
    """What to do about an Alpaca order status.

    Anything not known to be terminal is WORKING, so a status Alpaca adds
    later keeps the poll running rather than being mistaken for a fill.
    `partially_filled` is WORKING deliberately: a two-spread order can fill
    one spread, and the remainder may still arrive.
    """
    if status in _FILLED:
        return OrderState.FILLED
    if status in _DEAD:
        return OrderState.DEAD
    return OrderState.WORKING


@dataclass(frozen=True)
class OptionPosition:
    symbol: str
    qty: float  # signed: negative is short


def expiry_prefix(underlying: str, expiry: date) -> str:
    """The leading characters every OCC symbol for this expiry shares."""
    return f"{underlying}{expiry:%y%m%d}"


def existing_exposure(
    positions: Sequence[OptionPosition],
    working_orders: Sequence[Sequence[str]],
    underlying: str,
    expiry: date,
) -> str | None:
    """Why the account is already positioned on `expiry`, or None to proceed.

    Scoped to the expiry rather than to the strikes: re-running after spot
    has moved would pick different strikes and slip past a symbol-exact
    check. Scoped to the expiry rather than to every option: yesterday's
    1DTE spread is still open this morning and must not stand today's trade
    aside.

    Returns the reason string that gets printed and journalled, so a blocked
    run explains itself the way a stand-aside does.
    """
    prefix = expiry_prefix(underlying, expiry)
    held = sorted(p.symbol for p in positions if p.symbol.startswith(prefix))
    if held:
        return f"already holding {', '.join(held)} expiring {expiry}"
    for legs in working_orders:
        matched = sorted(s for s in legs if s.startswith(prefix))
        if matched:
            return f"a working order already covers {', '.join(matched)} expiring {expiry}"
    return None
