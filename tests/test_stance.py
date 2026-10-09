from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace

import anthropic
import httpx2
import pydantic
import pytest

from config import StanceConfig
from features import Feature, FeatureSet
from stance import (
    SYSTEM_PROMPT, StanceReply, anthropic_client, ask_stance, build_prompt,
)
from strategy import Stance

NOW = datetime(2026, 10, 9, 14, 15, tzinfo=timezone.utc)
REQUEST = httpx2.Request("POST", "https://api.anthropic.com/v1/messages")


def a_feature_set(**overrides):
    """All features ok. Overrides replace one field."""
    fields = dict(
        spot=Feature.ok(671.42, NOW),
        rv20=Feature.ok(0.1180, NOW),
        spot_over_sma20=Feature.ok(1.0123, NOW),
        spot_over_sma50=Feature.ok(1.0310, NOW),
        atm_iv=Feature.ok(0.1450, NOW),
        market_open=True,
        next_open=NOW + timedelta(days=1),
        next_close=NOW + timedelta(hours=2),
        expiry=date(2026, 10, 10),
        as_of=NOW,
    )
    return FeatureSet(**{**fields, **overrides})


class FakeMessages:
    def __init__(self, reply=None, error=None):
        self.reply = reply
        self.error = error
        self.calls = []

    def parse(self, **kwargs):
        self.calls.append(kwargs)
        if self.error is not None:
            raise self.error
        return self.reply


class FakeClient:
    """Stands in for anthropic.Anthropic. Performs no I/O."""

    def __init__(self, reply=None, error=None):
        self.messages = FakeMessages(reply, error)


def a_reply(stance="bullish", reason="spot above both averages", stop_reason="end_turn"):
    parsed = None if stance is None else StanceReply(stance=stance, reason=reason)
    return SimpleNamespace(stop_reason=stop_reason, parsed_output=parsed)


def a_validation_error():
    try:
        StanceReply.model_validate_json('{"stance": "bull')
    except pydantic.ValidationError as exc:
        return exc


# build_prompt

def test_the_prompt_carries_every_feature_and_the_expiry():
    text = build_prompt(a_feature_set())
    for expected in ("671.42", "1.0123", "1.0310", "0.1180", "0.1450", "2026-10-10"):
        assert expected in text
    assert "1.23% above its 20-day average" in text
    assert "3.10% above its 50-day average" in text


def test_the_prompt_says_when_the_market_is_closed():
    assert "(market open)" in build_prompt(a_feature_set())
    assert "(market closed)" in build_prompt(a_feature_set(market_open=False))


def test_a_ratio_under_one_reads_below():
    text = build_prompt(a_feature_set(spot_over_sma20=Feature.ok(0.9920, NOW)))
    assert "0.80% below its 20-day average" in text
    assert "-0.80" not in text


def test_the_system_prompt_names_all_three_stances():
    for word in ("bullish", "bearish", "neutral"):
        assert word in SYSTEM_PROMPT


# anthropic_client

def test_a_missing_api_key_fails_at_construction(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    with pytest.raises(KeyError, match="ANTHROPIC_API_KEY"):
        anthropic_client(StanceConfig())


def test_an_empty_api_key_fails_at_construction(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "")
    with pytest.raises(KeyError, match="ANTHROPIC_API_KEY"):
        anthropic_client(StanceConfig())


def test_the_client_takes_its_timeout_from_config(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    assert anthropic_client(StanceConfig()).timeout == 60.0


# ask_stance

def test_a_valid_reply_becomes_the_stance_and_its_reason():
    call = ask_stance(a_feature_set(), StanceConfig(), FakeClient(a_reply()))
    assert call.stance is Stance.BULLISH
    assert call.reason == "spot above both averages"


def test_a_neutral_reply_is_a_stance_not_a_failure():
    call = ask_stance(a_feature_set(), StanceConfig(), FakeClient(a_reply("neutral", "mixed")))
    assert call.stance is Stance.NEUTRAL


def test_the_request_uses_the_configured_model_effort_and_schema():
    client = FakeClient(a_reply())
    ask_stance(a_feature_set(), StanceConfig(), client)
    (kwargs,) = client.messages.calls
    assert kwargs["model"] == "claude-opus-5-5"
    assert kwargs["max_tokens"] == 8000
    assert kwargs["output_config"] == {"effort": "medium"}
    assert kwargs["output_format"] is StanceReply
    assert kwargs["system"] == SYSTEM_PROMPT
    assert kwargs["messages"] == [
        {"role": "user", "content": build_prompt(a_feature_set())}
    ]


def test_an_unusable_feature_stands_aside_without_calling_the_model():
    client = FakeClient(a_reply())
    features = a_feature_set(atm_iv=Feature.missing("no implied volatility on the 671.0 call"))
    call = ask_stance(features, StanceConfig(), client)
    assert call.stance is None
    assert "features unusable" in call.reason
    assert "atm_iv" in call.reason
    assert client.messages.calls == []


@pytest.mark.parametrize(
    "error, name",
    [
        (anthropic.APIConnectionError(request=REQUEST), "APIConnectionError"),
        (anthropic.APITimeoutError(request=REQUEST), "APITimeoutError"),
        (
            anthropic.RateLimitError(
                "rate limited", response=httpx2.Response(429, request=REQUEST), body=None
            ),
            "RateLimitError",
        ),
        (
            anthropic.APIStatusError(
                "overloaded", response=httpx2.Response(529, request=REQUEST), body=None
            ),
            "APIStatusError",
        ),
        (a_validation_error(), "ValidationError"),
    ],
    ids=["connection", "timeout", "rate limit", "status", "invalid reply"],
)
def test_a_failed_call_stands_aside_and_names_the_error(error, name):
    call = ask_stance(a_feature_set(), StanceConfig(), FakeClient(error=error))
    assert call.stance is None
    assert call.reason.startswith(f"model call failed: {name}")
    assert "\n" not in call.reason


@pytest.mark.parametrize(
    "reply",
    [
        a_reply(stop_reason="refusal", stance=None),
        a_reply(stop_reason="max_tokens", stance=None),
        a_reply(stop_reason="end_turn", stance=None),
    ],
    ids=["refusal", "max_tokens", "no parsed output"],
)
def test_an_unusable_reply_stands_aside(reply):
    call = ask_stance(a_feature_set(), StanceConfig(), FakeClient(reply))
    assert call.stance is None
    assert call.reason == f"no usable model reply: {reply.stop_reason}"
