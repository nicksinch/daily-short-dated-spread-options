import pytest
from datetime import date

from config import OrderConfig
from orders import build_order, OrderState, classify, OptionPosition, existing_exposure, expiry_prefix
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


@pytest.mark.parametrize(
    "status",
    ["canceled", "expired", "rejected", "suspended", "done_for_day", "replaced"],
)
def test_terminal_failures_are_dead(status):
    assert classify(status) is OrderState.DEAD


@pytest.mark.parametrize(
    "status",
    ["new", "accepted", "pending_new", "accepted_for_bidding", "held",
     "pending_cancel", "pending_replace", "stopped", "calculated"],
)
def test_non_terminal_statuses_keep_working(status):
    assert classify(status) is OrderState.WORKING


def test_filled_is_filled():
    assert classify("filled") is OrderState.FILLED


def test_a_partial_fill_is_still_working():
    # A two-spread order can fill one spread; the rest may still arrive.
    assert classify("partially_filled") is OrderState.WORKING


def test_an_unfamiliar_status_keeps_working_rather_than_looking_filled():
    assert classify("something_alpaca_added_later") is OrderState.WORKING


EXPIRY = date(2026, 9, 5)


def test_the_prefix_is_the_underlying_and_the_expiry():
    assert expiry_prefix("SPY", EXPIRY) == "SPY260905"


def test_nothing_open_lets_the_trade_through():
    assert existing_exposure([], [], "SPY", EXPIRY) is None


def test_a_position_on_the_target_expiry_blocks():
    positions = [OptionPosition("SPY260905P00761000", -2.0)]
    reason = existing_exposure(positions, [], "SPY", EXPIRY)
    assert reason is not None
    assert "SPY260905P00761000" in reason


def test_yesterdays_expiry_does_not_block_todays_trade():
    # The 1DTE spread opened yesterday expires today and is still open this
    # morning. Blocking on it would stand the agent aside every day after
    # the first.
    positions = [OptionPosition("SPY260904P00760000", -2.0)]
    assert existing_exposure(positions, [], "SPY", EXPIRY) is None


def test_an_equity_position_does_not_block():
    assert existing_exposure([OptionPosition("SPY", 1.0)], [], "SPY", EXPIRY) is None


def test_a_working_mleg_order_blocks_via_its_legs():
    # An mleg parent order's own symbol is the empty string; the contracts
    # live on legs[]. A guard reading the parent would never match.
    working = [["SPY260905P00761000", "SPY260905P00756000"]]
    reason = existing_exposure([], working, "SPY", EXPIRY)
    assert reason is not None
    assert "SPY260905P00761000" in reason


def test_a_working_order_on_another_expiry_does_not_block():
    assert existing_exposure([], [["SPY260904P00760000"]], "SPY", EXPIRY) is None


def test_a_position_is_reported_before_a_working_order():
    positions = [OptionPosition("SPY260905P00761000", -2.0)]
    working = [["SPY260905C00770000"]]
    assert "holding" in existing_exposure(positions, working, "SPY", EXPIRY)
