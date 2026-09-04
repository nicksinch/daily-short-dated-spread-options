# Strategy Layer — Design

**Date:** 2026-09-04
**Status:** Approved, ready for implementation planning
**Predecessor:** `docs/superpowers/specs/2026-09-03-data-signal-layer-design.md`
**Source spec:** `docs/start_point_spec.md`

## Scope

The deterministic strategy layer: given a `FeatureSet`, an option chain, a directional
stance and the account equity, decide whether to trade and, if so, produce a fully
specified credit spread — both legs, the credit, the max loss per contract and the
number of contracts.

In the end-to-end pipeline this is everything between the stance and the order:

```
features ✅ → stance (LLM, later) → structure → strikes → sizing → order (later) → log (later)
```

Out of scope, deferred: the LLM stance layer, order construction, order submission,
decision logging, position management, backtesting. **Nothing in this layer can reach
an order endpoint.** The stance arrives as a plain enum value on a function argument,
supplied for now by a `--stance` command-line flag.

## Decisions

Answered by the project owner during design; recorded because none follows from the
code.

| Decision | Choice |
| --- | --- |
| Stance → structure | bullish → put credit spread; bearish → call credit spread; neutral → stand aside |
| Short strike | contract whose absolute delta is closest to `delta_target` (0.30); ties broken toward the lower absolute delta |
| Long strike | fixed $5 further out of the money than the short strike |
| Sizing | maximum risk equals 1% of current account equity |
| Strike band | widened from $8 to $20 each side of spot, so the protective wing is inside the fetched chain |
| Stance source | a `--stance` flag on the dry run; no LLM code and no stub |
| Trade gate | every feature must be `Status.OK`; any missing or stale feature stands aside |
| Credit basis | short leg bid minus long leg ask — the credit actually obtainable by crossing |
| Minimum credit | rejected below 10% of the spread width ($0.50 on a $5 spread) |

Three of these deserve their reasoning recorded.

**Ties break toward the lower absolute delta.** Between a 0.25 and a 0.35 delta both
0.05 from target, the 0.25 sits further out of the money. The rule resolves the tie in
the direction that is short less risk, so the arbitrary case is at least consistently
arbitrary.

**Credit is the crossing credit, not the mid.** Sizing divides a risk budget by max
loss, and max loss is `width − credit`. Overstating the credit understates max loss and
oversizes the position, so the error lands on the wrong side. Taking `short.bid −
long.ask` gives a credit that is both achievable and conservative. The mid remains the
right basis for the eventual limit price; that belongs to the order layer.

**A closed market does not stand the agent aside.** The predecessor design established
that a closed market is a trading condition rather than a data defect, and
`quote_freshness` already declines to apply the staleness threshold when the market is
shut. That holds here: an off-hours dry run should print a real proposal off the last
session's data. Whether it is a legal moment to submit is the order layer's question,
not this one.

## Module layout

```
config.py            FeatureConfig, StrategyConfig          (StrategyConfig is new)
features.py          unchanged
data.py              AlpacaClient + Account, get_account()  (both new)
strategy.py          Stance, SpreadLeg, SpreadProposal, Decision, pure functions  (new)
main.py              --dry-run --stance                     (flag is new)
tests/test_strategy.py                                      (new)
```

`strategy.py` performs no I/O. It imports `OptionQuote` from `data.py` and `FeatureSet`
from `features.py` as record types only, the same way `features.py` imports records from
`data.py` without importing its client. Every input — features, chain, stance, equity —
is passed in, so the whole layer is testable against hand-computed fixtures with no
network and no LLM.

## Configuration

A second frozen dataclass in `config.py`. `FeatureConfig` stays about feature
calculation; the strategy tunables sit apart rather than swelling a class whose name no
longer fits them.

```python
@dataclass(frozen=True)
class StrategyConfig:
    delta_target: float = 0.30          # absolute delta of the short leg
    spread_width_dollars: float = 5.0   # long leg this far further OTM
    risk_fraction: float = 0.01         # max risk as a share of account equity
    min_credit_fraction: float = 0.10   # reject credit below this share of width
    contract_multiplier: int = 100      # US equity options, shares per contract
```

Two changes to `FeatureConfig`:

- `delta_target` **moves** to `StrategyConfig`. It was parked in `FeatureConfig` with a
  comment marking it unused; this is the session that uses it, and it belongs with the
  selection it drives.
- `strike_band_dollars` goes **8 → 20**. A $5 wing beyond a short strike roughly 4
  points out of the money needs about 9 points of room; 20 leaves margin without
  approaching any page limit. On SPY's $1 grid the request becomes 41 strikes across
  calls and puts — 82 contracts, well under the 1000 the chain request already asks for.

## Result types

Standing aside is a normal outcome, not an error, so it is a value with a reason —
mirroring how `Feature` carries a `detail` for a non-`ok` status.

