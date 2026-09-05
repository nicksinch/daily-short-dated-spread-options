# Order Layer Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn a `SpreadProposal` into a live multi-leg credit-spread order on the Alpaca paper account, confirm what happened to it, and append one line per run to a decision journal.

**Architecture:** Three new modules split on the seam this codebase already uses. `orders.py` is pure: it builds the `mleg` payload, classifies Alpaca's order statuses, and judges whether the account is already positioned. `broker.py` is the only module in the repository that can place an order; it performs the calls and returns the records `orders.py` defines. `journal.py` builds a JSON-native record (pure) and appends it (the one write). `config.py` gains an `OrderConfig`, and `main.py` replaces its `--dry-run`-only gate with `--dry-run | --submit`. `data.py`, `features.py` and `strategy.py` are untouched.

**Tech Stack:** Python 3.14, standard library only (`dataclasses`, `enum`, `json`, `time`, `sys`), `requests` for the new REST calls, pytest. Run tests with `.venv/bin/pytest`.

**Spec:** `docs/superpowers/specs/2026-09-04-order-layer-design.md`

## Global Constraints

- Prefer simple code. Do not add features, abstractions, or configuration the spec does not call for.
- No numeric literal appears inside a function body; every tunable lives in `config.py`. HTTP plumbing constants (timeouts, page limits) are module-level privates in the module that uses them, as `data.py` already does.
- `orders.py` and `journal.py`'s `build_record` perform **no I/O**. They import `SpreadProposal`/`Decision` from `strategy.py`, `FeatureSet` from `features.py` and `OrderConfig` from `config.py` as record types only.
- **`broker.py` imports `orders.py`, never the reverse.** All three record types — `OrderState`, `OrderRecord`, `OptionPosition` — live in `orders.py` so the pure guard predicate can use them without importing the I/O module. The spec lists them together without naming a home; this is that decision.
- **Exactly one module may reach an order endpoint.** `/v2/orders` appears in `broker.py` and nowhere else. The `mleg` vocabulary — `order_class`, `mleg`, `position_intent` — appears in `orders.py` and nowhere else. Task 9 replaces the old blanket scope-guard test with this narrower one.
- Transport and HTTP errors raise, as everywhere else in this codebase. The **single** exception is `journal.append`, which must never raise (Task 8).
- Order tunables: `time_in_force="day"`, `fill_poll_seconds=1.0`, `fill_timeout_seconds=15.0`, `journal_path="decisions.jsonl"`.
- **A credit is a negative `limit_price`.** Alpaca expresses an `mleg` net price as positive for a debit and negative for a credit. A positive price on a credit spread is *accepted and filled*, not rejected, while also permitting an inverted trade — this is the one failure in the layer that is silent and expensive.
- `qty` on an `mleg` order is the number of **spreads**, not contracts.
- Tests import modules from the repository root (`from orders import ...`), as the existing tests do.
- Every commit ends with these two trailers:

```
Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_014wa3goPT2PzCJSjQ7opWn3
```

## Worked example

Every task's fixtures use these numbers, carried from the strategy spec. SPY at 765.12, equity $100,000, bullish, 1DTE expiry 2026-09-05.

| Field | Value |
| --- | --- |
| Short leg | `SPY260905P00761000`, 761P, sell, delta −0.3020, bid 1.60, ask 1.68 |
| Long leg | `SPY260905P00756000`, 756P, buy, delta −0.1810, bid 0.88, ask 0.95 |
| Credit | 1.60 − 0.95 = **0.65** |
| Max loss per contract | (5.00 − 0.65) × 100 = **435.00** |
| Quantity | floor(1000 / 435) = **2** |
| Total risk / budget | **870.00** / **1000.00** |
| Payload `limit_price` | **`"-0.65"`** |
| Guard prefix | **`SPY260905`** |

---

### Task 1: OrderConfig

**Files:**
- Modify: `config.py` (append a third dataclass)
- Test: `tests/test_config.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `config.OrderConfig(time_in_force="day", fill_poll_seconds=1.0, fill_timeout_seconds=15.0, journal_path="decisions.jsonl")`, frozen.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_config.py`, merging `OrderConfig` into the existing `from config import ...` line rather than adding a second import:

```python
def test_order_defaults_match_the_design():
    cfg = OrderConfig()
    assert cfg.time_in_force == "day"
    assert cfg.fill_poll_seconds == 1.0
    assert cfg.fill_timeout_seconds == 15.0
    assert cfg.journal_path == "decisions.jsonl"


def test_order_config_is_frozen():
    cfg = OrderConfig()
    with pytest.raises(dataclasses.FrozenInstanceError):
        cfg.time_in_force = "gtc"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/test_config.py -v`
Expected: FAIL with `ImportError: cannot import name 'OrderConfig' from 'config'`

- [ ] **Step 3: Write minimal implementation**

Append to `config.py`:

```python
@dataclass(frozen=True)
class OrderConfig:
    """Tunables for placing the order and recording what happened.

    Separate from StrategyConfig for the same reason that class is separate
    from FeatureConfig: this is about submitting a spread, not about
    choosing one.
    """

    time_in_force: str = "day"          # options accept only day or gtc
    fill_poll_seconds: float = 1.0      # between order status re-fetches
    fill_timeout_seconds: float = 15.0  # then stop polling and record what was seen
    journal_path: str = "decisions.jsonl"
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/test_config.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add config.py tests/test_config.py
git commit -F - <<'MSG'
Add OrderConfig for the order layer's tunables

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_014wa3goPT2PzCJSjQ7opWn3
MSG
```

---

### Task 2: `build_order` and the negative limit price

**Files:**
- Create: `orders.py`
- Test: `tests/test_orders.py` (create)

**Interfaces:**
- Consumes: `config.OrderConfig` (Task 1); `strategy.SpreadProposal` and `strategy.SpreadLeg` (existing).
- Produces: `orders.build_order(proposal: SpreadProposal, cfg: OrderConfig) -> dict`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_orders.py`:

```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/test_orders.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'orders'`

- [ ] **Step 3: Write minimal implementation**

Create `orders.py`:

```python
"""Order construction and the vocabulary for what happens to an order.

Pure: no I/O, no requests, no sleeping. `broker.py` performs the calls and
returns the records defined here, which keeps the judging side -- what a
status means, whether the account is already positioned -- testable without
a socket.
"""

from dataclasses import dataclass

from config import OrderConfig
from strategy import SpreadProposal

MLEG = "mleg"
LIMIT = "limit"
RATIO_ONE = "1"

# Both legs open new exposure; closing is not this layer's business.
OPEN_INTENT = {"sell": "sell_to_open", "buy": "buy_to_open"}


