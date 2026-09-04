# Strategy Layer Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn a directional stance plus a `FeatureSet` and an option chain into a fully sized, defined-risk SPY credit spread proposal — or an explained decision to stand aside.

**Architecture:** One new pure module, `strategy.py`, holding selection and sizing arithmetic plus a single `build_decision` assembler that applies seven gates in order. It performs no I/O; every input is passed in, mirroring how `features.py` relates to `data.py`. `data.py` gains a single `get_account()` call for equity, `config.py` gains a `StrategyConfig`, and `main.py --dry-run` gains a `--stance` flag. Nothing in this layer can reach an order endpoint.

**Tech Stack:** Python 3.14, standard library only (`dataclasses`, `enum`, `math`), `requests` for the one new REST call, pytest. Run tests with `.venv/bin/pytest`.

**Spec:** `docs/superpowers/specs/2026-09-04-strategy-layer-design.md`

## Global Constraints

- Prefer simple code. Do not add features, abstractions, or configuration the spec does not call for.
- No numeric literal appears inside a function body; every tunable lives in `config.py`.
- `strategy.py` performs **no I/O**. It imports `OptionQuote` from `data.py` and `FeatureSet`/`Status` from `features.py` as record types only, never `AlpacaClient`.
- Typed results cover expected market conditions. Exceptions stay reserved for HTTP, network and programming errors.
- **No order construction and no order submission.** The scope-guard test in `tests/test_main.py` forbids the tokens `/v2/orders`, `order_class`, `mleg`, `position_intent` in every project module, and `strategy.py` joins that list in Task 7.
- Delta target `0.30`, spread width `$5.00`, risk fraction `0.01` (1% of equity), minimum credit fraction `0.10` of width, contract multiplier `100`, strike band `$20`.
- Short-leg tie-break: nearest absolute delta first, **lower** absolute delta on a tie.
- Credit basis is `short.bid - long.ask`, never a mid.
- Tests import modules from the repository root (`from strategy import ...`), as the existing tests do.

---

### Task 1: StrategyConfig and the widened strike band

**Files:**
- Modify: `config.py:29-34` (remove `delta_target` from `FeatureConfig`, widen `strike_band_dollars`), then append `StrategyConfig`
- Modify: `tests/test_config.py:23-24`
- Test: `tests/test_config.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `config.StrategyConfig(delta_target=0.30, spread_width_dollars=5.0, risk_fraction=0.01, min_credit_fraction=0.10, contract_multiplier=100)`, frozen. `FeatureConfig.strike_band_dollars == 20` and `FeatureConfig` no longer has `delta_target`.

- [ ] **Step 1: Write the failing tests**

In `tests/test_config.py`, change the existing `assert cfg.strike_band_dollars == 8` to `== 20`, delete the `assert cfg.delta_target == 0.30` line from `test_defaults_match_the_design`, and add at the end of the file:

```python
from config import FeatureConfig, StrategyConfig


def test_feature_config_no_longer_carries_the_delta_target():
    # It moved to StrategyConfig, which is the layer that uses it.
    assert not hasattr(FeatureConfig(), "delta_target")


def test_strategy_defaults_match_the_design():
    cfg = StrategyConfig()
    assert cfg.delta_target == 0.30
    assert cfg.spread_width_dollars == 5.0
    assert cfg.risk_fraction == 0.01
    assert cfg.min_credit_fraction == 0.10
    assert cfg.contract_multiplier == 100


def test_strategy_config_is_frozen():
    cfg = StrategyConfig()
    with pytest.raises(dataclasses.FrozenInstanceError):
        cfg.delta_target = 0.5
```

Merge the new `from config import ...` line into the existing import at the top rather than leaving two.

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/test_config.py -v`
Expected: FAIL with `ImportError: cannot import name 'StrategyConfig' from 'config'`.

- [ ] **Step 3: Write the implementation**

In `config.py`, delete the `delta_target` line and its comment from `FeatureConfig`, change the band, and append the new dataclass:

```python
    # Dollar half-width around spot, which equals a strike count only on a $1
    # grid (SPY's grid today). $20 leaves room for a $5 protective wing beyond
    # a short strike a few points out of the money.
    strike_band_dollars: int = 20
```

