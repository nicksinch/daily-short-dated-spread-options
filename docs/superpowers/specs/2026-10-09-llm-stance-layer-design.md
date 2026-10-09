# LLM Stance Layer — Design

**Date:** 2026-10-09
**Status:** Approved in conversation, awaiting written-spec review
**Predecessor:** `docs/superpowers/specs/2026-09-04-order-layer-design.md`

## Scope

Replace the hand-supplied `--stance` with a directional view chosen by Claude from the
features the data layer already computes. In the end-to-end pipeline this is the one
remaining box:

```
features ✅ → stance (LLM) → structure ✅ → strikes ✅ → sizing ✅ → order ✅ → log ✅
```

The model chooses **only** `bullish`, `bearish` or `neutral`. Strikes, size, price and
whether to submit stay deterministic and unchanged.

Out of scope: closing positions, scheduling, extra inputs (bars, news), evaluating the
model's calls over time, refusal fallbacks to another model.

## Decisions

Answered by the project owner during design.

| Decision | Choice |
| --- | --- |
| Provider | Anthropic, through the official `anthropic` Python SDK |
| Model | `claude-opus-5-5`, effort `medium`, set in config |
| Output | structured output via `client.messages.parse()` and a Pydantic model |
| Model input | the five features, the expiry, and whether the market is open — nothing else |
| Unusable features | no model call; stand aside |
| Model failure | stand aside and journal the reason; never raise to `main.py` |
| `--stance` | kept, optional, a manual override; omitted means ask the model |
| Credentials | `ANTHROPIC_API_KEY` from the environment, as the Alpaca keys are |

**Structured output rather than parsing.** `messages.parse()` constrains the reply to
the schema and returns a validated object, so there is no text parsing to write or test.
Strict tool use would give the same guarantee less directly, and Opus 5.5 cannot be
forced to call a tool.

**No server-side refusal fallback.** The SDK guidance enables it by default for Opus
5.5. It is left out because a refusal already stands aside like any other failure, and
the project prefers the shorter code.

## Module layout

```
config.py     + StanceConfig                                          (changed)
stance.py     prompt, the one Anthropic call, StanceCall              (new)
strategy.py   _unusable_features → unusable_features; Decision.stance may be None (changed)
journal.py    + stance_source, stance_reason; stance may be null      (changed)
main.py       --stance optional; asks the model when it is absent     (changed)
tests/test_stance.py                                                  (new)
requirements.txt  + anthropic, pinned                                 (changed)
```

`stance.py` is the only module that imports `anthropic`, as `broker.py` is the only one
that reaches an order endpoint. `main.py` constructs the client only when `--stance` is
absent, so a manual run needs no Anthropic key.

## Configuration

```python
@dataclass(frozen=True)
class StanceConfig:
    model: str = "claude-opus-5-5"
    effort: str = "medium"       # the model's default; set explicitly so it is visible
    max_tokens: int = 8000       # thinking counts against this, so not a one-word budget
    timeout_seconds: float = 60.0
```

## `stance.py`

```python
@dataclass(frozen=True)
class StanceCall:
    stance: Stance | None   # None: no usable answer, stand aside
    reason: str             # the model's reason, or why there is no stance


class StanceReply(BaseModel):          # the structured-output schema
    stance: Literal["bullish", "bearish", "neutral"]
    reason: str


SYSTEM_PROMPT: str                     # fixed text, below
def build_prompt(features: FeatureSet) -> str: ...           # pure
def ask_stance(features, cfg: StanceConfig, client) -> StanceCall: ...
```

`ask_stance`, in order:

1. If `unusable_features(features)` is non-empty, return
   `StanceCall(None, "features unusable: …")` without calling the client.
2. Call `client.messages.parse(model=…, max_tokens=…, output_config={"effort": …},
   system=SYSTEM_PROMPT, messages=[user: build_prompt(features)],
   output_format=StanceReply)` with the configured timeout.
3. Map the result:
   - `anthropic.APIError` (connection, timeout, rate limit, any status) →
     `StanceCall(None, "model call failed: <ExceptionType>: <message>")`;
   - `stop_reason` of `refusal` or `max_tokens`, or no `parsed_output` →
     `StanceCall(None, "no usable model reply: <stop_reason>")`;
   - otherwise `StanceCall(Stance(reply.stance), reply.reason)`.

The SDK's own retries (two, on connection errors, 429 and 5xx) are kept; nothing is
added on top.

