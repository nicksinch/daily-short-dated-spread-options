import pytest

from strategy import Decision, SpreadLeg, SpreadProposal, Stance, structure_for


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
