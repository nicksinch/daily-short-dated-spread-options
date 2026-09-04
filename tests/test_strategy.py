from datetime import date, datetime, timezone

import pytest

from config import StrategyConfig
from data import OptionQuote
from features import Feature, FeatureSet
from strategy import (
    Decision,
    SpreadLeg,
    SpreadProposal,
    Stance,
    build_decision,
    max_loss_per_contract,
    net_credit,
    position_size,
    select_long_leg,
    select_short_leg,
    structure_for,
)

NOW = datetime(2026, 9, 4, 16, 39, tzinfo=timezone.utc)

CFG = StrategyConfig()


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


def a_feature_set(**overrides):
    """All features ok, spot at 765.12. Overrides replace one feature."""
    fields = dict(
        spot=Feature.ok(765.12, NOW),
        rv20=Feature.ok(0.1483, NOW),
        spot_over_sma20=Feature.ok(1.0121, NOW),
        spot_over_sma50=Feature.ok(1.0388, NOW),
        atm_iv=Feature.ok(0.1642, NOW),
        market_open=True,
        next_open=NOW,
        next_close=NOW,
        expiry=date(2026, 9, 5),
        as_of=NOW,
    )
    return FeatureSet(**{**fields, **overrides})


def a_chain():
    """The worked example: a 0.30-delta short put at 761 with a 756 wing."""
    return [
        a_put(763.0, -0.38, bid=2.40, ask=2.50),
        a_put(761.0, -0.3020, bid=1.60, ask=1.68),
        a_put(756.0, -0.1810, bid=0.88, ask=0.95),
        a_call(769.0, 0.2980, bid=1.55, ask=1.63),
        a_call(774.0, 0.1700, bid=0.80, ask=0.87),
    ]


def test_the_worked_example_produces_the_expected_proposal():
    decision = build_decision(a_feature_set(), a_chain(), Stance.BULLISH, 100_000.0, CFG)
    assert decision.will_trade
    p = decision.proposal
    assert p.structure == "put_credit"
    assert (p.short_leg.strike, p.short_leg.side) == (761.0, "sell")
    assert (p.long_leg.strike, p.long_leg.side) == (756.0, "buy")
    assert p.short_leg.symbol == "SPY260905P00761000"
    assert p.credit == pytest.approx(0.65)
    assert p.max_loss_per_contract == pytest.approx(435.0)
    assert p.quantity == 2
    assert p.total_risk == pytest.approx(870.0)
    assert p.risk_budget == pytest.approx(1000.0)


def test_bearish_sells_calls_above_the_market():
    decision = build_decision(a_feature_set(), a_chain(), Stance.BEARISH, 100_000.0, CFG)
    assert decision.proposal.structure == "call_credit"
    assert decision.proposal.short_leg.strike == 769.0
    assert decision.proposal.long_leg.strike == 774.0


def test_neutral_stands_aside_without_touching_the_chain():
    decision = build_decision(a_feature_set(), a_chain(), Stance.NEUTRAL, 100_000.0, CFG)
    assert not decision.will_trade
    assert "neutral" in decision.reason


@pytest.mark.parametrize(
    "override, expected",
    [
        ({"atm_iv": Feature.missing("no implied volatility on the 765.0 call")}, "atm_iv"),
        ({"spot": Feature.stale(765.12, NOW, "180s old, threshold 60s")}, "spot"),
        ({"rv20": Feature.missing("need 21 settled closes, got 3")}, "rv20"),
    ],
)
def test_a_feature_that_is_not_ok_stands_the_agent_aside(override, expected):
    decision = build_decision(
        a_feature_set(**override), a_chain(), Stance.BULLISH, 100_000.0, CFG
    )
    assert not decision.will_trade
    assert expected in decision.reason


def test_the_feature_gate_is_checked_before_the_chain():
    # An unusable spot invalidates the strike search, so the reason must name
    # the feature rather than the empty chain that follows from it.
    decision = build_decision(
        a_feature_set(spot=Feature.missing("no two-sided quote and no trade")),
        [], Stance.BULLISH, 100_000.0, CFG,
    )
    assert "spot" in decision.reason


def test_no_usable_delta_stands_aside():
    chain = [a_put(761.0, None), a_put(756.0, None)]
    decision = build_decision(a_feature_set(), chain, Stance.BULLISH, 100_000.0, CFG)
    assert not decision.will_trade
    assert "delta" in decision.reason


def test_a_missing_wing_stands_aside_and_names_the_strike():
    chain = [a_put(761.0, -0.3020, bid=1.60, ask=1.68), a_put(757.0, -0.22)]
    decision = build_decision(a_feature_set(), chain, Stance.BULLISH, 100_000.0, CFG)
    assert not decision.will_trade
    assert "756" in decision.reason


def test_an_unquotable_leg_stands_aside():
    chain = [
        a_put(761.0, -0.3020, bid=None, ask=1.68),
        a_put(756.0, -0.1810, bid=0.88, ask=0.95),
    ]
    decision = build_decision(a_feature_set(), chain, Stance.BULLISH, 100_000.0, CFG)
    assert not decision.will_trade
    assert "credit" in decision.reason


def test_a_credit_below_the_floor_stands_aside():
    # 1.20 - 0.95 = 0.25, under the 0.50 minimum on a $5 spread.
    chain = [
        a_put(761.0, -0.3020, bid=1.20, ask=1.28),
        a_put(756.0, -0.1810, bid=0.88, ask=0.95),
    ]
    decision = build_decision(a_feature_set(), chain, Stance.BULLISH, 100_000.0, CFG)
    assert not decision.will_trade
    assert "0.50" in decision.reason


def test_a_budget_too_small_for_one_contract_stands_aside():
    decision = build_decision(a_feature_set(), a_chain(), Stance.BULLISH, 10_000.0, CFG)
    assert not decision.will_trade
    assert "435" in decision.reason


def test_a_trading_decision_still_explains_itself():
    decision = build_decision(a_feature_set(), a_chain(), Stance.BULLISH, 100_000.0, CFG)
    assert "761" in decision.reason and "756" in decision.reason