```python
class Stance(str, Enum):
    BULLISH = "bullish"
    BEARISH = "bearish"
    NEUTRAL = "neutral"

@dataclass(frozen=True)
class SpreadLeg:
    symbol: str          # OCC symbol, ready for the order layer
    strike: float
    right: str           # "C" or "P"
    side: str            # "sell" (short leg) or "buy" (long leg)
    # short.ask, long.bid and long.delta are not demanded by net_credit or by
    # selection (see below), so these three sides may be absent -- float | None.
    delta: float | None
    bid: float | None
    ask: float | None

@dataclass(frozen=True)
class SpreadProposal:
    structure: str                 # "put_credit" or "call_credit"
    short_leg: SpreadLeg
    long_leg: SpreadLeg
    credit: float                  # per share
    max_loss_per_contract: float   # dollars
    quantity: int                  # contracts
    total_risk: float              # dollars
    risk_budget: float             # dollars

@dataclass(frozen=True)
class Decision:
    stance: Stance
    proposal: SpreadProposal | None
    reason: str

    @property
    def will_trade(self) -> bool:
        return self.proposal is not None
```

`reason` is populated in both cases: standing aside explains what stopped it, trading
states what was chosen. Exceptions stay reserved for HTTP, network and programming
errors, as in the layers below.

## `strategy.py`

Two groups, split the same way `features.py` is: arithmetic and selection that know
only their arguments, then one assembler that applies the gates in order.

### Pure functions

```python
def structure_for(stance: Stance) -> str | None
def select_short_leg(chain, right: str, delta_target: float) -> OptionQuote | None
def select_long_leg(chain, right: str, short_strike: float, width: float) -> OptionQuote | None
def net_credit(short: OptionQuote, long: OptionQuote, width: float) -> float | None
def max_loss_per_contract(width: float, credit: float, multiplier: int) -> float
def position_size(equity: float, risk_fraction: float, max_loss: float) -> int
```

**`structure_for`** — `BULLISH → "put_credit"`, `BEARISH → "call_credit"`,
`NEUTRAL → None`.

**`select_short_leg`** — candidates are contracts of the required right whose delta is
present and non-zero. A zero delta is the synthesised-zero failure mode the predecessor
design documented at 0DTE; treating it as a real 0.00 would rank it as the furthest
possible strike rather than as absent data. The winner minimises
`(abs(abs(delta) - delta_target), abs(delta))` — nearest absolute delta first, lower
absolute delta as the tie-break. Returns `None` on an empty candidate list.

**`select_long_leg`** — the strike is `short_strike - width` for puts and
`short_strike + width` for calls, matched exactly against the chain. Exact float
comparison is safe here: strikes are parsed as integer thousandths divided by 1000, and
a $5.00 offset from any strike on SPY's grid is exactly representable. Returns `None`
when that strike is not in the chain, which is a real possibility at the band edge.

**`net_credit`** — `short.bid - long.ask`, or `None` if either of those two sides is
absent or non-positive. Only the sides that are actually paid and received are
required; the short leg's ask and the long leg's bid play no part in the credit and are
not demanded.

**`max_loss_per_contract`** — `(width - credit) * multiplier`. The credit is already
received, so it reduces the loss; the assembler never calls this before `net_credit`
has produced a positive number.

**`position_size`** — `floor(equity * risk_fraction / max_loss)`, returned as an `int`.
Zero is a legitimate answer and means the budget cannot fund one contract. `max_loss` is
guaranteed positive by the gate that precedes it, so there is no division by zero to
guard here.

### `build_decision`

```python
def build_decision(
    features: FeatureSet,
    chain: Sequence[OptionQuote],
    stance: Stance,
    equity: float,
    cfg: StrategyConfig,
) -> Decision
```

Gates, in this order, each returning a `Decision` with no proposal and a reason naming
what stopped it:

1. **Neutral stance** — `"neutral stance: no directional edge"`.
2. **A feature is not `OK`** — every one of the five is checked and the reason names
   each offender with its status and detail, e.g. `"features not ok: atm_iv (missing:
   no implied volatility on the 765 call)"`. Checked before any chain work so the
   reason describes the cause rather than a symptom of it.
3. **No short-leg candidate** — no contract of the required right carries a usable
   delta.
4. **Long strike absent** — names the strike that was sought.
5. **No credit** — a required quote side is missing, or the credit is not positive, or
   the credit equals or exceeds the width. The last case would make max loss zero or
   negative and divide the sizing by zero; a spread that cannot lose is a quoting fault,
   not an opportunity, so it stands aside and says so.
6. **Credit below the floor** — states the credit and the minimum, e.g. `"credit 0.30
   below minimum 0.50 (10% of $5.00 width)"`.
7. **Quantity below one** — states the max loss per contract against the risk budget.

Past all seven, the `Decision` carries a `SpreadProposal` and a reason summarising the
structure, the two strikes and the size.

The order matters twice: the stance gate comes first because a neutral day should not
report a data problem it never depended on, and the feature gate comes before strike
selection because a stale spot invalidates the strike search that would follow.

