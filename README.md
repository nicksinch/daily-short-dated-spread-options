# AI Options Trading Agent

A small educational AI trading agent built with the **Alpaca Trading API**.

The goal of this project is not to build a sophisticated trading system or make real-money investment decisions. It is a hands-on project for learning:

* Options fundamentals
* Credit/debit spreads
* Basic market features
* Risk management
* Options Greeks, especially delta
* Alpaca's trading and market-data APIs
* How an LLM can provide a simple directional signal while keeping trading decisions deterministic

> **Educational project — not financial advice and not intended for real-money trading.**

---

## How It Works

The agent follows a simple pipeline:

```text
Market Data
    ↓
Features
    ↓
LLM Directional Stance
    ↓
Bullish / Bearish / Neutral
    ↓
Deterministic Option Strategy
    ↓
Strike Selection
    ↓
Risk-Based Position Sizing
    ↓
Multi-Leg Limit Order
    ↓
JSON Decision Log
```

The LLM is only responsible for determining the **market stance**. Everything consequential after that is deterministic.

---

## 1. Market Features

The agent looks at a few simple signals for SPY:

### SMA20 / SMA50

Simple moving averages give us a basic idea of trend.

* `SPY > SMA20 > SMA50` → generally bullish
* `SPY < SMA20 < SMA50` → generally bearish

### Realized Volatility (RV20)

Realized volatility measures how much SPY has actually been moving recently.

It answers:

> **"How much has the market been moving?"**

### Implied Volatility (IV)

Implied volatility comes from the options market and represents the movement that options prices are implying for the future.

It answers:

> **"How much movement is the options market pricing in?"**

In short:

```text
RV = recent actual movement
IV = market-implied future movement
```

The feature layer also provides the current SPY price and ATM (at-the-money) IV.

---

## 2. Market Stance

The LLM receives the calculated features and returns exactly one of:

```text
BULLISH
BEARISH
NEUTRAL
```

The LLM does **not** choose strikes, position size, contracts, or order prices.

This keeps the system simple and prevents the model from making arbitrary trading decisions.

---

## 3. From Direction to Strategy

The strategy maps the stance to a simple options position:

| Stance  | Strategy           |
| ------- | ------------------ |
| Bullish | Put Credit Spread  |
| Bearish | Call Credit Spread |
| Neutral | No trade           |

### Why?

If we are **bullish**, we expect SPY to stay above a certain price.

So we can:

```text
SELL  Put closer to SPY
BUY   Put further below
```

This is a **Put Credit Spread**.

If we are **bearish**, we expect SPY to stay below a certain price.

So we can:

```text
SELL  Call closer to SPY
BUY   Call further above
```

This is a **Call Credit Spread**.

---

## 4. Credit vs Debit

The terms simply describe the cash flow when opening the position.

### Credit

A **credit** means we receive money upfront.

Example:

```text
Sell option   +$3.00
Buy option    -$1.00
--------------------
Net credit    +$2.00
```

For one options contract:

```text
$2.00 × 100 = $200 received
```

Credit spreads therefore involve **selling an option and buying another option for protection**.

### Debit

A **debit** is the opposite: we pay money upfront.

```text
Buy option    -$4.00
Sell option   +$2.00
--------------------
Net debit     -$2.00
```

Our strategy uses **credit spreads**.

---

## 5. Example: Bullish (Put Credit Spread)

Both examples use the same starting point, taken from a real dry run of the agent:

```text
SPY spot        = $772.15
Expiry          = next trading day (1DTE)
Account equity  = $99,996.63
Risk budget     = 1% of equity = $999.97
```

The option quotes below are illustrative, chosen so that the numbers match that run.
Section 8 explains the sizing rules and section 9 the minimum credit.

### Step 1: Pick the legs

The agent is **bullish**, so it sells a put below spot and buys a cheaper put $5 further down as protection.

| Leg   | Contract | Strike | Delta | Bid  | Ask  |
| ----- | -------- | ------ | ----- | ---- | ---- |
| SELL  | 769 Put  | 769    | −0.30 | 1.12 | 1.16 |
| BUY   | 764 Put  | 764    | −0.18 | 0.37 | 0.39 |

```text
           BUY 764P          SELL 769P        SPY
              ↓                  ↓             ↓
-----|--------|------------------|-------------|----->
    760      764                769          772.15

      max loss zone  |  partial loss  |   full profit zone
       (below 764)   |  (764 – 768.27)|   (above 769)
```

