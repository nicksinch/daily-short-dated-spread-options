# Order Layer — Design

**Date:** 2026-09-04
**Status:** Approved, ready for implementation planning
**Predecessor:** `docs/superpowers/specs/2026-09-04-strategy-layer-design.md`
**Source spec:** `docs/start_point_spec.md`
**Field evidence:** `docs/alpaca-paper-spread-smoke-test.md`

## Scope

Turn a `SpreadProposal` into a live multi-leg order on the paper account, confirm what
happened to it, and write one line to a decision journal.

In the end-to-end pipeline this is the last two boxes:

```
features ✅ → stance (LLM, later) → structure ✅ → strikes ✅ → sizing ✅ → order → log
```

This layer **opens** positions only. Closing them, whether at a target or before
expiry, is not in scope and not stubbed. Also out of scope: the LLM stance layer,
repricing or cancelling a resting order, aggregate exposure limits across days,
backtesting.

The wall that `--dry-run` enforced — "no code path from here to an order endpoint" — is
deliberately taken down in this session. It is replaced by a narrower and more useful
property, asserted by a test: **exactly one module in this repository can place an
order**, and the dry-run path returns before that module is constructed.

## Decisions

Answered by the project owner during design; recorded because none follows from the
code.

| Decision | Choice |
| --- | --- |
| Layer scope | open only; no closing logic, no working-order management |
| Limit price | the conservative credit — `short.bid − long.ask`, the same number that sized the position |
| Submit gate | `--dry-run` and `--submit`, mutually exclusive, exactly one required |
| After submit | poll the order read-only until terminal or timeout, then record what was seen |
| Journal | one JSON object per line, appended, **every** run — stand-asides and dry runs included |
| Duplicate guard | broker state: refuse if a position or working order already exists on the target expiry |
| Module split | order-placing I/O isolated in a new `broker.py`; `data.py` stays read-only |
| Exit code | `1` when an order was submitted and did not fill; `0` otherwise |

Four of these deserve their reasoning recorded.

**The limit price is the conservative credit, not the mid.** The predecessor design
anticipated the opposite — "the mid remains the right basis for the eventual limit
price" — and this design departs from it deliberately. Nothing in this layer handles an
order that fails to fill: with no cancel and no reprice, a resting mid order that never
trades is a silent no-trade day, indistinguishable from a stand-aside except in the
journal. `short.bid − long.ask` is marketable — it sells at the bid and buys at the ask
— so it fills essentially immediately. It also costs nothing in code, because
`build_decision` has already computed and validated exactly this number, and it makes
the realised credit provably no worse than the credit `max_loss_per_contract` was
computed from. The few cents given up against the mid are the price of certainty, on a
paper account, for a system with no unfilled-order handling.

**A credit is a negative `limit_price`.** From Alpaca's `CreateOrderRequest` schema:

> In case of `mleg`, the limit_price parameter is expressed with the following notation:
> a positive value indicates a debit, representing a cost or payment to be made; a
> negative value signifies a credit, reflecting an amount to be received.

The smoke test placed a *debit* spread, so its positive `"0.47"` was correct and is not
a precedent for this layer. Copying that sign onto a credit spread would not be
rejected — it would be accepted and filled, because receiving 0.65 satisfies "pay at
most 0.65". The same limit would equally permit *paying* 0.64, inverting the trade's
economics while the order response still reads as a normal fill. This is the one detail
in the layer that fails silently and expensively, so it is asserted by a dedicated test.

**The duplicate guard matches an expiry prefix.** Matching the two exact OCC symbols
would leak: re-run after spot moves a dollar, the short strike differs, the guard
passes and a second spread goes on. Matching *any* open option position would
overcorrect: yesterday's 1DTE spread is still open this morning and would stand the
agent aside every day after the first. Scoping to the target expiry —
`symbol.startswith(f"{underlying}{expiry:%y%m%d}")` — blocks a second run today and
permits today's trade while yesterday's position runs off.

**The journal records stand-asides.** `strategy.py` was built so that "a quiet day is
as explainable as a busy one"; a log that only recorded trades would discard exactly
the half of that the reasoning was built for.

## Module layout

