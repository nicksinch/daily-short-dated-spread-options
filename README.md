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

## 5. Example: Bullish

Suppose:

```text
SPY = $773
```

The agent is bullish.

It could construct:

```text
SELL  780 Put
BUY   775 Put
```

The short put is closer to the current price and the long put provides protection below it.

Visually:

```text
        SPY
         ↓
----|----|----|----|----|---->
   760  765  770  773  780

                   SELL 780P
                       ↓
              BUY 775P
                  ↓
```

The idea is:

> SPY doesn't need to go up dramatically. We mainly want it to stay above the short put strike.

---

## 6. Example: Bearish

Suppose:

```text
SPY = $773
```

The agent is bearish.

It could construct:

```text
SELL  780 Call
BUY   785 Call
```

Visually:

```text
----|----|----|----|----|----|---->
   770  773  775  780  785

                    SELL 780C
                        ↓
                         BUY 785C
                             ↓
```

The idea is:

> SPY doesn't need to crash. We mainly want it to stay below the short call strike.

**Important:** `BUY 775C + SELL 780C` would be a bullish call debit spread, not a bearish call credit spread.

---

## 7. Strike Selection

The strategy intentionally keeps strike selection simple.

### Expiration

Target approximately:

```text
30 DTE
```

(DTE = days to expiration)

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
SELL 780P
BUY  775P

Bearish:
SELL 780C
BUY  785C
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