def build_order(proposal: SpreadProposal, cfg: OrderConfig) -> dict:
    """The mleg payload that opens `proposal`.

    `limit_price` is negative because this is a credit spread: Alpaca
    expresses an mleg net price as positive for a debit and negative for a
    credit. A positive price here would not be rejected -- receiving 0.65
    satisfies "pay at most 0.65" -- it would merely also permit paying 0.64,
    inverting the trade while the fill still reads as normal.

    `qty` is the number of spreads. For an mleg order Alpaca defines qty as
    the number of units of the strategy, not the number of contracts. Both
    legs carry ratio_qty 1: a 1:1 vertical, and Alpaca requires the greatest
    common divisor across legs to be 1.

    Taking the whole proposal rather than loose arguments means there is no
    way to build a payload for a spread that never passed build_decision.
    """
    return {
        "order_class": MLEG,
        "qty": str(proposal.quantity),
        "type": LIMIT,
        "limit_price": f"{-proposal.credit:.2f}",
        "time_in_force": cfg.time_in_force,
        "legs": [
            {
                "symbol": leg.symbol,
                "ratio_qty": RATIO_ONE,
                "side": leg.side,
                "position_intent": OPEN_INTENT[leg.side],
            }
            for leg in (proposal.short_leg, proposal.long_leg)
        ],
    }
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/test_orders.py -v`
Expected: PASS, 6 tests

- [ ] **Step 5: Commit**

```bash
git add orders.py tests/test_orders.py
git commit -F - <<'MSG'
Build the multi-leg payload from a spread proposal

A credit is a negative mleg limit price. A positive one is accepted and
filled rather than rejected, and would also permit paying the same amount,
so the sign gets its own test.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_014wa3goPT2PzCJSjQ7opWn3
MSG
```

---

### Task 3: Order status classification

**Files:**
- Modify: `orders.py`
- Test: `tests/test_orders.py`

**Interfaces:**
- Consumes: nothing new.
- Produces: `orders.OrderState` (`FILLED`/`WORKING`/`DEAD`), `orders.OrderRecord(id, status, state, filled_qty, filled_avg_price, submitted_at)` frozen, `orders.classify(status: str) -> OrderState`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_orders.py`, merging the new names into the existing `from orders import ...` line:

```python
import pytest

from orders import OrderState, classify


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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/test_orders.py -v`
Expected: FAIL with `ImportError: cannot import name 'OrderState' from 'orders'`

- [ ] **Step 3: Write minimal implementation**

In `orders.py`, extend the imports and append below `build_order`:

```python
from datetime import datetime
from enum import Enum
```

```python
class OrderState(str, Enum):
    """What the program does about a status, not what the status is called."""

    FILLED = "filled"    # done, we are on
    WORKING = "working"  # not terminal; keep polling
    DEAD = "dead"        # terminal without a fill


_FILLED = {"filled"}
_DEAD = {"canceled", "expired", "rejected", "suspended", "done_for_day", "replaced"}


@dataclass(frozen=True)
class OrderRecord:
    id: str
    status: str  # Alpaca's own string, preserved rather than normalised
    state: OrderState
    filled_qty: float
    filled_avg_price: float | None
    submitted_at: datetime | None


def classify(status: str) -> OrderState:
    """What to do about an Alpaca order status.

    Anything not known to be terminal is WORKING, so a status Alpaca adds
    later keeps the poll running rather than being mistaken for a fill.
    `partially_filled` is WORKING deliberately: a two-spread order can fill
    one spread, and the remainder may still arrive.
    """
    if status in _FILLED:
        return OrderState.FILLED
    if status in _DEAD:
        return OrderState.DEAD
    return OrderState.WORKING
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/test_orders.py -v`
Expected: PASS, 22 tests

- [ ] **Step 5: Commit**

```bash
git add orders.py tests/test_orders.py
git commit -F - <<'MSG'
Collapse Alpaca's order statuses to filled, working or dead

An unrecognised status stays working, so a status added later keeps the
poll running rather than reading as a fill.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_014wa3goPT2PzCJSjQ7opWn3
MSG
```

---

### Task 4: The duplicate-position guard

**Files:**
- Modify: `orders.py`
- Test: `tests/test_orders.py`

**Interfaces:**
- Consumes: nothing new.
- Produces: `orders.OptionPosition(symbol: str, qty: float)` frozen; `orders.expiry_prefix(underlying: str, expiry: date) -> str`; `orders.existing_exposure(positions: Sequence[OptionPosition], working_orders: Sequence[Sequence[str]], underlying: str, expiry: date) -> str | None` returning a reason, or `None` to proceed.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_orders.py`:

```python
from datetime import date

from orders import OptionPosition, existing_exposure, expiry_prefix

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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/test_orders.py -v`
Expected: FAIL with `ImportError: cannot import name 'OptionPosition' from 'orders'`

- [ ] **Step 3: Write minimal implementation**

In `orders.py`, extend the imports and append:

```python
from collections.abc import Sequence
from datetime import date, datetime
```

```python
@dataclass(frozen=True)
class OptionPosition:
    symbol: str
    qty: float  # signed: negative is short


def expiry_prefix(underlying: str, expiry: date) -> str:
    """The leading characters every OCC symbol for this expiry shares."""
    return f"{underlying}{expiry:%y%m%d}"


def existing_exposure(
    positions: Sequence[OptionPosition],
    working_orders: Sequence[Sequence[str]],
    underlying: str,
    expiry: date,
) -> str | None:
    """Why the account is already positioned on `expiry`, or None to proceed.

    Scoped to the expiry rather than to the strikes: re-running after spot
    has moved would pick different strikes and slip past a symbol-exact
    check. Scoped to the expiry rather than to every option: yesterday's
    1DTE spread is still open this morning and must not stand today's trade
    aside.

    Returns the reason string that gets printed and journalled, so a blocked
    run explains itself the way a stand-aside does.
    """
    prefix = expiry_prefix(underlying, expiry)
    held = sorted(p.symbol for p in positions if p.symbol.startswith(prefix))
    if held:
        return f"already holding {', '.join(held)} expiring {expiry}"
    for legs in working_orders:
        matched = sorted(s for s in legs if s.startswith(prefix))
        if matched:
            return f"a working order already covers {', '.join(matched)} expiring {expiry}"
    return None
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/test_orders.py -v`
Expected: PASS, 30 tests

- [ ] **Step 5: Commit**

```bash
git add orders.py tests/test_orders.py
git commit -F - <<'MSG'
Refuse a second spread on an expiry already covered