```
config.py            FeatureConfig, StrategyConfig, OrderConfig     (OrderConfig is new)
data.py              unchanged — market data in, nothing out
features.py          unchanged
strategy.py          unchanged
orders.py            payload construction, status classification, the guard predicate  (new)
broker.py            the only module that can place an order                            (new)
journal.py           record construction and one append                                 (new)
main.py              --dry-run | --submit, orchestration            (gate and flow are new)
tests/test_orders.py, tests/test_broker.py, tests/test_journal.py                        (new)
```

`orders.py` and `journal.py`'s `build_record` perform no I/O, so they follow
`features.py` and `strategy.py`: testable against hand-written fixtures with no network.
`broker.py` imports `orders.py` — I/O depending on pure, the direction already used
throughout — and nothing imports `broker.py` except `main.py`.

`strategy.py` and `data.py` are untouched. The proposal already carries OCC symbols,
sides, quantity and credit, which is everything the payload needs.

### Why `broker.py` rather than growing `data.py`

`data.py` already reaches `TRADING_URL` for the clock, the account and the contract
list, so the order calls would fit there on the merits of HTTP alone. They are kept out
for one reason: it makes "can this program trade when I did not mean it to?" a question
you answer by reading a single short file, and it keeps `data.py`'s docstring true. The
cost is roughly ten duplicated lines of session and auth setup, since `Broker` builds
its own `requests.Session` rather than reaching into `AlpacaClient`'s private `_get`.

## Configuration

A third frozen dataclass in `config.py`, for the same reason `StrategyConfig` is
separate from `FeatureConfig`: this is about placing orders, not about computing
features or choosing strikes.

```python
@dataclass(frozen=True)
class OrderConfig:
    time_in_force: str = "day"          # options accept only day or gtc
    fill_poll_seconds: float = 1.0      # between order status re-fetches
    fill_timeout_seconds: float = 15.0  # then stop polling and record what was seen
    journal_path: str = "decisions.jsonl"
```

No change to `FeatureConfig` or `StrategyConfig`.

## Result types

```python
class OrderState(str, Enum):
    """What the program does about a status, not what the status is called."""
    FILLED = "filled"      # done, we are on
    WORKING = "working"    # not terminal; keep polling
    DEAD = "dead"          # terminal without a fill

@dataclass(frozen=True)
class OrderRecord:
    id: str
    status: str                      # Alpaca's own status string, preserved verbatim
    state: OrderState
    filled_qty: float
    filled_avg_price: float | None
    submitted_at: datetime | None

@dataclass(frozen=True)
class OptionPosition:
    symbol: str
    qty: float                       # signed: negative is short
```

Alpaca's sixteen order statuses collapse to three states:

| State | Statuses |
| --- | --- |
| `FILLED` | `filled` |
| `DEAD` | `canceled`, `expired`, `rejected`, `suspended`, `done_for_day`, `replaced` |
| `WORKING` | everything else, including `new`, `accepted`, `pending_new`, `partially_filled`, `held` |

`partially_filled` is `WORKING` rather than a fourth state: a two-spread order can fill
one spread, and the poll should keep waiting for the rest. If the timeout arrives first,
`filled_qty` in the journal records what got on.

## `orders.py`

Pure. Imports `SpreadProposal` from `strategy.py` and `OrderConfig` from `config.py` as
record types; imports nothing from `data.py`, `broker.py` or `requests`.

```python
def build_order(proposal: SpreadProposal, cfg: OrderConfig) -> dict
def classify(status: str) -> OrderState
def existing_exposure(
    positions: Sequence[OptionPosition],
    working_orders: Sequence[Sequence[str]],   # leg symbols per open order
    underlying: str,
    expiry: date,
) -> str | None                                # a reason, or None to proceed
```

`build_order` takes the whole proposal rather than loose arguments, so there is no way
to construct a payload for a spread that never passed `build_decision`'s gates. Both
legs get `ratio_qty: "1"` — a 1:1 vertical, and the greatest common divisor across legs
must be 1, which Alpaca enforces. The short leg is `sell` / `sell_to_open`, the long leg
`buy` / `buy_to_open`. `qty` is `proposal.quantity`: for an `mleg` order Alpaca defines
`qty` as "the number of units to trade of this strategy", i.e. spreads, not contracts.

`existing_exposure` returns the reason string that will be printed and journalled, so a
blocked run explains itself the same way a stand-aside does.

## `broker.py`

The only module that can place an order. Mirrors `AlpacaClient`'s shape — `from_env`,
an injectable `session`, `raise_for_status` on every call — and holds four endpoints
plus one loop.