```python
@dataclass(frozen=True)
class StrategyConfig:
    """Tunables for turning a stance into a sized spread.

    Separate from FeatureConfig because that class is about computing
    features, not about what to do with them.
    """

    delta_target: float = 0.30          # absolute delta of the short leg
    spread_width_dollars: float = 5.0   # long leg this far further OTM
    risk_fraction: float = 0.01         # max risk as a share of account equity
    min_credit_fraction: float = 0.10   # reject credit below this share of width
    contract_multiplier: int = 100      # US equity options, shares per contract
```

- [ ] **Step 4: Run the whole suite**

Run: `.venv/bin/pytest -v`
Expected: PASS. `tests/test_main.py::test_chain_band_is_centred_on_the_resolved_spot` reads the band from `FeatureConfig()`, so it follows the change automatically. If any test fails on a missing `delta_target`, that is a real reference to fix now.

- [ ] **Step 5: Commit**

```bash
git add config.py tests/test_config.py
git commit -m "Add StrategyConfig and widen the strike band to \$20"
```

---

### Task 2: Account equity from Alpaca

**Files:**
- Modify: `data.py` (add the `Account` record beside the other records, add `get_account` to `AlpacaClient`)
- Test: `tests/test_data.py`

**Interfaces:**
- Consumes: `FeatureConfig` (already used by `AlpacaClient.__init__`).
- Produces: `data.Account(equity: float)` and `AlpacaClient.get_account() -> Account`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_data.py`:

```python
def test_get_account_parses_equity_from_a_json_string():
    # Alpaca returns the account's numeric fields as JSON strings.
    client = make_client({"/v2/account": {"equity": "100000.42", "buying_power": "200000"}})
    assert client.get_account().equity == pytest.approx(100000.42)


def test_get_account_hits_the_trading_api():
    client = make_client({"/v2/account": {"equity": "100000"}})
    client.get_account()
    url, _ = client._session.calls[0]
    assert url == "https://paper-api.alpaca.markets/v2/account"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/test_data.py -k account -v`
Expected: FAIL with `AttributeError: 'AlpacaClient' object has no attribute 'get_account'`.

- [ ] **Step 3: Write the implementation**

Add the record next to `StockTrade` in `data.py`:

```python
@dataclass(frozen=True)
class Account:
    equity: float
```

Add the method to `AlpacaClient`, next to `get_clock`:

```python
    def get_account(self) -> Account:
        """Current account equity.

        No `None` path: the account always exists, so an HTTP failure raises
        like every other transport problem here. Alpaca returns this
        endpoint's numeric fields as JSON strings.
        """
        payload = self._get(self.TRADING_URL, "/v2/account")
        return Account(equity=float(payload["equity"]))
```

`_get(self, base, path, params=None)` already defaults its params, so this call passes none.

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/test_data.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add data.py tests/test_data.py
git commit -m "Fetch account equity from the trading API"
```

---

### Task 3: Strategy result types and the stance-to-structure map

**Files:**
- Create: `strategy.py`
- Test: `tests/test_strategy.py` (create)

**Interfaces:**
- Consumes: `config.StrategyConfig` (Task 1).
- Produces: `strategy.Stance` (`BULLISH`/`BEARISH`/`NEUTRAL`, values `"bullish"`/`"bearish"`/`"neutral"`), `strategy.SpreadLeg`, `strategy.SpreadProposal`, `strategy.Decision` with a `will_trade` property, and `strategy.structure_for(stance) -> str | None` returning `"put_credit"`, `"call_credit"` or `None`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_strategy.py`:

```python
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_strategy.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'strategy'`.

- [ ] **Step 3: Write the implementation**

Create `strategy.py`:

```python
"""Stance to sized spread. Deterministic, and performs no I/O.

Everything consequential about a trade is decided here: which structure a
stance implies, which strikes it lands on, what it can lose and how many
contracts fit the risk budget. The stance itself is an argument -- the layer
that produces it does not exist yet, and this module does not care how it is
produced.

Standing aside is a value, not an exception: `Decision` carries a reason in
both directions so a quiet day is as explainable as a busy one.
"""

from dataclasses import dataclass
from enum import Enum


class Stance(str, Enum):
    """A directional view. The only thing a later LLM layer will choose."""

    BULLISH = "bullish"
    BEARISH = "bearish"
    NEUTRAL = "neutral"


PUT_CREDIT = "put_credit"
CALL_CREDIT = "call_credit"
CALL, PUT = "C", "P"
SELL, BUY = "sell", "buy"


@dataclass(frozen=True)
class SpreadLeg:
    symbol: str  # OCC symbol, ready for the order layer
    strike: float
    right: str  # "C" or "P"
    side: str  # "sell" or "buy"
    delta: float
    bid: float
    ask: float