Scoped to the expiry: matching exact strikes would let a re-run through
after spot moved, and matching every option position would block every day
after the first while yesterday's 1DTE spread runs off.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_014wa3goPT2PzCJSjQ7opWn3
MSG
```

---

### Task 5: `broker.py` — the client and its read paths

**Files:**
- Create: `broker.py`
- Test: `tests/test_broker.py` (create)

**Interfaces:**
- Consumes: `config.OrderConfig`; `orders.OptionPosition`.
- Produces: `broker.Broker(key_id, secret_key, cfg, session=None)`, `Broker.from_env(cfg)`, `Broker.open_option_positions() -> list[OptionPosition]`, `Broker.open_orders() -> list[list[str]]`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_broker.py`:

```python
import pytest

from broker import Broker
from config import OrderConfig
from orders import OptionPosition


class StubSession:
    """Stands in for requests.Session. Returns a canned payload per URL suffix."""

    def __init__(self, routes):
        self.routes = routes
        self.headers = {}
        self.gets = []
        self.posts = []

    def get(self, url, params=None, timeout=None):
        self.gets.append((url, params))
        return StubResponse(self._route(url))

    def post(self, url, json=None, timeout=None):
        self.posts.append((url, json))
        return StubResponse(self._route(url))

    def _route(self, url):
        for suffix, payload in self.routes.items():
            if url.endswith(suffix):
                return payload
        raise AssertionError(f"unexpected URL {url}")


class StubResponse:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self._payload


def make_broker(routes, cfg=None):
    session = StubSession(routes)
    broker = Broker("key", "secret", cfg or OrderConfig(), session=session)
    return broker, session


def test_option_positions_drop_the_accounts_equity_holdings():
    broker, _ = make_broker({
        "/v2/positions": [
            {"symbol": "SPY", "qty": "1", "asset_class": "us_equity"},
            {"symbol": "SPY260905P00761000", "qty": "-2", "asset_class": "us_option"},
        ]
    })
    assert broker.open_option_positions() == [
        OptionPosition(symbol="SPY260905P00761000", qty=-2.0)
    ]


def test_no_positions_is_an_empty_list():
    broker, _ = make_broker({"/v2/positions": []})
    assert broker.open_option_positions() == []


def test_open_orders_asks_for_nested_legs():
    # Without nested=true an mleg order's legs are not returned at all.
    broker, session = make_broker({"/v2/orders": []})
    broker.open_orders()
    _, params = session.gets[0]
    assert params == {"status": "open", "nested": "true"}


def test_open_orders_reads_leg_symbols_not_the_empty_parent_symbol():
    broker, _ = make_broker({
        "/v2/orders": [{
            "id": "abc", "symbol": "", "order_class": "mleg",
            "legs": [
                {"symbol": "SPY260905P00761000"},
                {"symbol": "SPY260905P00756000"},
            ],
        }]
    })
    assert broker.open_orders() == [
        ["SPY260905P00761000", "SPY260905P00756000"]
    ]


def test_a_single_leg_order_falls_back_to_its_own_symbol():
    broker, _ = make_broker({
        "/v2/orders": [{"id": "abc", "symbol": "SPY260905P00761000", "legs": None}]
    })
    assert broker.open_orders() == [["SPY260905P00761000"]]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/test_broker.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'broker'`

- [ ] **Step 3: Write minimal implementation**

Create `broker.py`:

```python
"""The only module in this repository that can place an order.

Everything that reaches an order endpoint lives here, so "can this program
trade when I did not mean it to?" is a question you answer by reading one
short file. `data.py` stays what its docstring says: market data in,
nothing out.

Mirrors AlpacaClient's discipline -- an injectable session, raise_for_status
on every call, transport errors raise -- and adds one poll loop. It never
cancels and never replaces: this layer opens positions only.
"""

import os
import time
from datetime import datetime

import requests

from config import OrderConfig
from orders import OptionPosition, OrderRecord, OrderState, classify

# Transport mechanics, not strategy tunables, matching data.py's convention.
_HTTP_TIMEOUT_SECONDS = 10
_US_OPTION = "us_option"


class Broker:
    TRADING_URL = "https://paper-api.alpaca.markets"

    def __init__(
        self,
        key_id: str,
        secret_key: str,
        cfg: OrderConfig,
        session: object | None = None,
    ):
        self._cfg = cfg
        self._session = session if session is not None else requests.Session()
        self._session.headers.update(
            {"APCA-API-KEY-ID": key_id, "APCA-API-SECRET-KEY": secret_key}
        )

    @classmethod
    def from_env(cls, cfg: OrderConfig) -> "Broker":
        try:
            key_id = os.environ["ALPACA_API_KEY_ID"]
            secret_key = os.environ["ALPACA_API_SECRET_KEY"]
        except KeyError as exc:
            raise RuntimeError(
                "Set ALPACA_API_KEY_ID and ALPACA_API_SECRET_KEY in the environment."
            ) from exc
        return cls(key_id, secret_key, cfg)

    def _get(self, path: str, params: dict | None = None):
        response = self._session.get(
            f"{self.TRADING_URL}{path}", params=params, timeout=_HTTP_TIMEOUT_SECONDS
        )
        response.raise_for_status()
        return response.json()

    def open_option_positions(self) -> list[OptionPosition]:
        """Open option positions only.

        The paper account may hold unrelated equity, as it did during the
        smoke test, so the asset class is filtered rather than assumed.
        """
        payload = self._get("/v2/positions")
        return [
            OptionPosition(symbol=p["symbol"], qty=float(p["qty"]))
            for p in (payload or [])
            if p.get("asset_class") == _US_OPTION
        ]

    def open_orders(self) -> list[list[str]]:
        """Leg symbols for every open order, one list per order.

        `nested=true` is required: an mleg parent order's own `symbol` is the
        empty string and its legs are not returned without the flag, so a
        guard reading the parent symbol would silently never match.
        """
        payload = self._get("/v2/orders", {"status": "open", "nested": "true"})
        orders = []
        for order in payload or []:
            legs = order.get("legs") or []
            symbols = [leg["symbol"] for leg in legs]
            if not symbols and order.get("symbol"):
                symbols = [order["symbol"]]
            orders.append(symbols)
        return orders
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/test_broker.py -v`
Expected: PASS, 5 tests

- [ ] **Step 5: Commit**

```bash
git add broker.py tests/test_broker.py
git commit -F - <<'MSG'
Read account state through a broker separate from the data client

An mleg parent order carries an empty symbol and needs nested=true before
its legs are returned, so the guard reads leg symbols.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_014wa3goPT2PzCJSjQ7opWn3
MSG
```

---

