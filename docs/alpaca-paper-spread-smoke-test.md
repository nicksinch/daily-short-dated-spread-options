# Alpaca CLI Smoke Test — Manual 1-Lot SPY 0DTE Call Debit Spread (Paper)

**Purpose:** Prove the full order-placement path works end-to-end on the paper account
before writing any strategy code: contract discovery → pricing → dry-run validation →
live multi-leg submission → fill verification → position close.

**Environment:** `alpaca` CLI v0.0.14, profile `paper` (`paper-api.alpaca.markets`),
account `PA3FTHQC3U65`, `options_trading_level: 3`.

**Date of test:** 2026-08-31 (market open, SPY ~$766.06). Steps 0–2 (profile/doctor/account/clock
checks) are covered in the prior conversation and omitted here — this log starts at step 3.

---

## Step 3 — Discover live, tradable contracts

Goal: find real OCC-format contract symbols for today's (0DTE) SPY expiration, near the
money, with enough quoted size to actually fill a 1-lot.

### 3.1 Get the underlying's current price

```bash
alpaca data latest-quote --symbol SPY
```

**Why:** Need a spot reference before picking strikes — no point pulling a contract chain
without knowing where the underlying is trading.

**Parameters:**
- `--symbol SPY` — single-symbol lookup (there's also `latest-quotes` for multiple).

**Output:**
```json
{
  "quote": {
    "ap": 766.08,
    "as": 280,
    "ax": "V",
    "bp": 766.05,
    "bs": 120,
    "bx": "V",
    "c": ["R"],
    "t": "2026-08-31T14:01:37.224776975Z",
    "z": "B"
  },
  "symbol": "SPY"
}
```

**Reading it:** `bp`/`ap` are bid/ask price ($766.05 / $766.08); `bs`/`as` are bid/ask size
in round lots. SPY is trading essentially at $766.06 mid. This anchors which strikes count
as "near the money."

---

### 3.2 List today's SPY call contracts near the money

```bash
alpaca option contracts --underlying-symbols SPY \
  --expiration-date 2026-08-31 \
  --strike-price-gte 760 --strike-price-lte 775 \
  --type call
```

**Why:** Confirms contracts for *today's* expiration actually exist and are `tradable`
before trying to trade them — SPY has daily expirations, but you don't want to discover a
bad symbol only at order-submit time.

**Parameters:**
- `--underlying-symbols SPY` — filter to the SPY option chain (accepts a comma-separated list).
- `--expiration-date 2026-08-31` — exact-date filter (today = 0DTE), vs. `--expiration-date-gte/-lte` for a range.
- `--strike-price-gte 760` / `--strike-price-lte 775` — bracket the search around spot ($766) instead of pulling the entire chain.
- `--type call` — restrict to calls (a debit call spread only needs call legs).

**Output (abridged — full response had 15 contracts, 760–775 strike):**
```json
{
  "option_contracts": [
    {
      "close_price": "9.53",
      "expiration_date": "2026-08-31",
      "id": "2f2e0faf-f551-483e-822e-1584767b5a51",
      "multiplier": "100",
      "name": "SPY Aug 31 2026 760 Call",
      "open_interest": "2746",
      "root_symbol": "SPY",
      "status": "active",
      "strike_price": "760",
      "style": "american",
      "symbol": "SPY260831C00760000",
      "tradable": true,
      "type": "call",
      "underlying_symbol": "SPY"
    },
    ...
    {
      "close_price": "3.21", "strike_price": "767",
      "symbol": "SPY260831C00767000", "open_interest": "1222",
      "tradable": true, "status": "active"
    },
    ...
    {
      "close_price": "1.83", "strike_price": "769",
      "symbol": "SPY260831C00769000", "open_interest": "4091",
      "tradable": true, "status": "active"
    },
    {
      "close_price": "0.11", "strike_price": "775",
      "symbol": "SPY260831C00775000", "open_interest": "5094",
      "tradable": true, "status": "active"
    }
  ]
}
```

**Reading it:** Each contract has an OCC-format `symbol` (`SPY` + `260831` expiry +
`C`/`P` + 8-digit strike ×1000). `tradable: true` and `status: "active"` on every
strike from 760–775 confirmed the whole neighborhood was usable. `close_price` here is
Friday's (2026-08-28) settle — stale by the weekend, useful only as a rough sanity check,
not for pricing the order (that's what step 3.3 is for). `open_interest` gave an early
liquidity signal (1,222–10,476 contracts across the strikes checked).

---

### 3.3 Get live quotes for the candidate strikes

```bash
alpaca data option chain --underlying-symbol SPY \
  --expiration-date 2026-08-31 --type call \
  --strike-price-gte 766 --strike-price-lte 771
```

**Why:** `option contracts` only returns static reference data + Friday's stale close.
Need *live* bid/ask/size to (a) pick a spread width that's actually liquid enough to fill
a 1-lot, and (b) compute a realistic limit price for the dry-run in step 4.

**Parameters:**
- `--underlying-symbol SPY` — note singular flag name here (vs. `--underlying-symbols` plural in `option contracts`) — different subcommand, different flag.
- `--expiration-date 2026-08-31` — same 0DTE expiration as step 3.2.
- `--type call` — calls only.
- `--strike-price-gte 766` / `--strike-price-lte 771` — narrowed further to strikes actually near spot ($766), since this endpoint returns full snapshots (quote + trade + bars + greeks) per contract and is heavier than the contracts list.

**Output (abridged to the two strikes actually used):**
```json
{
  "snapshots": {
    "SPY260831C00767000": {
      "latestQuote": { "ap": 0.70, "as": 473, "bp": 0.65, "bs": 285, "t": "2026-08-31T14:02:04.040056499Z" },
      "latestTrade": { "p": 0.69, "s": 10 },
      "dailyBar": { "o": 2.72, "h": 2.72, "l": 0.64, "c": 1.05, "v": 42464 }
    },
    "SPY260831C00769000": {
      "latestQuote": { "ap": 0.22, "as": 2224, "bp": 0.21, "bs": 500, "t": "2026-08-31T14:02:03.746090092Z" },
      "latestTrade": { "p": 0.22, "s": 1 },
      "dailyBar": { "o": 0.58, "h": 1.85, "l": 0.19, "c": 0.36, "v": 25842 }
    }
  }
}
```

**Reading it:**
- `767C`: bid $0.65 / ask $0.70 (5¢-wide market), quoted size 285×473 contracts — plenty of depth for a 1-lot.
- `769C`: bid $0.21 / ask $0.22 (1¢-wide market), quoted size 500×2224 — very liquid.
- `greeks` in the raw response were all zeroed (`delta/gamma/rho/theta/vega: 0`) — this paper/indicative feed doesn't compute live greeks, so strike selection was done on quotes/spread-width/size instead, not delta targeting.

**Decision:** Buy `SPY260831C00767000` / Sell `SPY260831C00769000` — a $2-wide call debit
spread. Mid-price net debit = ask(long) − bid(short), and bid(long) − ask(short) for the
other bound: $0.65−$0.22=$0.43 to $0.70−$0.21=$0.49, mid ≈ **$0.46–0.47**. Picked because
both legs are liquid, the width is small (cheap 1-lot, defined risk), and it's plainly OTM
relative to the $766 spot.

---

## Step 4 — Dry-run the multi-leg order

```bash
alpaca order submit \
  --order-class mleg \
  --qty 1 \
  --type limit \
  --limit-price 0.47 \
  --time-in-force day \
  --legs '[
    {"symbol":"SPY260831C00767000","ratio_qty":"1","side":"buy","position_intent":"buy_to_open"},
    {"symbol":"SPY260831C00769000","ratio_qty":"1","side":"sell","position_intent":"sell_to_open"}
  ]' \
  --dry-run
```

**Why:** Validates the whole payload — flag names, JSON shape of `--legs`, order-class
compatibility — without ever hitting the live order book. This is the step that catches
a malformed request before it becomes a real (even if paper) order.

**Parameters:**
- `--order-class mleg` — tells Alpaca this is a combined multi-leg order (fills as one unit or not at all), required for any spread.
- `--qty 1` — the *parent* order quantity (1 spread = 1 lot). For `mleg`, `--symbol`/`--side` at the top level are omitted; those live per-leg instead.
- `--type limit` / `--limit-price 0.47` — net debit/credit limit for the whole combo, not per-leg. $0.47 was chosen at the top of the mid-to-worst-case band computed in step 3.3.
- `--time-in-force day` — order dies at end of regular session if unfilled (appropriate for 0DTE — no reason to let it linger overnight).
- `--legs` — JSON array string, one object per leg. Confirmed against Alpaca's own multi-leg docs (`us/options-level-3-trading`), not guessed:
  - `symbol` — the OCC contract symbol from step 3.
  - `ratio_qty` — proportional weight of this leg relative to the order qty; `"1"` on both legs = a simple 1:1 vertical. (Alpaca requires the GCD of all `ratio_qty` values across legs to be 1 — e.g. `4`/`2` would be rejected as unreduced.)
  - `side` — `buy` or `sell` execution direction for this leg.
  - `position_intent` — `buy_to_open`/`sell_to_open` (or `*_to_close` when closing/rolling) — tells Alpaca whether this leg is opening new exposure or closing existing exposure, which matters for margin calc and for whether "uncovered" checks apply (see step 7).
- `--dry-run` — prints the constructed request body; submits nothing.

**Output:**
```json
{
  "advanced_instructions": {},
  "legs": [
    {
      "position_intent": "buy_to_open",
      "ratio_qty": "1",
      "side": "buy",
      "symbol": "SPY260831C00767000"
    },
    {
      "position_intent": "sell_to_open",
      "ratio_qty": "1",
      "side": "sell",
      "symbol": "SPY260831C00769000"
    }
  ],
  "limit_price": "0.47",
  "order_class": "mleg",
  "qty": "1",
  "time_in_force": "day",
  "type": "limit"
}
```

**Reading it:** The CLI echoed back exactly the request it would have sent — `order_class:
mleg`, both legs present with correct `side`/`position_intent`, top-level `limit_price` and
`qty` set as intended. This confirmed the `--legs` JSON shape and flag combination were
valid before spending a real (paper) order attempt on it.

---

## Step 5 — Submit the live order (paper)

```bash
alpaca order submit \
  --order-class mleg \
  --qty 1 \
  --type limit \
  --limit-price 0.47 \
  --time-in-force day \
  --legs '[
    {"symbol":"SPY260831C00767000","ratio_qty":"1","side":"buy","position_intent":"buy_to_open"},
    {"symbol":"SPY260831C00769000","ratio_qty":"1","side":"sell","position_intent":"sell_to_open"}
  ]'
```

**Why:** Identical payload to step 4, `--dry-run` dropped — this is the one order-placing
action in the whole test, on the paper account.

**Parameters:** Same as step 4 (see above) — deliberately unchanged, so the only variable
between dry-run and live submit was the removal of `--dry-run` itself.

**Output:**
```json
{
  "client_order_id": "3fb74899-c572-40c8-a009-78e651820cfd",
  "created_at": "2026-08-31T14:06:14.723228947Z",
  "expires_at": "2026-08-31T20:15:00Z",
  "filled_qty": "0",
  "id": "b889185b-33be-41d5-980b-e929bd540902",
  "legs": [
    {
      "asset_id": "93fb21bb-ce71-4111-8de1-6e4e2440c176",
      "position_intent": "buy_to_open",
      "qty": "1",
      "side": "buy",
      "status": "pending_new",
      "symbol": "SPY260831C00767000",
      "time_in_force": "day",
      "type": "limit"
    },
    {
      "asset_id": "34e75f85-43ae-45f7-b3aa-dc30d1e702f4",
      "position_intent": "sell_to_open",
      "qty": "1",
      "side": "sell",
      "status": "pending_new",
      "symbol": "SPY260831C00769000",
      "time_in_force": "day",
      "type": "limit"
    }
  ],
  "limit_price": "0.47",
  "order_class": "mleg",
  "qty": "1",
  "status": "pending_new",
  "time_in_force": "day",
  "type": "limit"
}
```

**Reading it:** Parent order `id: b889185b-...` and both legs came back `status:
"pending_new"` — Alpaca's normal transient acknowledgment state immediately after
acceptance, before the matching engine has processed it (not an error, not yet a fill).
`expires_at: 20:15:00Z` = 16:15 ET, past today's 16:00 ET close, consistent with a `day`
TIF that expires at end of regular session. This confirmed the order was *accepted*, not
that it had filled — that's checked next.

---

## Step 6 — Verify fill and resulting positions

### 6.1 Poll the order status

```bash
alpaca order get --order-id b889185b-33be-41d5-980b-e929bd540902 --jq '{status, filled_qty, legs: [.legs[] | {symbol, status, filled_qty}]}'
```

**Why:** `pending_new` in step 5's response was a submission ack, not a fill confirmation.
Re-fetching the order by ID is how you find out what actually happened.

**Parameters:**
- `--order-id <id>` — the parent mleg order ID captured from step 5's response.
- `--jq '{...}'` — server-side/client-side JSON filter to print only the fields that matter (status + fill qty, per leg) instead of the full order object again.

**Output:**
```json
{
  "filled_qty": "1",
  "legs": [
    { "filled_qty": "1", "status": "filled", "symbol": "SPY260831C00767000" },
    { "filled_qty": "1", "status": "filled", "symbol": "SPY260831C00769000" }
  ],
  "status": "filled"
}
```

**Reading it:** Parent and both legs report `status: "filled"`, `filled_qty: "1"` — the
1-lot spread filled completely, as one unit (consistent with `order_class: mleg`'s
all-or-nothing semantics).

### 6.2 Confirm the resulting positions

```bash
alpaca position list
```

**Why:** Cross-checks the order-level fill report against the account's actual position
blotter — the source of truth for what you own.

**Parameters:** None — lists all open positions across asset classes.

**Output (option legs only; a pre-existing, unrelated long 1-share SPY equity position was also present and is omitted here):**
```json
[
  {
    "asset_class": "us_option",
    "avg_entry_price": "0.65",
    "cost_basis": "65",
    "qty": "1",
    "side": "long",
    "symbol": "SPY260831C00767000"
  },
  {
    "asset_class": "us_option",
    "avg_entry_price": "0.18",
    "cost_basis": "-18",
    "qty": "-1",
    "side": "short",
    "symbol": "SPY260831C00769000"
  }
]
```

**Reading it:** Long 767C filled at $0.65, short 769C filled at $0.18. Net debit paid =
$0.65 − $0.18 = **$0.47** — exactly the limit price submitted, confirming the mleg net
price constraint was respected (not just "each leg somewhere near mid").

---

## Step 7 — Close both legs

### 7.1 First attempt — wrong flag name (failed)

```bash
alpaca position close --symbol SPY260831C00767000
alpaca position close --symbol SPY260831C00769000
```

**Why:** Intended to flatten both legs individually (Alpaca has no single "close this
combo" endpoint for existing positions — you close each leg's position separately).

**Output (both commands, identical error):**
```json
{
  "code": 0,
  "error": "unknown flag: --symbol",
  "hint": "",
  "status": 0
}
```

**Reading it:** `--symbol` doesn't exist on `position close` — guessed wrong from
pattern-matching `order submit --symbol`. Needed the real flag name.

### 7.2 Check the actual flag

```bash
alpaca position close --help
```

**Output (relevant excerpt):**
```
Flags:
      --percentage string           percentage of position to liquidate
      --qty string                  the number of shares to liquidate. Can accept up to 9 decimal points. Cannot work with percentage
      --symbol-or-asset-id string   symbol or assetId
```

**Reading it:** Correct flag is `--symbol-or-asset-id` (accepts either an OCC symbol or
the internal asset UUID). No `--qty` given defaults to closing the entire position.

### 7.3 Retry — short leg (succeeded)

```bash
alpaca position close --symbol-or-asset-id SPY260831C00769000
```

**Parameters:**
- `--symbol-or-asset-id SPY260831C00769000` — the short leg's OCC symbol. No `--qty`/`--percentage` — closes the full 1-contract position.

**Output:**
```json
{
  "client_order_id": "c6ea85dd-3869-40d2-bd9d-7cbf31f944f1",
  "id": "5c8b2380-9b5f-4f8b-ac43-b9db60f5bb40",
  "order_class": "simple",
  "order_type": "market",
  "position_intent": "buy_to_close",
  "qty": "1",
  "side": "buy",
  "status": "pending_new",
  "symbol": "SPY260831C00769000",
  "time_in_force": "day",
  "type": "market"
}
```

**Reading it:** `position close` submits a plain `order_class: "simple"` **market** order
(not `mleg`) with `position_intent: buy_to_close` — closing individual legs is just a
regular single-leg order under the hood, market-priced for a fast, guaranteed exit.

### 7.4 Retry — long leg (failed: uncovered)

```bash
alpaca position close --symbol-or-asset-id SPY260831C00767000
```

**Output:**
```json
{
  "code": 40310000,
  "error": "account not eligible to trade uncovered option contracts",
  "hint": "Forbidden. Check your permissions, account status, minimum order size, or feature availability.",
  "method": "DELETE",
  "path": "https://paper-api.alpaca.markets/v2/positions/SPY260831C00767000",
  "status": 403
}
```

**Reading it:** Because both close requests were issued back-to-back, closing the *long*
767C first — while the short 769C was still open — would have momentarily left a naked
short call (selling the long call removes the coverage the short call relied on). Alpaca
rejected it with a 403 rather than allow that exposure, even transiently. **Key finding:**
closing a vertical spread leg-by-leg is not order-independent — the short leg must close
before the long leg.

### 7.5 Confirm the short leg actually filled

```bash
alpaca order get --order-id 5c8b2380-9b5f-4f8b-ac43-b9db60f5bb40 --jq '{status, filled_qty}'
```

**Why:** Before retrying the long-leg close, needed to confirm the short leg's
buy-to-close order (7.3) had actually filled — not just been accepted — since the
"uncovered" rejection in 7.4 could otherwise repeat if the short leg were still open.

**Output:**
```json
{ "filled_qty": "1", "status": "filled" }
```

**Reading it:** Short leg fully closed. Safe to retry the long leg now.

### 7.6 Retry — long leg (succeeded)

```bash
alpaca position close --symbol-or-asset-id SPY260831C00767000
```

**Output:**
```json
{
  "client_order_id": "29752f5d-b236-452f-9561-7341b8aacc9a",
  "id": "e8871d59-8b82-4762-9d82-f8c4b551fe5b",
  "order_class": "simple",
  "order_type": "market",
  "position_intent": "sell_to_close",
  "qty": "1",
  "side": "sell",
  "status": "pending_new",
  "symbol": "SPY260831C00767000",
  "time_in_force": "day",
  "type": "market"
}
```

**Reading it:** Accepted this time — `position_intent: sell_to_close`, market order,
`status: pending_new`. With the short leg already flat, this order carries no
"uncovered" risk.

### 7.7 Confirm the long leg filled

```bash
alpaca order get --order-id e8871d59-8b82-4762-9d82-f8c4b551fe5b --jq '{status, filled_qty, filled_avg_price}'
```

**Output:**
```json
{ "filled_avg_price": "0.56", "filled_qty": "1", "status": "filled" }
```

**Reading it:** Long 767C closed at $0.56 (vs. $0.65 opened at — a $0.09/contract loss on
that leg alone, offset by whatever the short leg's close cost/credited; net P&L on the
round trip is a secondary detail, not the point of this test).

### 7.8 Confirm the account is flat

```bash
alpaca position list --jq '[.[] | select(.asset_class=="us_option")]'
```

**Why:** Final check that both legs are actually gone from the position blotter, not just
that closing orders were accepted.

**Parameters:**
- `--jq '[.[] | select(.asset_class=="us_option")]'` — filters the full position list down to option positions only (to ignore the unrelated pre-existing SPY equity share).

**Output:**
```json
[]
```

**Reading it:** Empty array — no option positions remain. Both legs confirmed flat.

---

## Summary

| # | Action | Symbol | Side | Price |
|---|---|---|---|---|
| Open | mleg buy_to_open | SPY260831C00767000 | buy | $0.65 |
| Open | mleg sell_to_open | SPY260831C00769000 | sell | $0.18 |
| Close | buy_to_close (market) | SPY260831C00769000 | buy | (market) |
| Close | sell_to_close (market) | SPY260831C00767000 | sell | $0.56 |

**Net on open:** $0.47 debit (matched limit exactly).

### Findings to carry into strategy code

1. **`--legs` is a raw JSON array string**, not a series of repeated flags — must be
   built/escaped carefully from code (`symbol`, `ratio_qty`, `side`, `position_intent`
   per leg). `ratio_qty` values across legs must share a GCD of 1 (no unreduced ratios
   like 4:2).
2. **Multi-leg opens are one atomic order** (`order_class: mleg`) but **closes are not** —
   `position close` has no combo-aware equivalent; each leg closes as its own `simple`
   market order.
3. **Leg-close ordering matters**: close short leg(s) before long leg(s) on a vertical, or
   the long-leg close is rejected (403, "uncovered option contracts") because it would
   transiently leave a naked short. Any auto-exit logic must sequence closes accordingly
   (or check for and handle the 403 with a retry-after-short-closes strategy).
4. **`position close` flag is `--symbol-or-asset-id`**, not `--symbol` (that's only on
   `order submit`) — worth a `--help` check per subcommand rather than assuming flag
   names are consistent across the CLI.
5. **Options `greeks` came back zeroed** on this data feed/tier — strike selection needs
   to rely on quote/size/spread-width, not delta, unless a different feed is configured.