### Step 2: Credit, risk and size

```text
Credit (conservative) = short bid − long ask
                      = 1.12 − 0.39
                      = $0.73 per share  →  $73 per spread

Minimum credit check  = 0.73 ≥ 0.50 (10% of $5)  ✓

Max loss per spread   = (width − credit) × 100
                      = (5.00 − 0.73) × 100
                      = $427

Contracts             = floor(999.97 / 427) = floor(2.34) = 2

Max profit (total)    = 0.73 × 100 × 2 = $146
Max loss   (total)    = 427 × 2        = $854   (≤ $999.97 budget ✓)
Breakeven at expiry   = short strike − credit = 769 − 0.73 = $768.27
```

The order is a single multi-leg limit order for 2 spreads with `limit_price = -0.73`
(negative because it is a credit).

### Step 3: Profit and loss at expiry

Assumptions for the cost lines:

* **Fees:** Alpaca charges no commission on options, but small regulatory and clearing
  fees apply per contract. We assume **$0.05 per contract per leg**, so
  2 spreads × 2 legs = **$0.20 to open** and another $0.20 if the spread has to be
  closed. Check Alpaca's current fee schedule for exact values.
* **Tax:** SPY options are equity options. They do **not** get the 60/40 treatment that
  index options like SPX do, so a 1-day trade is a **short-term capital gain**, taxed as
  ordinary income. We assume a **24%** US federal rate and ignore state tax.
  A loss offsets other capital gains, so at 24% it saves 24% of the loss in tax.
* Paper trading has no real fees or tax. These lines show what the same trade would
  cost with real money.

| SPY at expiry          | Short 769P worth | Gross PnL                         | Fees   | Pre-tax PnL | Tax (24%) | After-tax PnL |
| ---------------------- | ---------------- | --------------------------------- | ------ | ----------- | --------- | ------------- |
| $775.00 (above 769)    | 0.00             | +0.73 × 100 × 2 = **+$146.00**    | −$0.20 | +$145.80    | −$34.99   | **+$110.81**  |
| $768.27 (breakeven)    | 0.73             | (0.73 − 0.73) × 200 = **$0.00**   | −$0.40 | −$0.40      | +$0.10    | **−$0.30**    |
| $766.00 (between)      | 3.00             | (0.73 − 3.00) × 200 = **−$454.00** | −$0.40 | −$454.40    | +$109.06  | **−$345.34**  |
| $760.00 (below 764)    | 9.00 (long 4.00) | (0.73 − 5.00) × 200 = **−$854.00** | −$0.40 | −$854.40    | +$205.06  | **−$649.34**  |

How to read a row, for example **$766.00**:

```text
Short 769P intrinsic value = 769 − 766 = $3.00
Long  764P                 = worthless (766 > 764)
Loss per share             = 3.00 − 0.73 credit = $2.27
Gross PnL                  = −2.27 × 100 × 2 = −$454.00
Fees                       = $0.20 open + $0.20 close = −$0.40
Pre-tax PnL                = −$454.40
Tax effect                 = 454.40 × 24% = $109.06 saved (against other gains)
After-tax PnL              = −454.40 + 109.06 = −$345.34
```

> SPY doesn't need to rally. The trade keeps the full credit as long as SPY stays above **769**.

Note the asymmetry: the best case is **+$146**, the worst case is **−$854**. In exchange, a
0.30-delta short put finishes out of the money roughly 70% of the time.

---

## 6. Example: Bearish (Call Credit Spread)

Same starting point: SPY = $772.15, 1DTE, a $999.97 risk budget.

### Step 1: Pick the legs

The agent is **bearish**, so it sells a call above spot and buys a cheaper call $5 further up as protection.

| Leg   | Contract | Strike | Delta | Bid  | Ask  |
| ----- | -------- | ------ | ----- | ---- | ---- |
| SELL  | 776 Call | 776    | 0.30  | 0.98 | 1.02 |
| BUY   | 781 Call | 781    | 0.15  | 0.30 | 0.33 |

```text
   SPY             SELL 776C           BUY 781C
    ↓                  ↓                  ↓
----|------------------|------------------|--------|---->
 772.15               776                781      785

   full profit zone  |  partial loss  |  max loss zone
     (below 776)     | (776 – 781)    |   (above 781)
```

### Step 2: Credit, risk and size