@dataclass(frozen=True)
class SpreadProposal:
    structure: str
    short_leg: SpreadLeg
    long_leg: SpreadLeg
    credit: float  # per share
    max_loss_per_contract: float  # dollars
    quantity: int  # contracts
    total_risk: float  # dollars
    risk_budget: float  # dollars


@dataclass(frozen=True)
class Decision:
    stance: Stance
    proposal: SpreadProposal | None
    reason: str

    @property
    def will_trade(self) -> bool:
        return self.proposal is not None


def structure_for(stance: Stance) -> str | None:
    """The credit spread a stance implies, or None to stand aside.

    A credit spread sells the side the view is against: a bullish view sells
    puts below the market, a bearish view sells calls above it.
    """
    if stance is Stance.BULLISH:
        return PUT_CREDIT
    if stance is Stance.BEARISH:
        return CALL_CREDIT
    return None
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/pytest tests/test_strategy.py -v`
Expected: PASS, 5 tests.

- [ ] **Step 5: Commit**

```bash
git add strategy.py tests/test_strategy.py
git commit -m "Add strategy result types and the stance-to-structure map"
```

---

### Task 4: Strike selection

**Files:**
- Modify: `strategy.py`
- Test: `tests/test_strategy.py`

**Interfaces:**
- Consumes: `SpreadLeg`, `PUT`, `CALL` from Task 3; `data.OptionQuote(symbol, strike, right, bid, ask, iv, delta, timestamp)`.
- Produces: `select_short_leg(chain, right, delta_target) -> OptionQuote | None` and `select_long_leg(chain, right, short_strike, width) -> OptionQuote | None`.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_strategy.py`, including this fixture helper which later tasks reuse:

```python
from datetime import datetime, timezone

from data import OptionQuote
from strategy import select_long_leg, select_short_leg

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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/test_strategy.py -v`
Expected: FAIL with `ImportError: cannot import name 'select_short_leg' from 'strategy'`.

- [ ] **Step 3: Write the implementation**

Add to `strategy.py` (extend the imports with `from collections.abc import Sequence` and `from data import OptionQuote`):

```python
def select_short_leg(
    chain: Sequence[OptionQuote], right: str, delta_target: float
) -> OptionQuote | None:
    """The contract whose absolute delta is nearest `delta_target`.

    A delta of zero is treated as absent rather than as a real reading: at
    0DTE Alpaca omits greeks and the CLI synthesises zeros, and a genuine 0.00
    would otherwise rank as the furthest strike from the money.

    Ties go to the lower absolute delta -- the further out of the money of two
    equally-distant strikes, so the arbitrary case is consistently arbitrary.
    """
    candidates = [
        q for q in chain if q.right == right and q.delta is not None and q.delta != 0
    ]
    if not candidates:
        return None
    return min(candidates, key=lambda q: (abs(abs(q.delta) - delta_target), abs(q.delta)))


def select_long_leg(
    chain: Sequence[OptionQuote], right: str, short_strike: float, width: float
) -> OptionQuote | None:
    """The protective wing, one width further out of the money.

    Exact float comparison is safe: strikes are integer thousandths divided by
    1000, and a $5.00 offset from any strike on SPY's grid is exact. Returns
    None when that strike is not in the fetched band.
    """
    wanted = short_strike - width if right == PUT else short_strike + width
    return next((q for q in chain if q.right == right and q.strike == wanted), None)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/test_strategy.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add strategy.py tests/test_strategy.py
git commit -m "Select the short strike by delta and the long strike by width"
```

---

### Task 5: Credit, max loss and position size

**Files:**
- Modify: `strategy.py`
- Test: `tests/test_strategy.py`

**Interfaces:**
- Consumes: `OptionQuote`, the `a_put` helper from Task 4.
- Produces: `net_credit(short, long, width) -> float | None`, `max_loss_per_contract(width, credit, multiplier) -> float`, `position_size(equity, risk_fraction, max_loss) -> int`.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_strategy.py`:

```python
from strategy import max_loss_per_contract, net_credit, position_size


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
```

`net_credit` takes the width as a third argument: it needs it to reject a credit that meets or exceeds the width, which would leave nothing to lose and divide the sizing by zero.

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/test_strategy.py -v`
Expected: FAIL with `ImportError: cannot import name 'net_credit' from 'strategy'`.

- [ ] **Step 3: Write the implementation**

Add to `strategy.py` (extend the imports with `import math`):

