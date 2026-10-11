# LLM Stance Layer Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** When `--stance` is omitted, ask Claude for `bullish`/`bearish`/`neutral` from the five features, and stand aside (journalled) whenever no usable answer comes back.

**Architecture:** A new `stance.py` is the only module that imports `anthropic`. It builds the prompt (pure), makes one `client.messages.parse()` call with a Pydantic schema, and turns every failure into `StanceCall(None, reason)`. `main.py` calls it only when `--stance` is absent; a `None` stance takes the existing stand-aside path, so it never reaches `build_decision` or `Broker`.

**Tech Stack:** Python 3.14, `anthropic==1.12.1` (brings `pydantic` and `httpx2`), pytest.

**Spec:** `docs/superpowers/specs/2026-10-09-llm-stance-layer-design.md`

## Global Constraints

- Run everything with the in-project venv: `.venv/bin/pytest`, `.venv/bin/pip`.
- `requirements.txt` is pinned: add exactly `anthropic==1.12.1`.
- Model `claude-opus-5-5`, effort `medium`, `max_tokens` 8000, timeout 60 s — all in `StanceConfig`, no literals in function bodies.
- `ANTHROPIC_API_KEY` is read with `os.environ["ANTHROPIC_API_KEY"]`, as `data.py` and `broker.py` read the Alpaca keys.
- `stance.py` is the only module with `import anthropic` / `from anthropic`. `/v2/orders` stays only in `broker.py`; `mleg` vocabulary stays only in `orders.py`.
- Tests make no network calls. The Anthropic client is always a fake.
- Prefer short, simple code (CLAUDE.md). No retries beyond the SDK's own, no refusal fallback, no prompt caching.
- Every commit message ends with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.

## Review Focus

1. **Missing `ANTHROPIC_API_KEY` with no `--stance`.** Expect a `KeyError` naming the variable *before any market data is fetched*, as a missing Alpaca key fails today. (Without the explicit read, the SDK raises a `TypeError` only at request time, after the fetch, and the run dies without a journal line.) Pinned in Task 2 and Task 4.
2. **A reply cut off or not valid JSON.** The SDK raises `pydantic.ValidationError` from inside `parse()`; expect a stand-aside, not a crash. Pinned in Task 2.
3. **A run before the open.** Features can be `ok` while the market is closed; the prompt must say "market closed". Pinned in Task 2.
4. **Spot below its average.** A ratio under 1 must read "below", never "-0.80% above". Pinned in Task 2.
5. **The model says `neutral`.** Expect the normal neutral stand-aside, with `stance_source: "model"` and the model's reason journalled. Pinned in Task 4.

---

## File Structure

```
config.py             + StanceConfig                                    (Task 1)
strategy.py           _unusable_features → unusable_features;
                      Decision.stance: Stance | None                    (Task 1)
main.py               format_decision handles a None stance (Task 1);
                      --stance optional, model wiring (Task 4)
requirements.txt      + anthropic==1.12.1                               (Task 1)
stance.py             StanceCall, StanceReply, SYSTEM_PROMPT, build_prompt,
                      anthropic_client, ask_stance                      (Task 2, new)
journal.py            MANUAL/MODEL, stance_source, stance_reason, null stance (Task 3)
tests/test_config.py, tests/test_main.py, tests/test_stance.py (new),
tests/test_journal.py
CLAUDE.md             Current state + credentials                       (Task 4)
```

---

### Task 1: Groundwork — dependency, config, a stance that may be None

**Files:**
- Modify: `requirements.txt`
- Modify: `config.py` (append after `OrderConfig`)
- Modify: `strategy.py:1-11` (module docstring), `strategy.py:24` (Stance docstring), `strategy.py:65-73` (`Decision`), `strategy.py:160-167` and `:200` (`_unusable_features`)
- Modify: `main.py` `format_decision` (first line of its body)
- Test: `tests/test_config.py`, `tests/test_main.py`

**Interfaces:**
- Produces: `config.StanceConfig(model: str, effort: str, max_tokens: int, timeout_seconds: float)`; `strategy.unusable_features(features: FeatureSet) -> list[str]` (public, same behaviour); `strategy.Decision.stance: Stance | None`; `format_decision` prints `stance: none` for a `None` stance.