```text
Credit (conservative) = short bid − long ask
                      = 0.98 − 0.33
                      = $0.65 per share  →  $65 per spread

Minimum credit check  = 0.65 ≥ 0.50  ✓

Max loss per spread   = (5.00 − 0.65) × 100 = $435

Contracts             = floor(999.97 / 435) = floor(2.30) = 2

Max profit (total)    = 0.65 × 100 × 2 = $130
Max loss   (total)    = 435 × 2        = $870   (≤ $999.97 budget ✓)
Breakeven at expiry   = short strike + credit = 776 + 0.65 = $776.65
```

### Step 3: Profit and loss at expiry

Same fee and tax assumptions as the bullish example.

| SPY at expiry          | Short 776C worth  | Gross PnL                          | Fees   | Pre-tax PnL | Tax (24%) | After-tax PnL |
| ---------------------- | ----------------- | ---------------------------------- | ------ | ----------- | --------- | ------------- |
| $770.00 (below 776)    | 0.00              | +0.65 × 100 × 2 = **+$130.00**     | −$0.20 | +$129.80    | −$31.15   | **+$98.65**   |
| $776.65 (breakeven)    | 0.65              | (0.65 − 0.65) × 200 = **$0.00**    | −$0.40 | −$0.40      | +$0.10    | **−$0.30**    |
| $778.00 (between)      | 2.00              | (0.65 − 2.00) × 200 = **−$270.00** | −$0.40 | −$270.40    | +$64.90   | **−$205.50**  |
| $785.00 (above 781)    | 9.00 (long 4.00)  | (0.65 − 5.00) × 200 = **−$870.00** | −$0.40 | −$870.40    | +$208.90  | **−$661.50**  |

> SPY doesn't need to crash. The trade keeps the full credit as long as SPY stays below **776**.

**Important:** `BUY 775C + SELL 780C` would be a bullish call debit spread, not a bearish
call credit spread. For a bearish credit spread the call you **sell** is always the one
**closer** to spot.

### What happens to an in-the-money spread

If SPY finishes between the strikes, the short leg is in the money and the long leg is
not, so the short leg can be **assigned**. For the bullish example, that means buying
200 SPY shares at $769, about $153,800, far more than the account's risk budget.
The agent does not close positions yet, so in practice a threatened spread should be closed
by hand before the market closes on expiry day (see `alpaca position close` in section 10).
The "between" rows above assume it is closed at intrinsic value.

---

## 7. Strike Selection

The strategy intentionally keeps strike selection simple.

### Expiration

Trade the next expiry after today:

```text
1 DTE
```

(DTE = days to expiration)

Why not 0DTE? Alpaca returns no greeks and no implied volatility for same-day
contracts on any feed, so the 0.30-delta rule below would have nothing to work with.
At 1DTE both are populated on the free indicative feed.

### Short Strike

Target approximately:

```text
0.30 delta
```

If an exact 0.30 delta contract is unavailable, choose the contract with the closest delta.

### Long Strike

Use a fixed:

```text
$5 wider spread
```

For example:

```text
Bullish:
SELL 769P
BUY  764P

Bearish:
SELL 776C
BUY  781C
```

This keeps the strategy easy to understand and avoids over-optimizing the strike selection.

---

## 8. Risk Management

The position size is based on **1% of current account equity**.

For example:

```text
Account equity = $100,000

Risk budget = 1%
            = $1,000
```

For a $5-wide spread, the maximum loss per spread is:

```text
Spread width
- Net credit
----------------
Maximum loss
```

If:

```text
Width = $5
Credit = $1
```

then:

```text
Maximum loss = $5 - $1
             = $4/share
             = $400 per spread
```

With a $1,000 risk budget, the system can size the position so that the total theoretical maximum loss does not exceed that budget.

### Conservative Credit Estimate

For risk calculations we use:

```text
Short bid - Long ask
```

rather than the midpoint.

This gives a more conservative estimate of the credit and therefore slightly overestimates the potential loss.

The **mid price** can still be used later when determining the actual limit order price.

---

## 9. Minimum Credit

A spread should not be traded if the premium received is too small relative to the risk.

The current rule is:

```text
Minimum credit = 10% of spread width
```

For a $5-wide spread:

```text
$5 × 10% = $0.50
```

Therefore:

```text
Credit < $0.50 → reject trade
Credit ≥ $0.50 → eligible
```

This prevents taking trades where the potential reward is tiny compared with the defined risk.

---

## 10. Alpaca CLI Cheat Sheet

