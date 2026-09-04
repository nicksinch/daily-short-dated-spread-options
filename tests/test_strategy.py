from datetime import datetime, timezone

import pytest

from data import OptionQuote
from strategy import (
    Decision,
    SpreadLeg,
    SpreadProposal,
    Stance,
    max_loss_per_contract,
    net_credit,
    position_size,
    select_long_leg,
    select_short_leg,
    structure_for,
)

NOW = datetime(2026, 9, 4, 16, 39, tzinfo=timezone.utc)


def a_put(strike, delta, bid=1.60, ask=1.68):
    # Puts carry a negative delta; selection compares absolute values.
    return OptionQuote(
        symbol=f"SPY260905P{int(strike * 1000):08d}",
        strike=strike, right="P", bid=bid, ask=ask, iv=0.17, delta=delta, timestamp=NOW,
    )


def a_call(strike, delta, bid=1.55, ask=1.63):
    return OptionQuote(
        symbol=f"SPY260905C{int(strike * 1000):08d}",
        strike=strike, right="C", bid=bid, ask=ask, iv=0.16, delta=delta, timestamp=NOW,
    )


def test_bullish_sells_a_put_credit_spread():
    assert structure_for(Stance.BULLISH) == "put_credit"


def test_bearish_sells_a_call_credit_spread():
    assert structure_for(Stance.BEARISH) == "call_credit"


def test_neutral_has_no_structure():
    assert structure_for(Stance.NEUTRAL) is None


def test_stance_parses_from_its_command_line_spelling():
    assert Stance("bullish") is Stance.BULLISH


def test_a_decision_without_a_proposal_will_not_trade():
    decision = Decision(stance=Stance.NEUTRAL, proposal=None, reason="neutral stance")
    assert decision.will_trade is False


def test_short_leg_is_the_delta_nearest_the_target():
    chain = [a_put(763.0, -0.38), a_put(761.0, -0.302), a_put(759.0, -0.22)]
    assert select_short_leg(chain, "P", 0.30).strike == 761.0


def test_short_leg_ties_break_toward_the_lower_absolute_delta():
    # 0.25 and 0.35 are both 0.05 from target; the lower one is further OTM.
    chain = [a_put(763.0, -0.35), a_put(757.0, -0.25)]
    assert select_short_leg(chain, "P", 0.30).strike == 757.0


def test_short_leg_ignores_the_other_right():
    # A call at exactly the target must not be picked for a put spread.
    chain = [a_call(769.0, 0.30), a_put(761.0, -0.24)]
    assert select_short_leg(chain, "P", 0.30).right == "P"


@pytest.mark.parametrize("delta", [None, 0.0], ids=["absent delta", "synthesised zero"])
def test_short_leg_skips_contracts_without_a_usable_delta(delta):
    # Alpaca omits greeks at 0DTE and the CLI synthesises zeros; a 0.0 delta
    # is absent data, not a real reading, and must not rank as furthest OTM.
    chain = [a_put(761.0, delta), a_put(757.0, -0.25)]
    assert select_short_leg(chain, "P", 0.30).strike == 757.0


def test_short_leg_is_none_when_no_candidate_qualifies():
    assert select_short_leg([a_call(769.0, 0.30)], "P", 0.30) is None
    assert select_short_leg([], "P", 0.30) is None


def test_long_put_sits_a_width_below_the_short_strike():
    chain = [a_put(761.0, -0.30), a_put(756.0, -0.18)]
    assert select_long_leg(chain, "P", 761.0, 5.0).strike == 756.0


def test_long_call_sits_a_width_above_the_short_strike():
    chain = [a_call(769.0, 0.30), a_call(774.0, 0.18)]
    assert select_long_leg(chain, "C", 769.0, 5.0).strike == 774.0


def test_long_leg_is_none_when_the_strike_is_outside_the_chain():
    # Real at the band edge: the short strike is present, its wing is not.
    chain = [a_put(761.0, -0.30), a_put(757.0, -0.22)]
    assert select_long_leg(chain, "P", 761.0, 5.0) is None


def test_credit_is_the_short_bid_less_the_long_ask():
    # The crossing credit, not the mid: the number actually obtainable.
    short = a_put(761.0, -0.30, bid=1.60, ask=1.68)
    long = a_put(756.0, -0.18, bid=0.88, ask=0.95)
    assert net_credit(short, long, width=5.0) == pytest.approx(0.65)


@pytest.mark.parametrize(
    "short_bid, long_ask, why",
    [
        (None, 0.95, "no short bid"),
        (1.60, None, "no long ask"),
        (0.0, 0.95, "zero short bid"),
        (0.95, 0.95, "credit is zero"),
        (0.80, 0.95, "credit is negative"),
        (5.00, 0.00, "credit equals the width"),
        (6.00, 0.50, "credit exceeds the width"),
    ],
)
def test_credit_is_none_when_the_quotes_cannot_support_it(short_bid, long_ask, why):
    # A credit at or above the width would make max loss zero or negative and
    # divide the sizing by zero. A spread that cannot lose is a quoting fault.
    short = a_put(761.0, -0.30, bid=short_bid, ask=1.68)
    long = a_put(756.0, -0.18, bid=0.88, ask=long_ask)
    assert net_credit(short, long, width=5.0) is None, why


def test_max_loss_is_the_width_less_the_credit_times_the_multiplier():
    assert max_loss_per_contract(5.0, 0.65, 100) == pytest.approx(435.0)


def test_position_size_floors_to_whole_contracts():
    # 1% of $100k is $1,000; two $435 contracts fit, three do not.
    assert position_size(100_000.0, 0.01, 435.0) == 2


def test_position_size_is_zero_when_the_budget_cannot_fund_one_contract():
    assert position_size(10_000.0, 0.01, 435.0) == 0


def test_position_size_takes_an_exact_fit():
    assert position_size(100_000.0, 0.01, 500.0) == 2