- [ ] **Step 1: Install the SDK and pin it**

```bash
.venv/bin/pip install anthropic==1.12.1
```

Add this line to the top of `requirements.txt`:

```
anthropic==1.12.1
```

- [ ] **Step 2: Write the failing tests**

Append to `tests/test_config.py`, and change its import line to
`from config import FeatureConfig, StrategyConfig, OrderConfig, StanceConfig`:

```python
def test_stance_defaults_match_the_design():
    cfg = StanceConfig()
    assert cfg.model == "claude-opus-5-5"
    assert cfg.effort == "medium"
    assert cfg.max_tokens == 8000
    assert cfg.timeout_seconds == 60.0


def test_stance_config_is_frozen():
    cfg = StanceConfig()
    with pytest.raises(dataclasses.FrozenInstanceError):
        cfg.model = "claude-haiku-5-5"
```

Append to `tests/test_main.py`, directly after `test_standing_aside_prints_the_reason_in_place_of_legs`:

```python
def test_no_stance_prints_none_and_the_reason():
    text = format_decision(Decision(None, None, "no stance: model call failed"))
    assert "stance: none" in text
    assert "stand aside" in text
    assert "model call failed" in text
```

- [ ] **Step 3: Run them to verify they fail**

Run: `.venv/bin/pytest tests/test_config.py tests/test_main.py -k "stance" -v`
Expected: FAIL — `ImportError: cannot import name 'StanceConfig'` (the whole config module errors), and `AttributeError: 'NoneType' object has no attribute 'value'` for the format test.

- [ ] **Step 4: Implement**

Append to `config.py`:

```python
@dataclass(frozen=True)
class StanceConfig:
    """Tunables for asking the model for a stance.

    Separate from the others because this is about one model call, not
    about features, strikes or orders.
    """

    model: str = "claude-opus-5-5"
    effort: str = "medium"         # the model's default; set so it is visible
    max_tokens: int = 8000         # thinking counts against this, so not a one-word budget
    timeout_seconds: float = 60.0
```

In `strategy.py`, replace the module docstring's middle paragraph:

```python
Everything consequential about a trade is decided here: which structure a
stance implies, which strikes it lands on, what it can lose and how many
contracts fit the risk budget. The stance itself is an argument, chosen by
hand or by `stance.py`; this module does not care how it is produced.
```

Change the `Stance` docstring to:

```python
    """A directional view. The only thing the model in stance.py chooses."""
```

Change `Decision` so the field reads:

```python
@dataclass(frozen=True)
class Decision:
    stance: Stance | None  # None: no stance was available, so nothing was decided
    proposal: SpreadProposal | None
    reason: str
```

(Keep `will_trade` as it is.) Rename `_unusable_features` to `unusable_features` in its definition and at its one call site in `build_decision`:

```python
    faults = unusable_features(features)
```

In `main.py` `format_decision`, replace the first line of the body:

```python
    stance = "none" if decision.stance is None else decision.stance.value
    lines = [f"stance: {stance}"]
```

- [ ] **Step 5: Run the full suite**

Run: `.venv/bin/pytest -q`
Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add requirements.txt config.py strategy.py main.py tests/test_config.py tests/test_main.py
git commit -m "Prepare for a model-chosen stance

Add StanceConfig and the anthropic SDK, make unusable_features public for
reuse, and let a Decision carry no stance.

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 2: `stance.py` — prompt, client, one call

**Files:**
- Create: `stance.py`
- Test: `tests/test_stance.py`