```python
def net_credit(
    short: OptionQuote, long: OptionQuote, width: float
) -> float | None:
    """Credit received per share, or None if the quotes cannot support one.

    The short leg's bid and the long leg's ask are the sides actually
    received and paid; the other two play no part and are not demanded.

    A credit at or above the width is rejected rather than returned: it would
    make max loss zero or negative, and a spread that cannot lose is a
    quoting fault, not an opportunity.
    """
    if not short.bid or not long.ask:
        return None
    credit = short.bid - long.ask
    if credit <= 0 or credit >= width:
        return None
    return credit


def max_loss_per_contract(width: float, credit: float, multiplier: int) -> float:
    """Worst case per contract, in dollars. The credit is already received."""
    return (width - credit) * multiplier


def position_size(equity: float, risk_fraction: float, max_loss: float) -> int:
    """Whole contracts that fit the risk budget.

    Zero is a legitimate answer: the budget cannot fund one contract.
    `max_loss` is positive by construction -- `net_credit` rejects any credit
    at or above the width -- so there is no division by zero to guard.
    """
    return math.floor(equity * risk_fraction / max_loss)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/test_strategy.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add strategy.py tests/test_strategy.py
git commit -m "Compute the spread credit, max loss and contract count"
```

---

### Task 6: build_decision and its gates

**Files:**
- Modify: `strategy.py`
- Test: `tests/test_strategy.py`

**Interfaces:**
- Consumes: everything from Tasks 3-5, `config.StrategyConfig`, `features.FeatureSet` and `features.Feature`.
- Produces: `build_decision(features, chain, stance, equity, cfg) -> Decision`.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_strategy.py`:

```python
from datetime import date

from config import StrategyConfig
from features import Feature, FeatureSet
from strategy import build_decision

CFG = StrategyConfig()


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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/test_strategy.py -v`
Expected: FAIL with `ImportError: cannot import name 'build_decision' from 'strategy'`.

- [ ] **Step 3: Write the implementation**

Add to `strategy.py` (extend the imports with `from config import StrategyConfig` and `from features import FeatureSet`):

```python
FEATURE_NAMES = ("spot", "rv20", "spot_over_sma20", "spot_over_sma50", "atm_iv")


def _unusable_features(features: FeatureSet) -> list[str]:
    """Names and reasons for every feature that is not ok."""
    faults = []
    for name in FEATURE_NAMES:
        feature = getattr(features, name)
        if not feature.usable:
            faults.append(f"{name} ({feature.status.value}: {feature.detail})")
    return faults


def _leg(quote: OptionQuote, side: str) -> SpreadLeg:
    return SpreadLeg(
        symbol=quote.symbol,
        strike=quote.strike,
        right=quote.right,
        side=side,
        delta=quote.delta,
        bid=quote.bid,
        ask=quote.ask,
    )


def build_decision(
    features: FeatureSet,
    chain: Sequence[OptionQuote],
    stance: Stance,
    equity: float,
    cfg: StrategyConfig,
) -> Decision:
    """Turn a stance into a sized spread, or explain why not.

    The gates run in a deliberate order. The stance is checked first, so a
    neutral day does not report a data problem it never depended on. The
    features are checked next, before any strike work, because an unusable
    spot invalidates the selection that would follow it.
    """
    structure = structure_for(stance)
    if structure is None:
        return Decision(stance, None, "neutral stance: no directional edge")

    faults = _unusable_features(features)
    if faults:
        return Decision(stance, None, f"features not ok: {', '.join(faults)}")

    right = PUT if structure == PUT_CREDIT else CALL
    short = select_short_leg(chain, right, cfg.delta_target)
    if short is None:
        return Decision(
            stance, None, f"no {right} contract with a usable delta in the chain"
        )

    width = cfg.spread_width_dollars
    long = select_long_leg(chain, right, short.strike, width)
    if long is None:
        wanted = short.strike - width if right == PUT else short.strike + width
        return Decision(
            stance, None, f"no {right} at strike {wanted} to protect the {short.strike} short"
        )

    credit = net_credit(short, long, width)
    if credit is None:
        return Decision(
            stance,
            None,
            f"no usable credit from the {short.strike}/{long.strike} {right} spread",
        )

    minimum = cfg.min_credit_fraction * width
    if credit < minimum:
        return Decision(
            stance,
            None,
            f"credit {credit:.2f} below minimum {minimum:.2f} "
            f"({cfg.min_credit_fraction:.0%} of {width:.2f} width)",
        )

    max_loss = max_loss_per_contract(width, credit, cfg.contract_multiplier)
    budget = equity * cfg.risk_fraction
    quantity = position_size(equity, cfg.risk_fraction, max_loss)
    if quantity < 1:
        return Decision(
            stance,
            None,
            f"max loss {max_loss:.2f} per contract exceeds the {budget:.2f} risk budget",
        )

    proposal = SpreadProposal(
        structure=structure,
        short_leg=_leg(short, SELL),
        long_leg=_leg(long, BUY),
        credit=credit,
        max_loss_per_contract=max_loss,
        quantity=quantity,
        total_risk=quantity * max_loss,
        risk_budget=budget,
    )
    return Decision(
        stance,
        proposal,
        f"sell {short.strike}{right} / buy {long.strike}{right} "
        f"for {credit:.2f}, {quantity} contract(s)",
    )
