"""The stance: the one thing a model decides.

The only module that talks to Anthropic, as `broker.py` is the only one
that can place an order. The model sees the five features and nothing else,
and answers bullish, bearish or neutral. Every way of not getting a usable
answer -- unusable features, an API or network error, a refusal, a reply
that does not fit the schema -- becomes a `StanceCall` with no stance and a
reason, so the caller stands aside and the journal says why.
"""

import os
from dataclasses import dataclass
from typing import Literal

import anthropic
import pydantic

from config import StanceConfig
from features import FeatureSet
from strategy import Stance, unusable_features

SYSTEM_PROMPT = """\
You give a one-day directional view on SPY for a defined-risk options strategy. Your stance decides the trade:
- bullish -> sell a put credit spread (short put near 0.30 delta, next expiry). It profits if SPY stays above the short strike through the option expiry.
- bearish -> sell a call credit spread (short call near 0.30 delta, next expiry). It profits if SPY stays below the short strike through the option expiry.
- neutral -> no trade today.

You are given only the features below; you have no news or other data. Choose neutral when the features don't support a direction with reasonable confidence: standing aside costs nothing. Give your stance and a one-sentence reason grounded in the features."""


@dataclass(frozen=True)
class StanceCall:
    stance: Stance | None  # None: no usable answer, stand aside
    reason: str            # the model's reason, or why there is no stance


class StanceReply(pydantic.BaseModel):
    """The structured-output schema the reply is constrained to."""

    stance: Literal["bullish", "bearish", "neutral"]
    reason: str


def _ratio_line(label: str, ratio: float, window: int) -> str:
    pct = (ratio - 1) * 100
    side = "above" if pct >= 0 else "below"
    return (
        f"{label:<14}{ratio:.4f}   "
        f"(spot is {abs(pct):.2f}% {side} its {window}-day average)"
    )


def build_prompt(features: FeatureSet) -> str:
    """The user message. Only called with every feature usable."""
    market = "market open" if features.market_open else "market closed"
    return "\n".join([
        f"SPY features as of {features.as_of.isoformat()} ({market})",
        f"Option expiry: {features.expiry.isoformat()}",
        "",
        f"{'spot':<14}{features.spot.value:.2f}",
        _ratio_line("spot/sma20", features.spot_over_sma20.value, 20),
        _ratio_line("spot/sma50", features.spot_over_sma50.value, 50),
        f"{'rv20':<14}{features.rv20.value:.4f}   "
        "(20-day realized volatility, annualized)",
        f"{'atm_iv':<14}{features.atm_iv.value:.4f}   "
        "(at-the-money implied volatility for the expiry, annualized)",
    ])


def anthropic_client(cfg: StanceConfig) -> anthropic.Anthropic:
    """Read the key explicitly, as the Alpaca clients do, so a missing or
    empty key fails here rather than as a TypeError inside the first request."""
    key = os.environ["ANTHROPIC_API_KEY"]
    if not key:
        raise KeyError("ANTHROPIC_API_KEY")
    return anthropic.Anthropic(api_key=key, timeout=cfg.timeout_seconds)


def ask_stance(features: FeatureSet, cfg: StanceConfig, client) -> StanceCall:
    """One model call, or a reason why there is no stance. Never raises for
    API, network or reply-format failures."""
    faults = unusable_features(features)
    if faults:
        return StanceCall(None, f"features unusable: {', '.join(faults)}")

    try:
        reply = client.messages.parse(
            model=cfg.model,
            max_tokens=cfg.max_tokens,
            output_config={"effort": cfg.effort},
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": build_prompt(features)}],
            output_format=StanceReply,
        )
    except (anthropic.APIError, pydantic.ValidationError) as exc:
        # pydantic.ValidationError: the SDK validates the reply text against
        # StanceReply inside parse(), so a cut-off reply raises rather than
        # returning. Keep the first line: the full message can be long.
        first = (str(exc).splitlines() or [""])[0]
        return StanceCall(None, f"model call failed: {type(exc).__name__}: {first}")

    parsed = reply.parsed_output
    if reply.stop_reason in ("refusal", "max_tokens") or parsed is None:
        return StanceCall(None, f"no usable model reply: {reply.stop_reason}")
    return StanceCall(Stance(parsed.stance), parsed.reason)