**Interfaces:**
- Consumes: `config.StanceConfig`; `strategy.Stance`, `strategy.unusable_features`; `features.FeatureSet`, `features.Feature`.
- Produces:
  - `stance.StanceCall(stance: Stance | None, reason: str)` — frozen dataclass.
  - `stance.StanceReply` — Pydantic model, `stance: Literal["bullish", "bearish", "neutral"]`, `reason: str`.
  - `stance.SYSTEM_PROMPT: str`
  - `stance.build_prompt(features: FeatureSet) -> str`
  - `stance.anthropic_client(cfg: StanceConfig) -> anthropic.Anthropic` — raises `KeyError` when `ANTHROPIC_API_KEY` is unset.
  - `stance.ask_stance(features: FeatureSet, cfg: StanceConfig, client) -> StanceCall` — never raises for API, network or reply-format failures.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_stance.py`:

```python
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
```

- [ ] **Step 2: Run them to verify they fail**

Run: `.venv/bin/pytest tests/test_stance.py -v`
Expected: collection error — `ModuleNotFoundError: No module named 'stance'`.

- [ ] **Step 3: Implement**

Create `stance.py`:

```python
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
- bullish -> sell a put credit spread (short put near 0.30 delta, 1DTE). It profits if SPY stays above the short strike through tomorrow's close.
- bearish -> sell a call credit spread (short call near 0.30 delta, 1DTE). It profits if SPY stays below the short strike.
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
    """Read the key explicitly, as the Alpaca clients do, so a missing key
    fails here rather than as a TypeError inside the first request."""
    return anthropic.Anthropic(
        api_key=os.environ["ANTHROPIC_API_KEY"], timeout=cfg.timeout_seconds
    )


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
```

- [ ] **Step 4: Run them to verify they pass**

Run: `.venv/bin/pytest tests/test_stance.py -v`
Expected: all pass.

- [ ] **Step 5: Run the full suite**

Run: `.venv/bin/pytest -q`
Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add stance.py tests/test_stance.py
git commit -m "Ask the model for a stance

stance.py builds the prompt from the five features, makes one structured
call to Claude, and turns every failure into a stance of None with a reason.

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 3: Journal — where the stance came from

**Files:**
- Modify: `journal.py` (constants, `build_record`)
- Test: `tests/test_journal.py`

**Interfaces:**
- Consumes: `strategy.Decision` with `stance: Stance | None` (Task 1).
- Produces: `journal.MANUAL = "manual"`, `journal.MODEL = "model"`; `build_record(now, mode, underlying, expiry, features, decision, stance_source: str, stance_reason: str | None, payload=None, record=None) -> dict` with top-level `"stance"` (string or `None`), `"stance_source"`, `"stance_reason"`.

- [ ] **Step 1: Write the failing tests**

In `tests/test_journal.py`, change the import to
`from journal import DRY_RUN, MANUAL, MODEL, SUBMIT, append, build_record`, and replace `make` with:

```python
def make(decision, mode=SUBMIT, payload=None, record=None,
         stance_source=MANUAL, stance_reason=None):
    return build_record(
        now=NOW, mode=mode, underlying="SPY", expiry=EXPIRY,
        features=a_feature_set(), decision=decision,
        stance_source=stance_source, stance_reason=stance_reason,
        payload=payload, record=record,
    )
```

Append:

```python
def test_a_manual_stance_records_its_source_and_no_reason():
    record = make(a_trade())
    assert record["stance_source"] == "manual"
    assert record["stance_reason"] is None


def test_a_model_stance_records_the_model_and_its_reason():
    record = make(a_trade(), stance_source=MODEL, stance_reason="spot above both averages")
    assert record["stance"] == "bullish"
    assert record["stance_source"] == "model"
    assert record["stance_reason"] == "spot above both averages"


def test_no_stance_records_null_and_round_trips():
    decision = Decision(None, None, "no stance: model call failed: RateLimitError")
    record = make(decision, stance_source=MODEL,
                  stance_reason="model call failed: RateLimitError")
    assert record["stance"] is None
    assert record["decision"]["will_trade"] is False
    assert json.loads(json.dumps(record)) == record
