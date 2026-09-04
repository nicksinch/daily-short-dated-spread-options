# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Current state

The data and signal layer for a daily SPY option-spread agent, trading in
Alpaca's paper environment.

- `config.py` — `FeatureConfig`, every tunable in one frozen dataclass.
- `data.py` — Alpaca REST access. Returns plain records; raises on HTTP and
  network errors, returns `None` for absent market data.
- `features.py` — pure arithmetic plus feature builders that attach
  timestamps and a data-quality `Status`. No I/O.
- `main.py --dry-run` — fetches, computes, prints, exits. Places no orders.
- `tests/` — pytest. Run with `.venv/bin/pytest`.

Credentials come from `ALPACA_API_KEY_ID` and `ALPACA_API_SECRET_KEY`.

## Environment

The interpreter is the in-project virtualenv; PyCharm registers it as SDK
"Python 3.14 (daily-short-dated-spread-spy)". Activate it before running anything:

```bash
source .venv/bin/activate
python main.py --dry-run
```

Install dependencies into that venv (`.venv/bin/pip install ...`) rather than system Python,
and record them in `requirements.txt`, which is pinned.

Tests run with `.venv/bin/pytest`. There is no lint or format command configured:
PyCharm's Black integration is enabled in `.idea/misc.xml`, but `black` is not
installed in the venv.

## Domain context

A daily defined-risk option spread on SPY. Order construction, strike
selection, sizing and submission are deliberately not implemented yet.

Two findings constrain the design; both are documented in
`docs/superpowers/specs/2026-09-03-data-signal-layer-design.md`:

- **Alpaca returns no greeks and no implied volatility for 0DTE contracts on
  any feed.** The strategy therefore targets 1DTE, where both are populated
  on the free indicative feed. Note that the `alpaca` CLI displays
  `greeks: {delta: 0, ...}` at 0DTE while the REST API omits the key
  entirely — those zeros are synthesised by the CLI, not returned by Alpaca.
- **Stock quotes come from IEX and can be one-sided.** A bid with a zero ask
  is normal outside regular hours; taking a naive mid produces a plausible
  but badly wrong price, so `spot_feature` rejects one-sided quotes.

## Constraints
- always prefer simple code => adopt simplicity, don't overengineer and do not add unnecessary features/complications unless asked to do so.