### Task 6: `broker.py` — submit, fetch and await the fill

**Files:**
- Modify: `broker.py`
- Test: `tests/test_broker.py`

**Interfaces:**
- Consumes: `orders.OrderRecord`, `orders.OrderState`, `orders.classify`.
- Produces: `Broker.submit(payload: dict) -> OrderRecord`, `Broker.get_order(order_id: str) -> OrderRecord`, `Broker.await_fill(order_id: str) -> OrderRecord`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_broker.py`. `SequencedSession` exists because `await_fill` must see a *changing* status across polls, which the single-payload `StubSession` cannot express:

```python
import dataclasses

from orders import OrderState

FILLED_ORDER = {
    "id": "b889185b", "status": "filled", "filled_qty": "2",
    "filled_avg_price": "-0.65",
    "submitted_at": "2026-09-04T14:06:14.723228947Z",
}
WORKING_ORDER = {
    "id": "b889185b", "status": "pending_new", "filled_qty": "0",
    "filled_avg_price": None, "submitted_at": "2026-09-04T14:06:14.723228947Z",
}


class SequencedSession(StubSession):
    """Returns each queued payload once, then repeats the last one."""

    def __init__(self, routes, sequence):
        super().__init__(routes)
        self.sequence = list(sequence)

    def _route(self, url):
        if url.rstrip("/").endswith("/v2/orders/b889185b"):
            return self.sequence.pop(0) if len(self.sequence) > 1 else self.sequence[0]
        return super()._route(url)


def a_payload():
    return {
        "order_class": "mleg", "qty": "2", "type": "limit",
        "limit_price": "-0.65", "time_in_force": "day",
        "legs": [
            {"symbol": "SPY260905P00761000", "ratio_qty": "1",
             "side": "sell", "position_intent": "sell_to_open"},
            {"symbol": "SPY260905P00756000", "ratio_qty": "1",
             "side": "buy", "position_intent": "buy_to_open"},
        ],
    }


def test_submit_posts_the_payload_unmodified():
    broker, session = make_broker({"/v2/orders": WORKING_ORDER})
    payload = a_payload()
    broker.submit(payload)
    url, sent = session.posts[0]
    assert url.endswith("/v2/orders")
    assert sent == payload


def test_submit_returns_the_parsed_record():
    broker, _ = make_broker({"/v2/orders": FILLED_ORDER})
    record = broker.submit(a_payload())
    assert record.id == "b889185b"
    assert record.status == "filled"
    assert record.state is OrderState.FILLED
    assert record.filled_qty == 2.0
    assert record.filled_avg_price == -0.65
    assert record.submitted_at.year == 2026


def test_an_absent_fill_price_stays_none():
    broker, _ = make_broker({"/v2/orders": WORKING_ORDER})
    record = broker.submit(a_payload())
    assert record.filled_avg_price is None
    assert record.filled_qty == 0.0


def test_await_fill_stops_once_the_order_is_terminal():
    session = SequencedSession({}, [WORKING_ORDER, FILLED_ORDER])
    cfg = dataclasses.replace(
        OrderConfig(), fill_poll_seconds=0.0, fill_timeout_seconds=5.0
    )
    broker = Broker("key", "secret", cfg, session=session)
    record = broker.await_fill("b889185b")
    assert record.state is OrderState.FILLED
    assert len(session.gets) == 2


def test_await_fill_returns_its_last_observation_on_timeout():
    # A timeout is a recorded outcome, not an exception: the caller
    # journals whatever was last seen and exits non-zero.
    session = SequencedSession({}, [WORKING_ORDER])
    cfg = dataclasses.replace(
        OrderConfig(), fill_poll_seconds=0.0, fill_timeout_seconds=0.0
    )
    broker = Broker("key", "secret", cfg, session=session)
    record = broker.await_fill("b889185b")
    assert record.state is OrderState.WORKING
    assert record.status == "pending_new"


def test_await_fill_never_cancels_or_replaces():
    session = SequencedSession({}, [FILLED_ORDER])
    cfg = dataclasses.replace(OrderConfig(), fill_poll_seconds=0.0)
    broker = Broker("key", "secret", cfg, session=session)
    broker.await_fill("b889185b")
    assert session.posts == []
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/test_broker.py -v`
Expected: FAIL with `AttributeError: 'Broker' object has no attribute 'submit'`

- [ ] **Step 3: Write minimal implementation**

Append to `broker.py`, inside the `Broker` class:

```python
    def _post(self, path: str, payload: dict) -> dict:
        response = self._session.post(
            f"{self.TRADING_URL}{path}", json=payload, timeout=_HTTP_TIMEOUT_SECONDS
        )
        response.raise_for_status()
        return response.json()

    def submit(self, payload: dict) -> OrderRecord:
        """Place the order. The one call in this repository that trades."""
        return _record(self._post("/v2/orders", payload))

    def get_order(self, order_id: str) -> OrderRecord:
        return _record(self._get(f"/v2/orders/{order_id}"))

    def await_fill(self, order_id: str) -> OrderRecord:
        """Poll until the order is no longer working, or until the timeout.

        Read-only: it never cancels and never replaces, which is what keeps
        this layer to opening positions. A timeout is a recorded outcome,
        not an exception -- the caller journals whatever was last seen.
        """
        deadline = time.monotonic() + self._cfg.fill_timeout_seconds
        record = self.get_order(order_id)
        while record.state is OrderState.WORKING and time.monotonic() < deadline:
            time.sleep(self._cfg.fill_poll_seconds)
            record = self.get_order(order_id)
        return record