The `alpaca` CLI is the quickest way to check by hand
what the agent sees and does. These are the commands that matter for this strategy.
Contract symbols follow the OCC format: `SPY261012P00769000` = SPY, 2026-10-12, **P**ut, strike 769.000.

### Setup and sanity checks

```bash
alpaca profile login        # authenticate (use the paper-trading keys)
alpaca doctor               # check configuration and connectivity
alpaca clock                # is the market open? next open / close
alpaca calendar --start 2026-10-01 --end 2026-10-31   # trading days (find the 1DTE expiry)
alpaca account get          # equity, buying power (the 1% risk budget comes from equity)
```

### Market data (the features)

```bash
# SPY spot. The agent uses IEX, which can be one-sided outside regular hours
alpaca data latest-quote --symbol SPY --feed iex

# Daily bars for SMA20 / SMA50 / RV20
alpaca data bars --symbol SPY --timeframe 1Day --start 2026-06-01

# Option chain with greeks and IV for the next expiry.
# Always pass --feed indicative: the CLI defaults to opra, which this plan cannot use.
alpaca data option chain --underlying-symbol SPY --expiration-date 2026-10-12 \
  --type put --strike-price-gte 750 --strike-price-lte 775 --feed indicative

# Quotes for the two legs of a specific spread
alpaca data option snapshot --symbols SPY261012P00769000,SPY261012P00764000 --feed indicative
```

At 0DTE the CLI shows `greeks: {delta: 0, ...}`. Those zeros are made up by the CLI:
the API returns no greeks at all for same-day contracts, which is why the agent trades 1DTE.

### Orders

```bash
# Preview the exact multi-leg order the agent places (--dry-run prints it and submits nothing)
alpaca order submit --order-class mleg --qty 2 --type limit --limit-price -0.73 \
  --time-in-force day --dry-run --legs '[
    {"symbol":"SPY261012P00769000","ratio_qty":"1","side":"sell","position_intent":"sell_to_open"},
    {"symbol":"SPY261012P00764000","ratio_qty":"1","side":"buy","position_intent":"buy_to_open"}]'

alpaca order list --status all --nested --limit 10   # recent orders, legs grouped under each spread
alpaca order get --order-id <id> --nested            # one order and its fill
alpaca order cancel --order-id <id>                  # pull an unfilled order
```

The limit price is **negative** for a credit. A positive price on a credit spread is
read as a debit: it is not rejected, and it can fill with you paying instead of receiving.

### Positions and results

```bash
alpaca position list                                   # open legs, market value, unrealized PnL
alpaca position close --symbol-or-asset-id SPY261012P00769000   # close the short leg first...
alpaca position close --symbol-or-asset-id SPY261012P00764000   # ...then the long leg
alpaca account activity list-by-type --activity-type FILL --after 2026-10-01   # fills, for PnL
alpaca account portfolio                               # equity over time
```

Closing the short leg first means you are never left holding an uncovered short option.

### Useful flags

```bash
--jq '<expr>'   # filter JSON output, e.g. alpaca account get --jq '.equity'
--csv           # CSV output, handy for spreadsheets
--schema        # show the response schema without calling the API
```

---

## Strategy Summary

```text
                  ┌─────────────┐
                  │ Market Data │
                  └──────┬──────┘
                         ↓
              ┌────────────────────┐
              │ SMA20 / SMA50      │
              │ RV20 / ATM IV      │
              │ SPY Spot           │
              └─────────┬──────────┘
                        ↓
                 ┌─────────────┐
                 │     LLM     │
                 └──────┬──────┘
                        ↓
             ┌──────────┼──────────┐
             ↓          ↓          ↓
         BULLISH     BEARISH    NEUTRAL
             ↓          ↓          ↓
       Put Credit   Call Credit   No Trade
          Spread       Spread
             ↓          ↓
          0.30 Δ     0.30 Δ
          short       short
             ↓          ↓
            $5-wide spreads
                  ↓
             1% NAV risk
                  ↓
          Multi-leg limit order
```

---

## Project Philosophy

The project intentionally favors **simplicity and determinism** over sophistication.

The interesting part is not trying to predict the market perfectly. It is understanding the entire pipeline:

> **Market data → features → market stance → options structure → risk → execution**

The LLM provides a small amount of reasoning at the beginning, while the actual options construction and risk management remain rule-based and reproducible.

This makes the project useful as a learning exercise for both **AI agents and options trading fundamentals**.