```python
class Broker:
    TRADING_URL = "https://paper-api.alpaca.markets"

    def open_option_positions(self) -> list[OptionPosition]   # GET /v2/positions
    def open_orders(self) -> list[list[str]]                  # GET /v2/orders
    def submit(self, payload: dict) -> OrderRecord            # POST /v2/orders
    def get_order(self, order_id: str) -> OrderRecord         # GET /v2/orders/{id}
    def await_fill(self, order_id: str) -> OrderRecord        # poll until terminal
```

`open_option_positions` filters the response to `asset_class == "us_option"`; the paper
account may hold unrelated equity positions, as it did during the smoke test.

`open_orders` requests `status=open` **and `nested=true`**, and returns leg symbols. An
`mleg` parent order's top-level `symbol` is the empty string — Alpaca's own response
example shows `"symbol": ""` with the contracts only on `legs[]` — so a guard reading
the parent symbol would silently never match, and without `nested=true` the legs are not
returned at all.

`await_fill` re-fetches every `fill_poll_seconds` until `classify` reports something
other than `WORKING`, or until `fill_timeout_seconds` elapses. It returns its last
observation either way: **a timeout is a recorded outcome, not an exception.** It never
cancels and never replaces, which is what keeps it inside "open only".

Transport and HTTP errors raise, as everywhere else in this codebase.

## `journal.py`

```python
def build_record(...) -> dict     # pure; JSON-native types only
def append(record: dict, path: str) -> None
```

`build_record` converts datetimes to ISO strings itself rather than deferring to a
custom `json.dumps` encoder. That keeps it testable without touching a file, and lets a
test assert the record survives a `dumps`/`loads` round trip unchanged.

One line per run:

```json
{"timestamp": "2026-09-04T13:40:02-04:00", "mode": "submit", "underlying": "SPY",
 "expiry": "2026-09-05", "stance": "bullish",
 "features": {"spot": {"value": 765.12, "status": "ok", "timestamp": "..."}},
 "decision": {"will_trade": true, "reason": "...", "structure": "put_credit",
              "credit": 0.65, "quantity": 2, "max_loss_per_contract": 435.0,
              "total_risk": 870.0,
              "legs": [{"symbol": "SPY260905P00761000", "side": "sell", "strike": 761.0}]},
 "order": {"payload": {}, "id": "...", "status": "filled", "state": "filled",
           "filled_qty": 2, "filled_avg_price": -0.65}}
```

`"order"` is `null` on stand-aside days and on dry runs; `"decision"."will_trade"` is
`false` with the reason on stand-aside days. `"payload"` holds the order exactly as
submitted, so the journal alone is enough to reconstruct what was sent.

`"status"` and `"filled_avg_price"` are preserved verbatim from Alpaca rather than
normalised. The sign of `filled_avg_price` on a *credit* fill is unverified — the only
worked example in Alpaca's docs is a debit — so nothing in this layer depends on it,
and the first live fill should be checked against the journal to settle it.

**One deliberate exception to this codebase's raise-on-failure discipline.** A journal
write failure must not raise once an order has been placed: by then the money is
committed, and dying on a full disk would leave a live position with no record anywhere.
`append` is therefore called inside a `try` that, on failure, prints a loud warning
*and the complete record* to stderr and continues. Everywhere else an I/O error should
raise; here the record is the thing being protected, and stderr preserves it.

## `main.py` — the gate and the flow

`--dry-run` stops being `required=True` and joins `--submit` in a mutually exclusive
group with `required=True`. Exactly one must be given: there is no bare invocation, and
no default that trades.

```
parse args (--dry-run xor --submit, exactly one)
fetch, build features, build_decision, print          # unchanged from today
no proposal      -> journal (will_trade false), exit 0
build_order(proposal)
--dry-run        -> print payload, journal (mode dry_run), exit 0
--submit         -> guard: open positions + open orders on the target expiry
                    already on  -> print reason, journal, exit 0
                    otherwise   -> submit, await_fill, print, journal
                                   exit 0 if filled else 1
```

The `--dry-run` branch returns before a `Broker` is constructed, so the safety property
lives in the control flow rather than only in the module list.

The guard runs after the decision, not before it: there is no reason to ask the broker
anything on a day the strategy stands aside.