```

- [ ] **Step 2: Run them to verify they fail**

Run: `.venv/bin/pytest tests/test_journal.py -v`
Expected: collection error — `ImportError: cannot import name 'MANUAL' from 'journal'`.

- [ ] **Step 3: Implement**

In `journal.py`, below `SUBMIT = "submit"`:

```python
MANUAL = "manual"  # stance from --stance
MODEL = "model"    # stance from stance.py
```

Replace `build_record` with:

```python
def build_record(
    now: datetime,
    mode: str,
    underlying: str,
    expiry: date,
    features: FeatureSet,
    decision: Decision,
    stance_source: str,
    stance_reason: str | None,
    payload: dict | None = None,
    record: OrderRecord | None = None,
) -> dict:
    """One run, as JSON-native values."""
    return {
        "timestamp": now.isoformat(),
        "mode": mode,
        "underlying": underlying,
        "expiry": expiry.isoformat(),
        "stance": None if decision.stance is None else decision.stance.value,
        "stance_source": stance_source,
        "stance_reason": stance_reason,
        "features": {name: _feature(getattr(features, name)) for name in FEATURE_NAMES},
        "decision": _decision(decision),
        "order": _order(payload, record),
    }
```

- [ ] **Step 4: Run the journal tests**

Run: `.venv/bin/pytest tests/test_journal.py -v`
Expected: all pass.

- [ ] **Step 5: Keep main working until Task 4**

Run: `.venv/bin/pytest -q`
Expected: failures only in `tests/test_main.py`, each `TypeError: build_record() missing 2 required positional arguments: 'stance_source' and 'stance_reason'`.

Bridge them so this commit is green on its own — in `main.py`, change the `context = dict(` block to

```python
    context = dict(
        now=now, mode=mode, underlying=cfg.underlying, expiry=expiry,
        features=features, decision=decision,
        stance_source=MANUAL, stance_reason=None,
    )
```

and the journal import to `from journal import DRY_RUN, MANUAL, SUBMIT, append, build_record`.

Run: `.venv/bin/pytest -q`
Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add journal.py main.py tests/test_journal.py
git commit -m "Journal where the stance came from

Each line now records stance_source and stance_reason, and a null stance
when there was none.

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 4: `main.py` — ask the model when `--stance` is omitted

**Files:**
- Modify: `main.py` (imports, parser, stance block, context dict, module docstring)
- Modify: `tests/test_main.py`
- Modify: `CLAUDE.md` (Current state)

**Interfaces:**
- Consumes: `stance.anthropic_client`, `stance.ask_stance`, `stance.StanceCall` (Task 2); `config.StanceConfig` (Task 1); `journal.MANUAL`, `journal.MODEL` (Task 3); `Decision(None, None, reason)` (Task 1).
- Produces: CLI where `--stance` is optional. Omitted → client built *before* any data fetch, model asked after features are computed.

- [ ] **Step 1: Write the failing tests**

In `tests/test_main.py`:

Add to the imports:

```python
from types import SimpleNamespace

import anthropic
import httpx2

from stance import StanceReply
```

Add these fakes after `StubClient`:

```python
class FakeModel:
    """Stands in for anthropic.Anthropic. Records calls, performs no I/O."""

    def __init__(self, stance="bullish", reason="spot above both averages", error=None):
        self.stance = stance
        self.reason = reason
        self.error = error
        self.calls = []
        self.messages = self

    def parse(self, **kwargs):
        self.calls.append(kwargs)
        if self.error is not None:
            raise self.error
        return SimpleNamespace(
            stop_reason="end_turn",
            parsed_output=StanceReply(stance=self.stance, reason=self.reason),
        )


def use_model(monkeypatch, model):
    monkeypatch.setattr(main_module, "anthropic_client", lambda cfg: model)
    return model
```

Change `run_main_with` so a `stance` of `None` omits the flag — replace its last two lines with:

```python
    argv = [mode] if stance is None else [mode, "--stance", stance]
    assert main_module.main(argv) == expected
    return client
```

Replace `test_the_stance_flag_is_required` with:

```python
def test_omitting_the_stance_asks_the_model(monkeypatch, tmp_path):
    model = use_model(monkeypatch, FakeModel(stance="bullish"))
    path = tmp_path / "d.jsonl"
    a_bullish_run(monkeypatch, stance=None, journal_path=str(path))
    assert len(model.calls) == 1
    record = json.loads(path.read_text())
    assert record["stance"] == "bullish"
    assert record["stance_source"] == "model"
    assert record["stance_reason"] == "spot above both averages"
    assert record["decision"]["will_trade"] is True
```

Note: `a_bullish_run` passes `stance="bullish"` itself; change its body so a caller's `stance` wins:

```python
def a_bullish_run(monkeypatch, **kwargs):
    kwargs.setdefault("stance", "bullish")
    return run_main_with(
        monkeypatch,
        quote=StockQuote(bid=765.0, ask=765.5, bid_size=1, ask_size=1, timestamp=NOW),
        trade=None, bars=daily_bars(60, date(2026, 9, 3)),
        chain=a_full_chain(), **kwargs,
    )
```

Append:

```python
def test_a_manual_stance_never_asks_the_model(monkeypatch, tmp_path):
    def explode(cfg):
        raise AssertionError("--stance must not construct an Anthropic client")

    monkeypatch.setattr(main_module, "anthropic_client", explode)
    path = tmp_path / "d.jsonl"
    a_bullish_run(monkeypatch, journal_path=str(path))
    record = json.loads(path.read_text())
    assert record["stance_source"] == "manual"
    assert record["stance_reason"] is None


def test_a_neutral_model_stance_stands_aside(monkeypatch, tmp_path):
    use_model(monkeypatch, FakeModel(stance="neutral", reason="signals are mixed"))
    path = tmp_path / "d.jsonl"
    a_bullish_run(monkeypatch, stance=None, journal_path=str(path))
    record = json.loads(path.read_text())
    assert record["stance"] == "neutral"
    assert record["stance_source"] == "model"
    assert record["stance_reason"] == "signals are mixed"
    assert record["decision"]["will_trade"] is False


def test_a_failed_model_call_stands_aside_and_never_reaches_the_broker(
    monkeypatch, tmp_path, capsys
):
    def explode(cls, cfg):
        raise AssertionError("no stance must not construct a Broker")

    monkeypatch.setattr(main_module.Broker, "from_env", classmethod(explode))
    request = httpx2.Request("POST", "https://api.anthropic.com/v1/messages")
    use_model(monkeypatch, FakeModel(error=anthropic.APIConnectionError(request=request)))
    path = tmp_path / "d.jsonl"
    a_bullish_run(monkeypatch, stance=None, mode="--submit", expected=0,
                  journal_path=str(path))
    record = json.loads(path.read_text())
    assert record["stance"] is None
    assert record["stance_source"] == "model"
    assert "APIConnectionError" in record["stance_reason"]
    assert record["decision"]["will_trade"] is False
    assert "stance: none" in capsys.readouterr().out


def test_unusable_features_never_reach_the_model(monkeypatch, tmp_path):
    model = use_model(monkeypatch, FakeModel())
    path = tmp_path / "d.jsonl"
    run_main_with(monkeypatch, quote=None, trade=None, stance=None,
                  journal_path=str(path))
    assert model.calls == []
    record = json.loads(path.read_text())
    assert record["stance"] is None
    assert "features unusable" in record["stance_reason"]


def test_a_missing_api_key_fails_before_any_market_data(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    client = StubClient(None, None)
    fetched = []
    client.get_clock = lambda: fetched.append("clock")
    monkeypatch.setattr(
        main_module.AlpacaClient, "from_env", classmethod(lambda cls, cfg: client)
    )
    with pytest.raises(KeyError, match="ANTHROPIC_API_KEY"):
        main_module.main(["--dry-run"])
    assert fetched == []
```

Extend `test_only_broker_reaches_an_order_endpoint`: add `"stance.py"` to `modules`, and append at the end of the test:

```python
    import re
    imports_anthropic = re.compile(r"^\s*(import|from)\s+anthropic\b", re.MULTILINE)
    for name, source in sources.items():
        if name != "stance.py":
            assert not imports_anthropic.search(source), f"{name} imports anthropic"
    assert imports_anthropic.search(sources["stance.py"])
```

- [ ] **Step 2: Run them to verify they fail**

Run: `.venv/bin/pytest tests/test_main.py -v`
Expected: FAIL — `AttributeError: <module 'main'> has no attribute 'anthropic_client'` for the `use_model` tests; `SystemExit` (`--stance` still required) for the missing-key test.

- [ ] **Step 3: Implement**

In `main.py`:

Imports — change and add:

```python
from config import FeatureConfig, OrderConfig, StanceConfig, StrategyConfig
from journal import DRY_RUN, MANUAL, MODEL, SUBMIT, append, build_record
from stance import anthropic_client, ask_stance
```

Module docstring — replace its first paragraph with:

```python
"""Entrypoint for the daily SPY spread agent.

Fetches market data, computes the features, takes a stance (from --stance,
or from the model in stance.py when it is omitted), decides, and either
prints the order it would place (--dry-run) or places it (--submit).
Exactly one of those flags is required: there is no default, and no bare
invocation that trades.
```

Parser — replace the `--stance` argument with:

```python
    parser.add_argument(
        "--stance",
        type=Stance,
        choices=list(Stance),
        metavar="{bullish,bearish,neutral}",  # choices renders the enum repr otherwise
        help="manual override; omit to ask the model",
    )
```

Directly after `client = AlpacaClient.from_env(cfg)`, build the model client so a missing key fails before any fetch:

```python
    stance_cfg = StanceConfig()
    model = None if args.stance is not None else anthropic_client(stance_cfg)
```

Replace these two lines:

```python
    decision = build_decision(features, chain, args.stance, account.equity, strategy_cfg)
    print(format_decision(decision))
```

with:

```python
    if model is None:
        stance, stance_source, stance_reason = args.stance, MANUAL, None
    else:
        call = ask_stance(features, stance_cfg, model)
        stance, stance_source, stance_reason = call.stance, MODEL, call.reason
        shown = "none" if stance is None else stance.value
        print(f"model stance: {shown} — {stance_reason}")

    if stance is None:
        decision = Decision(None, None, f"no stance: {stance_reason}")
    else:
        decision = build_decision(features, chain, stance, account.equity, strategy_cfg)
    print(format_decision(decision))
```

Replace the Task 3 bridge in `context` with:

```python
    context = dict(
        now=now, mode=mode, underlying=cfg.underlying, expiry=expiry,
        features=features, decision=decision,
        stance_source=stance_source, stance_reason=stance_reason,
    )
```

`Decision` is already imported from `strategy`.

- [ ] **Step 4: Run them to verify they pass**

Run: `.venv/bin/pytest tests/test_main.py -v`
Expected: all pass.

- [ ] **Step 5: Run the full suite**

Run: `.venv/bin/pytest -q`
Expected: all pass.

- [ ] **Step 6: Update CLAUDE.md**

In `CLAUDE.md` "Current state", add after the `journal.py` bullet:

```markdown
- `stance.py` — the only module that imports `anthropic`. Builds the prompt
  from the five features, makes one structured call to Claude, and returns
  `StanceCall(None, reason)` on any failure so the run stands aside.
```

Replace the `main.py` bullet with:

```markdown
- `main.py --dry-run|--submit [--stance <bullish|bearish|neutral>]` — fetches,
  computes, takes a stance (from the model when `--stance` is omitted),
  decides, and either prints the order it would place or places it.
  Exactly one mode flag is required.
```

Replace the credentials line with:

```markdown
Credentials come from `ALPACA_API_KEY_ID`, `ALPACA_API_SECRET_KEY` and, when
`--stance` is omitted, `ANTHROPIC_API_KEY`.
```

In "Domain context", change "Two invariants are asserted" to "Three invariants are asserted", and add after the `mleg` sentence: "`anthropic` is imported only in `stance.py`."

- [ ] **Step 7: Commit**

```bash
git add main.py tests/test_main.py CLAUDE.md
git commit -m "Let the model choose the stance when --stance is omitted

--stance stays as a manual override. A model failure stands aside and is
journalled; a missing ANTHROPIC_API_KEY fails before any market data is
fetched.

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

- [ ] **Step 8: Live check (manual, needs the real keys)**

Run: `source .venv/bin/activate && python main.py --dry-run`
Expected: the feature table, then `model stance: <bullish|bearish|neutral|none> — <reason>`, then the decision. A new line in `decisions.jsonl` with `"stance_source": "model"`. Report the output as-is; a `none` stance during closed hours (features stale) is a correct result, not a failure.
