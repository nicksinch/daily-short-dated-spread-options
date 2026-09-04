"""Order construction and the vocabulary for what happens to an order.

Pure: no I/O, no requests, no sleeping. `broker.py` performs the calls and
returns the records defined here, which keeps the judging side -- what a
status means, whether the account is already positioned -- testable without
a socket.
"""

from dataclasses import dataclass

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