## `data.py`

One addition, following the existing shape.

```python
@dataclass(frozen=True)
class Account:
    equity: float

def get_account(self) -> Account
```

`GET /v2/account` on `TRADING_URL`. The account always exists, so there is no `None`
path: an HTTP failure raises, as everywhere else in this module. Alpaca returns the
numeric fields on this endpoint as JSON strings, so `equity` is parsed with `float()`.
Only `equity` is read — buying power and the rest are not needed by this layer and are
not modelled.

## `main.py` — dry run

`--dry-run` stays required. A new `--stance` is required too, with choices `bullish`,
`bearish`, `neutral`, parsed into the `Stance` enum. The run fetches what it already
fetched, adds the account, builds the features, then builds the decision and prints
both.

```text
SPY  as of 2026-09-04T12:40:00-04:00  market_open=True  next_open=2026-09-05T09:30-04:00
expiry: 2026-09-05

spot              765.1200  ok      2026-09-04T12:39:58-04:00
rv20                0.1483  ok      2026-09-03T00:00:00+00:00
spot/sma20          1.0121  ok      2026-09-04T12:39:58-04:00  (sma20=756.02 over closes to 2026-09-03)
spot/sma50          1.0388  ok      2026-09-04T12:39:58-04:00  (sma50=736.58 over closes to 2026-09-03)
atm_iv              0.1642  ok      2026-09-04T12:39:12-04:00  (mean of 765C 0.1590 and 765P 0.1694)

stance: bullish
decision: trade put_credit
  sell  SPY260905P00761000  761.0P  delta -0.3020  bid 1.60  ask 1.68
  buy   SPY260905P00756000  756.0P  delta -0.1810  bid 0.88  ask 0.95
  credit 0.65  max loss/contract $435.00  quantity 2  total risk $870.00 of $1000.00 budget
```

Standing aside prints the reason in place of the legs:

```text
stance: neutral
decision: stand aside — neutral stance: no directional edge
```

The dry run still constructs no order and submits nothing.

## Worked example

The numbers the tests are built from. SPY at 765.12, equity $100,000, bullish, $5 width,
0.30 delta target, 1% risk.

| Step | Value |
| --- | --- |
| Structure | put credit spread |
| Short leg | 761P, delta −0.3020 — nearest absolute delta to 0.30 |
| Long leg | 756P (761 − 5), ask 0.95 |
| Credit | 1.60 − 0.95 = **0.65** per share |
| Minimum credit | 0.10 × 5.00 = 0.50 → 0.65 passes |
| Max loss per contract | (5.00 − 0.65) × 100 = **$435.00** |
| Risk budget | 0.01 × 100,000 = **$1,000.00** |
| Quantity | floor(1000 / 435) = **2** |
| Total risk | 2 × 435 = **$870.00** |

## Testing

`tests/test_strategy.py`, pytest, no network. Chain fixtures are lists of `OptionQuote`
built in the test file, as the feature tests already do for bars and quotes.

Pure functions, against hand-computed values:

- `structure_for` for all three stances.
- `select_short_leg`: nearest delta wins; the tie at 0.25 versus 0.35 resolves to 0.25;
  contracts of the wrong right are ignored; a `None` delta and a zero delta are both
  excluded; an empty candidate list returns `None`.
- `select_long_leg`: puts go down $5 and calls go up $5; a missing strike returns
  `None`.
- `net_credit`: the worked example; `None` when the short bid is absent, when the long
  ask is absent, when the credit is zero or negative, and when it equals or exceeds the
  width.
- `max_loss_per_contract` and `position_size`: the worked example, plus a budget too
  small for one contract returning 0, plus the exact-fit boundary.

`build_decision`, one test per gate, asserting no proposal and the reason naming the
cause: neutral stance; a stale feature and a missing feature; no usable delta; absent
long strike; missing quote side; a credit at or above the width; credit under the floor; quantity
zero. Then the happy
path asserting every field of the proposal against the worked example.

`main.py`: extend the existing fake-client tests to cover `--stance` parsing, the
account fetch, and that a stand-aside prints its reason. The rule that the dry run
reaches no order endpoint stays asserted as it is today.

## Departures and deliberate omissions

Recorded so they are visible rather than silent:

1. **No limit price is computed.** Pricing the order is the order layer's job; this
   layer reports the credit it sized against.
2. **No LLM code, not even a stub.** The stance is an argument. The eventual LLM layer
   fills that argument and deletes nothing.
3. **`delta_target` moves out of `FeatureConfig`**, which is a small change to an
   existing public dataclass, made now because this is the session that gives it a home.
4. **One contract multiplier, not a per-contract one.** SPY equity options are 100
   shares; a multiplier that varies by contract is a problem this system does not have.
5. **No minimum-liquidity or maximum-spread check on the legs.** The credit floor
   already rejects the thinnest structures, and a bid/ask width rule needs live
   observation to calibrate rather than a guessed constant.