```

- [ ] **Step 4: Run the whole suite**

Run: `.venv/bin/pytest -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add strategy.py tests/test_strategy.py
git commit -m "Assemble the trade decision behind seven explicit gates"
```

---

### Task 7: Wire the decision into the dry run

**Files:**
- Modify: `main.py` (imports, `--stance` argument, account fetch, `format_decision`, `main`)
- Modify: `tests/test_main.py:60-70` (scope guard) and `tests/test_main.py:92-96` (`run_main_with`), plus the `StubClient`
- Test: `tests/test_main.py`

**Interfaces:**
- Consumes: `strategy.build_decision`, `strategy.Stance`, `strategy.Decision`, `config.StrategyConfig`, `data.AlpacaClient.get_account`.
- Produces: `main.format_decision(decision) -> str`; `main.main(["--dry-run", "--stance", "bullish"])`.

- [ ] **Step 1: Write the failing tests**

In `tests/test_main.py`: add `"strategy.py"` to the module tuple in `test_no_module_contains_order_placing_code`; add a `get_account` method to `StubClient`; and give `run_main_with` a stance. Replace the existing `run_main_with` with:

```python
    def get_account(self):
        return Account(equity=100_000.0)


def run_main_with(monkeypatch, quote, trade, stance="neutral"):
    client = StubClient(quote, trade)
    monkeypatch.setattr(main_module.AlpacaClient, "from_env", classmethod(lambda cls, cfg: client))
    assert main_module.main(["--dry-run", "--stance", stance]) == 0
    return client
```

Import `Account` from `data` and add these tests:

```python
from data import Account
from main import format_decision
from strategy import Decision, SpreadLeg, SpreadProposal, Stance


def a_proposal():
    return SpreadProposal(
        structure="put_credit",
        short_leg=SpreadLeg("SPY260905P00761000", 761.0, "P", "sell", -0.3020, 1.60, 1.68),
        long_leg=SpreadLeg("SPY260905P00756000", 756.0, "P", "buy", -0.1810, 0.88, 0.95),
        credit=0.65, max_loss_per_contract=435.0, quantity=2,
        total_risk=870.0, risk_budget=1000.0,
    )


def test_a_trade_prints_both_legs_and_the_sizing():
    text = format_decision(Decision(Stance.BULLISH, a_proposal(), "sell 761.0P / buy 756.0P"))
    assert "put_credit" in text
    assert "SPY260905P00761000" in text and "SPY260905P00756000" in text
    assert "sell" in text and "buy" in text
    assert "0.65" in text and "435.00" in text and "870.00" in text
    assert "2" in text


def test_standing_aside_prints_the_reason_in_place_of_legs():
    text = format_decision(
        Decision(Stance.NEUTRAL, None, "neutral stance: no directional edge")
    )
    assert "stand aside" in text
    assert "neutral stance" in text
    assert "SPY26" not in text


def test_the_stance_flag_is_required(monkeypatch):
    monkeypatch.setattr(
        main_module.AlpacaClient, "from_env",
        classmethod(lambda cls, cfg: StubClient(None, None)),
    )
    with pytest.raises(SystemExit):
        main_module.main(["--dry-run"])


def test_an_unknown_stance_is_rejected(monkeypatch):
    monkeypatch.setattr(
        main_module.AlpacaClient, "from_env",
        classmethod(lambda cls, cfg: StubClient(None, None)),
    )
    with pytest.raises(SystemExit):
        main_module.main(["--dry-run", "--stance", "sideways"])


