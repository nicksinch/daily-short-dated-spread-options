# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Current state

This project is an empty PyCharm scaffold, not yet a working codebase:

- `main.py` is the untouched PyCharm `print_hi` template.
- `.venv/` is a bare virtualenv (CPython 3.14.5, `pip` only — no third-party packages installed).
- There is no README, no dependency manifest, no test suite, and **no git repository** (`git init` has not been run).

Do not describe or reason about architecture that does not exist yet. When the first real
code lands, replace this section with the actual structure.

## Environment

The interpreter is the in-project virtualenv; PyCharm registers it as SDK
"Python 3.14 (daily-short-dated-spread-spy)". Activate it before running anything:

```bash
source .venv/bin/activate
python main.py
```

Install dependencies into that venv (`.venv/bin/pip install ...`) rather than system Python,
and record them in a manifest (`requirements.txt` or `pyproject.toml`) — one does not exist yet,
so creating it is part of the first real change.

There is no lint, format, or test command configured. PyCharm's Black integration is enabled
in `.idea/misc.xml`, but `black` is not installed in the venv.

## Domain context

The project name — "daily short-dated spread SPY" — points at a short-dated (0DTE/weekly)
option spread strategy on SPY. An **Interactive Brokers (IBKR) MCP server** is connected in
this session, exposing account positions/orders/trades, option chains
(`get_option_parameters`, `get_option_data`), combo identifiers for multi-leg spreads
(`get_combo_identifier`), price history, and watchlists. Prefer those tools for live account
and market data over writing ad-hoc broker API clients.

Anything touching real orders is live-money surface: `create_order_instruction` and the alert
tools mutate the user's brokerage account. Confirm with the user before calling them, and never
place or modify an order as a side effect of research or backtesting work.

## Constraints
- always prefer simple code => adopt simplicity, don't overengineer and do not add unnecessary features/complications unless asked to do so.