```

and at module level, below the class:

```python
def _record(payload: dict) -> OrderRecord:
    """Alpaca's order JSON as the three things this program acts on.

    The status string and fill price are preserved verbatim rather than
    normalised; the sign Alpaca reports on a credit fill is not something
    this layer depends on.
    """
    status = payload["status"]
    price = payload.get("filled_avg_price")
    submitted = payload.get("submitted_at")
    return OrderRecord(
        id=payload["id"],
        status=status,
        state=classify(status),
        filled_qty=float(payload.get("filled_qty") or 0),
        filled_avg_price=None if price is None else float(price),
        submitted_at=datetime.fromisoformat(submitted) if submitted else None,
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/test_broker.py -v`
Expected: PASS, 11 tests

- [ ] **Step 5: Commit**

```bash
git add broker.py tests/test_broker.py
git commit -F - <<'MSG'
Submit the spread and poll read-only until it is terminal

A timeout returns the last observation rather than raising, so the caller
can journal what was seen. The loop never cancels and never replaces.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_014wa3goPT2PzCJSjQ7opWn3
MSG
```

---

### Task 7: `journal.py` — the record

**Files:**
- Create: `journal.py`
- Test: `tests/test_journal.py` (create)

**Interfaces:**
- Consumes: `features.FeatureSet`, `strategy.Decision`, `orders.OrderRecord`.
- Produces: `journal.DRY_RUN = "dry_run"`, `journal.SUBMIT = "submit"`, and `journal.build_record(now, mode, underlying, expiry, features, decision, payload=None, record=None) -> dict`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_journal.py`:

```python
import json
from datetime import date, datetime, timedelta, timezone

from features import Feature, FeatureSet
from journal import DRY_RUN, SUBMIT, build_record
from orders import OrderRecord, OrderState
from strategy import Decision, SpreadLeg, SpreadProposal, Stance

NOW = datetime(2026, 9, 4, 13, 40, 2, tzinfo=timezone.utc)
EXPIRY = date(2026, 9, 5)

SHORT = SpreadLeg("SPY260905P00761000", 761.0, "P", "sell", -0.3020, 1.60, 1.68)
LONG = SpreadLeg("SPY260905P00756000", 756.0, "P", "buy", -0.1810, 0.88, 0.95)


def a_feature_set():
    return FeatureSet(
        spot=Feature.ok(765.12, NOW),
        rv20=Feature.ok(0.1483, NOW),
        spot_over_sma20=Feature.ok(1.0121, NOW),
        spot_over_sma50=Feature.ok(1.0388, NOW),
        atm_iv=Feature.missing("no implied volatility on the 765.0 call"),
        market_open=True,
        next_open=NOW + timedelta(days=1),
        next_close=NOW + timedelta(hours=4),
        expiry=EXPIRY,
        as_of=NOW,
    )


def a_proposal():
    return SpreadProposal(
        structure="put_credit", short_leg=SHORT, long_leg=LONG, credit=0.65,
        max_loss_per_contract=435.0, quantity=2, total_risk=870.0,
        risk_budget=1000.0,
    )


def a_trade():
    return Decision(Stance.BULLISH, a_proposal(), "sell 761.0P / buy 756.0P")


def a_stand_aside():
    return Decision(Stance.NEUTRAL, None, "neutral stance: no directional edge")


def make(decision, mode=SUBMIT, payload=None, record=None):
    return build_record(
        now=NOW, mode=mode, underlying="SPY", expiry=EXPIRY,
        features=a_feature_set(), decision=decision,
        payload=payload, record=record,
    )


def test_the_record_is_json_native():
    # No custom encoder: build_record converts datetimes itself, so the
    # shape can be tested without touching a file.
    assert json.loads(json.dumps(make(a_trade()))) == make(a_trade())


def test_a_stand_aside_records_its_reason_and_no_order():
    record = make(a_stand_aside())
    assert record["decision"]["will_trade"] is False
    assert "neutral stance" in record["decision"]["reason"]
    assert record["order"] is None
    assert record["stance"] == "neutral"


def test_a_dry_run_records_no_order():
    assert make(a_trade(), mode=DRY_RUN)["order"] is None


def test_every_feature_is_recorded_with_its_status():
    features = make(a_trade())["features"]
    assert set(features) == {
        "spot", "rv20", "spot_over_sma20", "spot_over_sma50", "atm_iv"
    }
    assert features["spot"]["value"] == 765.12
    assert features["spot"]["status"] == "ok"
    assert features["atm_iv"]["status"] == "missing"
    assert features["atm_iv"]["value"] is None
    assert "no implied volatility" in features["atm_iv"]["detail"]


def test_a_trade_records_the_sizing_and_both_legs():
    decision = make(a_trade())["decision"]
    assert decision["will_trade"] is True
    assert decision["structure"] == "put_credit"
    assert decision["credit"] == 0.65
    assert decision["quantity"] == 2
    assert decision["total_risk"] == 870.0
    assert [leg["symbol"] for leg in decision["legs"]] == [
        "SPY260905P00761000", "SPY260905P00756000"
    ]


def test_a_submitted_order_records_the_payload_and_the_outcome():
    payload = {"order_class": "mleg", "limit_price": "-0.65"}
    record = OrderRecord(
        id="b889185b", status="filled", state=OrderState.FILLED,
        filled_qty=2.0, filled_avg_price=-0.65, submitted_at=NOW,
    )
    order = make(a_trade(), payload=payload, record=record)["order"]
    assert order["payload"] == payload
    assert order["id"] == "b889185b"
    assert order["state"] == "filled"
    assert order["filled_qty"] == 2.0


def test_the_timestamp_and_expiry_are_iso_strings():
    record = make(a_trade())
    assert record["timestamp"] == NOW.isoformat()
    assert record["expiry"] == "2026-09-05"
    assert record["mode"] == "submit"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/test_journal.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'journal'`

- [ ] **Step 3: Write minimal implementation**

Create `journal.py`:

```python
"""One line per run, appended.

`build_record` is pure and returns only JSON-native values -- datetimes are
converted here rather than by a custom json encoder -- so the shape is
testable without touching a file. Every run writes a line, stand-asides
included: strategy.py was built so a quiet day is as explainable as a busy
one, and a log of trades only would discard half of that.
"""

from datetime import date, datetime

from features import Feature, FeatureSet
from orders import OrderRecord
from strategy import Decision

DRY_RUN = "dry_run"
SUBMIT = "submit"

FEATURE_NAMES = ("spot", "rv20", "spot_over_sma20", "spot_over_sma50", "atm_iv")


def _feature(feature: Feature) -> dict:
    return {
        "value": feature.value,
        "status": feature.status.value,
        "timestamp": None if feature.timestamp is None else feature.timestamp.isoformat(),
        "detail": feature.detail,
    }


def _decision(decision: Decision) -> dict:
    body = {"will_trade": decision.will_trade, "reason": decision.reason}
    proposal = decision.proposal
    if proposal is None:
        return body
    body.update(
        {
            "structure": proposal.structure,
            "credit": proposal.credit,
            "quantity": proposal.quantity,
            "max_loss_per_contract": proposal.max_loss_per_contract,
            "total_risk": proposal.total_risk,
            "risk_budget": proposal.risk_budget,
            "legs": [
                {
                    "symbol": leg.symbol,
                    "side": leg.side,
                    "strike": leg.strike,
                    "right": leg.right,
                }
                for leg in (proposal.short_leg, proposal.long_leg)
            ],
        }
    )
    return body


def _order(payload: dict | None, record: OrderRecord | None) -> dict | None:
    """The submitted order, or None when nothing was sent.

    A dry run and a stand-aside both record None: nothing reached an order
    endpoint, and there is no outcome to describe.
    """
    if payload is None or record is None:
        return None
    return {
        "payload": payload,
        "id": record.id,
        "status": record.status,
        "state": record.state.value,
        "filled_qty": record.filled_qty,
        "filled_avg_price": record.filled_avg_price,
    }


def build_record(
    now: datetime,
    mode: str,
    underlying: str,
    expiry: date,
    features: FeatureSet,
    decision: Decision,
    payload: dict | None = None,
    record: OrderRecord | None = None,
) -> dict:
    """One run, as JSON-native values."""
    return {
        "timestamp": now.isoformat(),
        "mode": mode,
        "underlying": underlying,
        "expiry": expiry.isoformat(),
        "stance": decision.stance.value,
        "features": {name: _feature(getattr(features, name)) for name in FEATURE_NAMES},
        "decision": _decision(decision),
        "order": _order(payload, record),
    }
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/test_journal.py -v`
Expected: PASS, 7 tests

- [ ] **Step 5: Commit**

```bash
git add journal.py tests/test_journal.py
git commit -F - <<'MSG'
Build one JSON-native decision record per run

Stand-asides are recorded too: a log of trades only would discard the half
of the reasoning that explains a quiet day.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_014wa3goPT2PzCJSjQ7opWn3
MSG
```

---

### Task 8: `journal.py` — the append that never raises

**Files:**
- Modify: `journal.py`
- Test: `tests/test_journal.py`

**Interfaces:**
- Consumes: nothing new.
- Produces: `journal.append(record: dict, path: str) -> None`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_journal.py`:

```python
from journal import append


def test_append_writes_one_line_per_run(tmp_path):
    path = str(tmp_path / "decisions.jsonl")
    append(make(a_trade()), path)
    append(make(a_stand_aside()), path)
    lines = (tmp_path / "decisions.jsonl").read_text().splitlines()
    assert len(lines) == 2
    assert json.loads(lines[0])["decision"]["will_trade"] is True
    assert json.loads(lines[1])["decision"]["will_trade"] is False


def test_a_write_failure_warns_and_preserves_the_record(tmp_path, capsys):
    # The one place an I/O error must not raise: by the time this runs an
    # order may already be live, and dying on a full disk would leave a
    # position with no record anywhere. stderr keeps the record.
    unwritable = str(tmp_path / "no-such-directory" / "decisions.jsonl")
    append(make(a_trade()), unwritable)
    err = capsys.readouterr().err
    assert "WARNING" in err
    assert "SPY260905P00761000" in err
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/test_journal.py -v`
Expected: FAIL with `ImportError: cannot import name 'append' from 'journal'`

- [ ] **Step 3: Write minimal implementation**

Add `import json` and `import sys` to `journal.py`'s imports, then append:

```python
def append(record: dict, path: str) -> None:
    """Append one line. Never raises.

    The single deliberate exception to this codebase's raise-on-failure
    discipline. By the time this is called an order may already be live, so
    dying on a full disk would leave a position with no record anywhere.
    The warning carries the complete record, which makes stderr the fallback
    journal.
    """
    try:
        with open(path, "a") as handle:
            handle.write(json.dumps(record) + "\n")
    except OSError as exc:
        print(f"WARNING: could not write the journal to {path}: {exc}", file=sys.stderr)
        print(json.dumps(record), file=sys.stderr)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/test_journal.py -v`
Expected: PASS, 9 tests

- [ ] **Step 5: Commit**

```bash
git add journal.py tests/test_journal.py
git commit -F - <<'MSG'
Append the decision record without ever raising

By the time the journal is written an order may already be live. A full
disk must not leave a position with no record, so the failure path warns
and prints the record to stderr.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_014wa3goPT2PzCJSjQ7opWn3
MSG
```

---

### Task 9: The `--dry-run | --submit` gate and the narrowed scope guard

**Files:**
- Modify: `main.py` (the `--dry-run` argument, the module docstring)
- Modify: `tests/test_main.py:52-60` (replace `test_no_module_contains_order_placing_code`)
- Test: `tests/test_main.py`

**Interfaces:**
- Consumes: nothing new.
- Produces: `main.main(["--dry-run", "--stance", ...])` unchanged; `main.main(["--submit", "--stance", ...])` accepted; neither flag and both flags are errors.

This task changes only the gate. The `--submit` branch is Task 10; until then `--submit` follows the same path `--dry-run` does.

- [ ] **Step 1: Write the failing tests**

In `tests/test_main.py`, **replace** `test_no_module_contains_order_placing_code` (lines 52–60) with:

```python
def test_only_broker_reaches_an_order_endpoint():
    # This replaces the old blanket wall. Now that the order layer exists
    # the property worth guarding is narrower: exactly one module can place
    # an order, and the mleg vocabulary lives in the pure module that builds
    # the payload.
    root = pathlib.Path(__file__).parent.parent
    modules = (
        "config.py", "data.py", "features.py", "strategy.py",
        "orders.py", "broker.py", "journal.py", "main.py",
    )
    sources = {name: (root / name).read_text() for name in modules}

    assert "/v2/orders" in sources["broker.py"]
    for name, source in sources.items():
        if name != "broker.py":
            assert "/v2/orders" not in source, f"{name} reaches an order endpoint"
        if name != "orders.py":
            for forbidden in ("order_class", "mleg", "position_intent"):
                assert forbidden not in source, f"{name} mentions {forbidden}"
```

and append:

```python
def test_exactly_one_mode_flag_is_required(monkeypatch):
    with pytest.raises(SystemExit):
        main_module.main(["--stance", "bullish"])


def test_the_two_mode_flags_are_mutually_exclusive(monkeypatch):
    with pytest.raises(SystemExit):
        main_module.main(["--dry-run", "--submit", "--stance", "bullish"])
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/test_main.py -v`
Expected: FAIL, exactly two failures — `test_exactly_one_mode_flag_is_required` and `test_the_two_mode_flags_are_mutually_exclusive`, both with `SystemExit` not raised as expected, because `--submit` is not yet a recognised argument and `--dry-run` is still individually `required=True`.

`test_only_broker_reaches_an_order_endpoint` should **pass** immediately: `orders.py`, `broker.py` and `journal.py` all exist by this point, and none of them violates the rule. If it fails, a forbidden token leaked into the wrong module in Tasks 2–8 — fix that module rather than the test.

- [ ] **Step 3: Write minimal implementation**

In `main.py`, replace the `--dry-run` argument with a mutually exclusive group:

```python
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument(
        "--dry-run",
        action="store_true",
        help="fetch, compute, print the decision and the order it would place, exit",
    )
    mode.add_argument(
        "--submit",
        action="store_true",
        help="do all of --dry-run, then actually place the order",
    )
```

and replace the module docstring's second paragraph with:

```python
"""Entrypoint for the daily SPY spread agent.

Fetches market data, computes the features, decides, and either prints the
order it would place (--dry-run) or places it (--submit). Exactly one of
those flags is required: there is no default, and no bare invocation that
trades.

Only `broker.py` can reach an order endpoint, and the --dry-run path returns
before a Broker is constructed.
"""
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/pytest -v`
Expected: PASS, whole suite

- [ ] **Step 5: Commit**

```bash
git add main.py tests/test_main.py
git commit -F - <<'MSG'
Require exactly one of --dry-run and --submit

Replaces the blanket "no module places orders" guard with the narrower
property the order layer can actually keep: only broker.py reaches an order
endpoint, and only orders.py speaks mleg.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_014wa3goPT2PzCJSjQ7opWn3
MSG
```

---

### Task 10: The submit flow

**Files:**
- Modify: `main.py` (imports, a `format_order` helper, the tail of `main`)
- Test: `tests/test_main.py`

**Interfaces:**
- Consumes: `orders.build_order`, `orders.existing_exposure`, `orders.OrderState`, `broker.Broker`, `journal.build_record`, `journal.append`, `config.OrderConfig`.
- Produces: `main.main(...)` returning `0` on a stand-aside, a dry run, a guard hit or a fill, and `1` on a submitted order that did not fill.

- [ ] **Step 1: Write the failing tests**

In `tests/test_main.py`, extend `run_main_with` to take a mode and an expected exit code, replacing the existing definition:

```python
def run_main_with(
    monkeypatch, quote, trade, stance="neutral", bars=None, chain=None,
    mode="--dry-run", expected=0, journal_path=None,
):
    client = StubClient(quote, trade, bars=bars, chain=chain)
    monkeypatch.setattr(
        main_module.AlpacaClient, "from_env", classmethod(lambda cls, cfg: client)
    )
    if journal_path is not None:
        monkeypatch.setattr(
            main_module, "OrderConfig",
            lambda: dataclasses.replace(
                config_module.OrderConfig(), journal_path=journal_path,
                fill_poll_seconds=0.0, fill_timeout_seconds=0.0,
            ),
        )
    assert main_module.main([mode, "--stance", stance]) == expected
    return client
```

Add `import dataclasses` and `import config as config_module` to the test imports.

Then append the new tests:

```python
class StubBroker:
    """Records what main() asks of the broker. Places nothing."""

    def __init__(self, positions=None, working=None, outcome=None):
        self.positions = positions or []
        self.working = working or []
        self.outcome = outcome
        self.submitted = []

    def open_option_positions(self):
        return self.positions

    def open_orders(self):
        return self.working

    def submit(self, payload):
        self.submitted.append(payload)
        return self.outcome

    def await_fill(self, order_id):
        return self.outcome


def use_broker(monkeypatch, broker):
    monkeypatch.setattr(
        main_module.Broker, "from_env", classmethod(lambda cls, cfg: broker)
    )
    return broker


def an_outcome(status="filled", state=None):
    from orders import OrderRecord, OrderState
    return OrderRecord(
        id="b889185b", status=status,
        state=state or (OrderState.FILLED if status == "filled" else OrderState.WORKING),
        filled_qty=2.0 if status == "filled" else 0.0,
        filled_avg_price=-0.65 if status == "filled" else None,
        submitted_at=NOW,
    )


def a_bullish_run(monkeypatch, **kwargs):
    return run_main_with(
        monkeypatch,
        quote=StockQuote(bid=765.0, ask=765.5, bid_size=1, ask_size=1, timestamp=NOW),
        trade=None, stance="bullish", bars=daily_bars(60, date(2026, 9, 3)),
        chain=a_full_chain(), **kwargs,
    )


def test_the_dry_run_never_constructs_a_broker(monkeypatch, tmp_path):
    # The executable form of the property the module split exists for.
    def explode(cls, cfg):
        raise AssertionError("--dry-run must not construct a Broker")

    monkeypatch.setattr(main_module.Broker, "from_env", classmethod(explode))
    a_bullish_run(monkeypatch, journal_path=str(tmp_path / "d.jsonl"))


def test_the_dry_run_prints_the_order_it_would_place(monkeypatch, tmp_path, capsys):
    a_bullish_run(monkeypatch, journal_path=str(tmp_path / "d.jsonl"))
    out = capsys.readouterr().out
    assert '"order_class": "mleg"' in out
    assert '"limit_price": "-0.65"' in out


def test_submitting_sends_the_payload_and_exits_zero_on_a_fill(monkeypatch, tmp_path):
    broker = use_broker(monkeypatch, StubBroker(outcome=an_outcome()))
    a_bullish_run(
        monkeypatch, mode="--submit", expected=0,
        journal_path=str(tmp_path / "d.jsonl"),
    )
    assert len(broker.submitted) == 1
    assert broker.submitted[0]["limit_price"] == "-0.65"


def test_an_unfilled_order_exits_one(monkeypatch, tmp_path):
    use_broker(monkeypatch, StubBroker(outcome=an_outcome(status="pending_new")))
    a_bullish_run(
        monkeypatch, mode="--submit", expected=1,
        journal_path=str(tmp_path / "d.jsonl"),
    )


def test_an_expiry_already_covered_submits_nothing(monkeypatch, tmp_path, capsys):
    from orders import OptionPosition
    broker = use_broker(monkeypatch, StubBroker(
        positions=[OptionPosition("SPY260904P00760000", -2.0)],
        outcome=an_outcome(),
    ))
    a_bullish_run(
        monkeypatch, mode="--submit", expected=0,
        journal_path=str(tmp_path / "d.jsonl"),
    )
    assert broker.submitted == []
    assert "already holding" in capsys.readouterr().out


def test_every_run_appends_exactly_one_journal_line(monkeypatch, tmp_path):
    path = tmp_path / "d.jsonl"
    use_broker(monkeypatch, StubBroker(outcome=an_outcome()))
    a_bullish_run(monkeypatch, mode="--submit", journal_path=str(path))
    a_bullish_run(monkeypatch, journal_path=str(path))
    lines = path.read_text().splitlines()
    assert len(lines) == 2
    assert json.loads(lines[0])["mode"] == "submit"
    assert json.loads(lines[1])["mode"] == "dry_run"
    assert json.loads(lines[0])["order"]["state"] == "filled"


def test_a_stand_aside_journals_and_places_nothing(monkeypatch, tmp_path):
    path = tmp_path / "d.jsonl"
    broker = use_broker(monkeypatch, StubBroker(outcome=an_outcome()))
    run_main_with(
        monkeypatch,
        quote=StockQuote(bid=765.0, ask=765.5, bid_size=1, ask_size=1, timestamp=NOW),
        trade=None, stance="neutral", bars=daily_bars(60, date(2026, 9, 3)),
        chain=a_full_chain(), mode="--submit", journal_path=str(path),
    )
    assert broker.submitted == []
    assert json.loads(path.read_text())["decision"]["will_trade"] is False
```

Add `import json` to the test imports.

Note: the expiry `StubClient.resolve_expiry` returns is `date(2026, 9, 4)`, so the guard prefix in these tests is `SPY260904` — which is why the blocking fixture uses `SPY260904P00760000` while `a_full_chain()` quotes `SPY260905` symbols. Those chain symbols are never matched by the guard, which is what makes the *unblocked* tests pass.

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/test_main.py -v`
Expected: FAIL with `AttributeError: module 'main' has no attribute 'Broker'`

- [ ] **Step 3: Write minimal implementation**

In `main.py`, extend the imports:

```python
import json

from broker import Broker
from config import FeatureConfig, OrderConfig, StrategyConfig
from journal import DRY_RUN, SUBMIT, append, build_record
from orders import OrderState, build_order, existing_exposure
```

Add a formatter beside the existing ones:

```python
def format_order(payload: dict) -> str:
    """The order exactly as it would be sent."""
    return json.dumps(payload, indent=2)


def format_outcome(record) -> str:
    price = "-" if record.filled_avg_price is None else f"{record.filled_avg_price:.2f}"
    return (
        f"order {record.id}: {record.status} ({record.state.value})  "
        f"filled {record.filled_qty:g} at {price}"
    )
```

Replace the tail of `main` — everything from `decision = build_decision(...)` to `return 0` — with:

```python
    decision = build_decision(features, chain, args.stance, account.equity, strategy_cfg)
    print(format_decision(decision))

    order_cfg = OrderConfig()
    mode = DRY_RUN if args.dry_run else SUBMIT
    context = dict(
        now=now, mode=mode, underlying=cfg.underlying, expiry=expiry,
        features=features, decision=decision,
    )

    if decision.proposal is None:
        append(build_record(**context), order_cfg.journal_path)
        return 0

    payload = build_order(decision.proposal, order_cfg)
    print()
    print(format_order(payload))

    if args.dry_run:
        append(build_record(**context), order_cfg.journal_path)
        return 0

    # Everything below is the only path in this program that trades.
    broker = Broker.from_env(order_cfg)
    blocked = existing_exposure(
        broker.open_option_positions(), broker.open_orders(), cfg.underlying, expiry
    )
    if blocked:
        print(f"\nnot submitting: {blocked}")
        append(build_record(**context), order_cfg.journal_path)
        return 0

    outcome = broker.await_fill(broker.submit(payload).id)
    print(f"\n{format_outcome(outcome)}")
    append(
        build_record(payload=payload, record=outcome, **context),
        order_cfg.journal_path,
    )
    return 0 if outcome.state is OrderState.FILLED else 1
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/pytest -v`
Expected: PASS, whole suite

- [ ] **Step 5: Commit**

```bash
git add main.py tests/test_main.py
git commit -F - <<'MSG'
Place the spread and journal every run

--dry-run returns before a Broker is constructed, which is asserted rather
than assumed. A submitted order that does not fill exits 1, so a scheduler
can tell "no trade by design" from "tried and did not get on".

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_014wa3goPT2PzCJSjQ7opWn3
MSG
```

---

### Task 11: Documentation and the journal's git status

**Files:**
- Modify: `CLAUDE.md`
- Modify: `.gitignore`
- Test: none — documentation. Verified by running the suite and a real dry run.

**Interfaces:**
- Consumes: nothing.
- Produces: nothing consumed by later tasks.

- [ ] **Step 1: Ignore the journal**

`decisions.jsonl` is account activity, not source. Append to `.gitignore`:

```
decisions.jsonl
```

- [ ] **Step 2: Update CLAUDE.md**

In the "Current state" section, replace the `main.py` bullet and add three, so the list reads:

```markdown
- `orders.py` — pure: the multi-leg payload, order-status classification and
  the duplicate-position guard. No I/O.
- `broker.py` — the only module that can place an order. Everything reaching
  an order endpoint lives here; `data.py` stays read-only.
- `journal.py` — one JSON line per run, appended, stand-asides included.
- `main.py --dry-run|--submit --stance <bullish|bearish|neutral>` — fetches,
  computes, decides, and either prints the order it would place or places it.
  Exactly one mode flag is required.
```

Then replace the "Order construction and submission are deliberately not implemented yet." sentence in **Domain context** with:

```markdown
Order construction and submission are implemented and documented in
`docs/superpowers/specs/2026-09-04-order-layer-design.md`. The layer opens
positions only: nothing closes a spread, so a position is left to expire or
be closed by hand.

Two invariants are asserted by `tests/test_main.py` rather than merely
intended: `/v2/orders` appears only in `broker.py`, and the `mleg`
vocabulary only in `orders.py`. A credit spread is priced with a **negative**
`limit_price` — Alpaca reads a positive multi-leg limit as a debit, and a
positive price on a credit spread fills rather than being rejected.
```

- [ ] **Step 3: Run the whole suite**

Run: `.venv/bin/pytest`
Expected: PASS, no failures

- [ ] **Step 4: Run a real dry run against the paper account**

Run: `source .venv/bin/activate && python main.py --dry-run --stance bullish`
Expected: the feature block, the decision, and a JSON payload whose `limit_price` starts with `-`. Confirm `decisions.jsonl` gained exactly one line, and that `git status` does not list it.

- [ ] **Step 5: Commit**

```bash
git add CLAUDE.md .gitignore
git commit -F - <<'MSG'
Describe the order layer in CLAUDE.md

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_014wa3goPT2PzCJSjQ7opWn3
MSG
```

---

## After the plan

The first live `--submit` settles the one thing the spec records as unverified: whether Alpaca reports `filled_avg_price` as negative on a credit fill. Check the journal line against the position blotter and, if the sign differs from the example in the spec, correct the spec rather than the code — nothing in the layer depends on it.

Not built here, and deliberately: closing logic (the smoke test records that closes are per-leg and must go short-leg-first or Alpaca rejects the long-leg close as uncovered with a 403), cancel/reprice for a resting order, aggregate exposure limits across overlapping expiries, and the LLM stance layer that will eventually fill the `--stance` argument.
