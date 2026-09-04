from config import OrderConfig
from orders import build_order
from strategy import SpreadLeg, SpreadProposal

SHORT = SpreadLeg(
    symbol="SPY260905P00761000", strike=761.0, right="P", side="sell",
    delta=-0.3020, bid=1.60, ask=1.68,
)
LONG = SpreadLeg(
    symbol="SPY260905P00756000", strike=756.0, right="P", side="buy",
    delta=-0.1810, bid=0.88, ask=0.95,
)


def a_proposal(quantity=2, credit=0.65):
    return SpreadProposal(
        structure="put_credit", short_leg=SHORT, long_leg=LONG,
        credit=credit, max_loss_per_contract=435.0, quantity=quantity,
        total_risk=870.0, risk_budget=1000.0,
    )


def test_a_credit_spread_is_priced_as_a_negative_limit():
    # The one silent, expensive failure in this layer. Alpaca reads a
    # positive mleg limit as a debit: submitting +0.65 on a credit spread is
    # accepted and filled, and would equally permit *paying* 0.64.
    payload = build_order(a_proposal(), OrderConfig())
    assert payload["limit_price"] == "-0.65"


def test_quantity_is_spreads_not_contracts():
    # For an mleg order Alpaca defines qty as units of the strategy.
    payload = build_order(a_proposal(quantity=3), OrderConfig())
    assert payload["qty"] == "3"


def test_the_payload_carries_the_order_class_type_and_tif():
    payload = build_order(a_proposal(), OrderConfig())
    assert payload["order_class"] == "mleg"
    assert payload["type"] == "limit"
    assert payload["time_in_force"] == "day"


def test_both_legs_open_at_a_one_to_one_ratio():
    payload = build_order(a_proposal(), OrderConfig())
    assert payload["legs"] == [
        {
            "symbol": "SPY260905P00761000", "ratio_qty": "1",
            "side": "sell", "position_intent": "sell_to_open",
        },
        {
            "symbol": "SPY260905P00756000", "ratio_qty": "1",
            "side": "buy", "position_intent": "buy_to_open",
        },
    ]


def test_the_short_leg_comes_first():
    payload = build_order(a_proposal(), OrderConfig())
    assert payload["legs"][0]["side"] == "sell"


def test_the_limit_price_is_rounded_to_a_penny():
    payload = build_order(a_proposal(credit=0.6549), OrderConfig())
    assert payload["limit_price"] == "-0.65"