Exit `1` on a submitted-but-unfilled order — rejected, expired, or still working at
timeout — lets a scheduler distinguish "no trade today, by design" from "tried to trade
and did not get on" without parsing the journal.

## Worked example

Continuing the predecessor spec's numbers: SPY at 765.12, equity $100,000, bullish,
short 761P bid 1.60, long 756P ask 0.95, credit 0.65, 2 contracts, $870 total risk.

```json
{"order_class": "mleg", "qty": "2", "type": "limit",
 "limit_price": "-0.65", "time_in_force": "day",
 "legs": [
   {"symbol": "SPY260905P00761000", "ratio_qty": "1",
    "side": "sell", "position_intent": "sell_to_open"},
   {"symbol": "SPY260905P00756000", "ratio_qty": "1",
    "side": "buy",  "position_intent": "buy_to_open"}]}
```

`qty` is 2 spreads, not 2 contracts. `limit_price` is negative because 0.65 is received.
Guard prefix for this run: `SPY260905`.

## Testing

pytest, no network. `tests/test_broker.py` reuses the `StubSession` idiom from
`tests/test_data.py` — a canned payload per URL suffix, injected through the `session`
parameter — extended with `post`.

`tests/test_orders.py`, pure, no stubs:

- `build_order`: the worked example, field for field. **A dedicated test that a credit
  spread serialises to `"-0.65"`** — the sign that fails silently. `qty` is the spread
  count, not the contract count. Both legs carry `ratio_qty: "1"`; short is
  `sell`/`sell_to_open` and long is `buy`/`buy_to_open`. `time_in_force` comes from
  config.
- `classify`: every one of Alpaca's statuses maps to the intended state, with
  `partially_filled` asserted `WORKING`.
- `existing_exposure`: an open position on the target expiry blocks; a working `mleg`
  order whose **parent symbol is `""` and whose contracts are on `legs[]`** blocks; a
  position on *yesterday's* expiry does **not** block; an equity position does not
  block; nothing open returns `None`.

`tests/test_broker.py`:

- the payload reaches `POST /v2/orders` unmodified;
- `open_orders` sends `status=open` and `nested=true`;
- `open_option_positions` drops the account's equity positions;
- `await_fill` stops on a terminal status, and returns its last observation on timeout
  rather than raising. Poll timing comes from `OrderConfig`, so tests set it near zero.

`tests/test_journal.py`: record shape for all three cases (trade, stand-aside, dry run);
`json.dumps`/`loads` round trip; and that a write failure warns to stderr rather than
propagating.

`tests/test_main.py`, extended: neither flag is an error; both flags are an error;
**`--dry-run` never constructs a `Broker`** — the executable form of the property the
module split exists to provide; a guard hit prints its reason and submits nothing; a
submitted-but-unfilled order exits 1.

## Departures and deliberate omissions

Recorded so they are visible rather than silent:

1. **The limit price is the conservative credit, not the mid**, reversing the
   predecessor spec's expectation. Reasoning under Decisions above.
2. **No closing logic.** Positions are left to expire or be closed by hand. The smoke
   test established that closes are per-leg and must be sequenced short-leg-first or
   Alpaca rejects the long-leg close as uncovered (403); that finding is recorded there
   for whichever session implements exits.
3. **No cancel and no reprice.** `await_fill` is read-only. An order still working at
   timeout is journalled and exits 1; it remains live until its `day` TIF expires.
4. **No aggregate exposure limit.** The account can hold two overlapping spreads —
   yesterday's expiring today and today's expiring tomorrow — so roughly 2% of equity
   may be at risk briefly. That is inherent to a daily 1DTE agent, not something a
   duplicate guard should decide, and a real limit belongs in its own layer.
5. **No market-hours check before submitting.** The predecessor established that a
   closed market is a trading condition, not a data defect. A `day` order placed
   outside the session is queued by Alpaca to the next session, which is visible in the
   journal; guessing at a session calendar here would duplicate the clock the data layer
   already fetches.
6. **`Broker` duplicates `AlpacaClient`'s session setup** rather than sharing it. About
   ten lines, accepted to keep the two modules independent and the read-only client
   read-only.
7. **No `client_order_id`.** Alpaca generates one. A deterministic id derived from the
   date would give idempotency at the API level, but the broker-state guard already
   covers the duplicate case this system has.