### System prompt

> You give a one-day directional view on SPY for a defined-risk options strategy. Your
> stance decides the trade:
> - bullish → sell a put credit spread (short put near 0.30 delta, 1DTE). It profits if
>   SPY stays above the short strike through tomorrow's close.
> - bearish → sell a call credit spread (short call near 0.30 delta, 1DTE). It profits
>   if SPY stays below the short strike.
> - neutral → no trade today.
>
> You are given only the features below; you have no news or other data. Choose neutral
> when the features don't support a direction with reasonable confidence: standing aside
> costs nothing. Give your stance and a one-sentence reason grounded in the features.

The prompt explains what each feature means and what each stance leads to. It gives no
rules ("if spot/sma20 > 1 then bullish"): rules belong in code, and a model applying
them would add nothing. The system prompt is the same on every run, and everything that
changes daily is in the user message.

### User message (`build_prompt`)

```
SPY features as of 2026-10-09T10:15:00-04:00 (market open)
Option expiry: 2026-10-10 (1DTE)

spot          671.42
spot/sma20    1.0123   (spot is 1.23% above its 20-day average)
spot/sma50    1.0310   (spot is 3.10% above its 50-day average)
rv20          0.1180   (20-day realized volatility, annualized)
atm_iv        0.1450   (at-the-money implied volatility for the expiry, annualized)
```

"market open" reads "market closed" when the clock says so. Every value printed here is
also in the journal, so any run's prompt can be rebuilt from its journal line.

## `strategy.py`

- `_unusable_features` is renamed `unusable_features` so `stance.py` reuses it rather
  than restating what "usable" means.
- `Decision.stance` becomes `Stance | None`. `build_decision` is unchanged and still
  requires a `Stance`; a `None` stance never reaches it.

## `main.py`

- `--stance` loses `required=True`; its help text becomes "manual override; omit to ask
  the model".
- With `--stance`: `stance, source, stance_reason = args.stance, "manual", None`.
- Without it: `call = ask_stance(features, StanceConfig(), anthropic.Anthropic(timeout=…))`;
  `source = "model"`, `stance_reason = call.reason`.
- If the stance is `None`: `decision = Decision(None, None, f"no stance: {call.reason}")`
  and the existing stand-aside path runs (journal, exit 0). Otherwise
  `build_decision(...)` as today.
- `format_decision` prints `stance: none` for a `None` stance and, for a model stance,
  the model's reason on the line after it.

Because a `None` stance takes the stand-aside path, it returns before a `Broker` is
constructed, as every stand-aside already does.

## `journal.py`

`build_record` gains `stance_source: str` and `stance_reason: str | None`, written as
top-level fields beside `stance`. `stance` is written as `null` when the decision has
none. Existing fields are unchanged.

## Testing

pytest, no network. `tests/test_stance.py` uses a fake client whose `messages.parse`
returns a canned object or raises.

`build_prompt`:
- contains every feature value and the expiry;
- says "market open" or "market closed" from the feature set.

`ask_stance`:
- a valid parsed reply → that stance and reason;
- an unusable feature → the client is never called, and the reason names the feature;
- `APIConnectionError`, `RateLimitError`, `APIStatusError` → stance `None`, reason
  carries the exception type;
- `stop_reason` `refusal`, `stop_reason` `max_tokens`, and a missing `parsed_output` →
  stance `None`.

`tests/test_main.py`, changed and extended:
- `test_the_stance_flag_is_required` is replaced: omitting `--stance` now asks the model;
- with `--stance`, the model is never called and the journal records `"manual"`;
- without it, the model's stance drives `build_decision` and the journal records
  `"model"` and its reason;
- a failed model call stands aside, journals `stance: null` with the reason, exits 0,
  and constructs no `Broker`.

`tests/test_journal.py`: the two new fields, and a `null` stance round-trips.

Manual check after implementation, not in the suite: one live
`python main.py --dry-run` with no `--stance`.

## Deliberate omissions

1. **No refusal fallback model.** A refusal stands aside. Reasoning under Decisions.
2. **No extra inputs.** Bars and news would make calls harder to explain and runs harder
   to reproduce; the five features are what the journal can show.
3. **No evaluation of the model's calls.** The journal now records stance, source and
   reason next to the outcome, which is what a later evaluation would read.
4. **No prompt caching.** The prompt is far below the minimum cacheable length, and the
   agent runs once a day.