def test_the_dry_run_reaches_the_decision(monkeypatch, capsys):
    run_main_with(
        monkeypatch,
        quote=StockQuote(bid=766.0, ask=767.0, bid_size=1, ask_size=1, timestamp=NOW),
        trade=None,
        stance="bullish",
    )
    assert "decision:" in capsys.readouterr().out
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/test_main.py -v`
Expected: FAIL — `ImportError: cannot import name 'format_decision' from 'main'`, and the existing tests fail on the now-required `--stance`.

- [ ] **Step 3: Write the implementation**

In `main.py`, extend the imports:

```python
from config import FeatureConfig, StrategyConfig
from strategy import Decision, Stance, build_decision
```

Add the formatter below `format_feature_set`:

```python
def format_decision(decision: Decision) -> str:
    """The decision, and either its legs or the reason there are none."""
    lines = [f"stance: {decision.stance.value}"]
    if decision.proposal is None:
        lines.append(f"decision: stand aside — {decision.reason}")
        return "\n".join(lines)

    p = decision.proposal
    lines.append(f"decision: trade {p.structure}")
    for leg in (p.short_leg, p.long_leg):
        lines.append(
            f"  {leg.side:<5} {leg.symbol}  {leg.strike}{leg.right}  "
            f"delta {leg.delta:+.4f}  bid {leg.bid:.2f}  ask {leg.ask:.2f}"
        )
    lines.append(
        f"  credit {p.credit:.2f}  max loss/contract ${p.max_loss_per_contract:.2f}  "
        f"quantity {p.quantity}  total risk ${p.total_risk:.2f} "
        f"of ${p.risk_budget:.2f} budget"
    )
    return "\n".join(lines)
```

In `main()`, add the argument after `--dry-run`:

```python
    parser.add_argument(
        "--stance",
        required=True,
        type=Stance,
        choices=list(Stance),
        help="directional view; supplied by hand until the LLM layer exists",
    )
    args = parser.parse_args(argv)
```

(The existing call discards the parse result — capture it as `args`.)

Then add the config and the account fetch beside the other fetches, and print the decision after the features:

```python
    strategy_cfg = StrategyConfig()
    ...
    account = client.get_account()
    ...
    print(format_feature_set(features, cfg.underlying))
    print()
    decision = build_decision(features, chain, args.stance, account.equity, strategy_cfg)
    print(format_decision(decision))
    return 0
```

- [ ] **Step 4: Run the whole suite**

Run: `.venv/bin/pytest -v`
Expected: PASS, every test.

- [ ] **Step 5: Verify against the live paper account**

Run: `source .venv/bin/activate && python main.py --dry-run --stance bullish`
Expected: the feature block, then a decision. Either outcome is a pass — a proposal, or a stand-aside whose reason matches the market conditions at the time. Confirm no order was placed: the run prints nothing about orders, and the scope guard test already forbids the endpoints.

- [ ] **Step 6: Commit**

```bash
git add main.py tests/test_main.py
git commit -m "Print the trade decision from the dry run"
```

---

### Task 8: Update the project documentation

**Files:**
- Modify: `CLAUDE.md` (the "Current state" and "Domain context" sections)

**Interfaces:**
- Consumes: the finished implementation.
- Produces: no code.

- [ ] **Step 1: Update CLAUDE.md**

In "Current state", add `strategy.py` to the module list and correct the `main.py` line:

```markdown
- `strategy.py` — stance to sized spread: structure, strike selection by
  delta, credit, max loss and position size. Pure; no I/O.
- `main.py --dry-run --stance <bullish|bearish|neutral>` — fetches, computes,
  decides, prints, exits. Places no orders.
```

In "Domain context", replace the sentence saying strike selection and sizing are not implemented with:

```markdown
A daily defined-risk option spread on SPY. Strike selection and sizing are
implemented in `strategy.py` and documented in
`docs/superpowers/specs/2026-09-04-strategy-layer-design.md`. Order
construction and submission are deliberately not implemented yet.
```

- [ ] **Step 2: Verify the claims are true**

Run: `.venv/bin/pytest -v && grep -n "strategy.py" CLAUDE.md`
Expected: tests pass and the new lines are present. Do not describe anything the code does not do.

- [ ] **Step 3: Commit**

```bash
git add CLAUDE.md
git commit -m "Describe the strategy layer in CLAUDE.md"
```

---

## Done when

- `.venv/bin/pytest` passes, including the scope guard covering `strategy.py`.
- `python main.py --dry-run --stance bullish` prints features and a decision.
- No module references an order endpoint.
