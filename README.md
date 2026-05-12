# Portfolio Risk Cockpit

A guarded desktop trading cockpit for planning orders, checking risk, and eventually submitting broker orders with hard confirmations.

## Current status

This first version is intentionally safe:

- Runs in **paper trading mode** by default.
- Uses a local mock account with cash and positions.
- Blocks orders that fail basic risk rules.
- Requires typing `CONFIRM` before an order can be submitted.
- Writes every submitted paper order to a local audit log.

Live broker trading should only be enabled after we add and test an official broker integration.

## Quick start in PyCharm

1. Open this project in PyCharm.
2. Make sure your interpreter is the project virtual environment.
3. Run:

```bash
python -m app.main
```

No third-party packages are required for v0.1.

## Project goals

- Keep margin disabled / cash-only by default.
- Make order entry deliberate and hard to fat-finger.
- Show position sizing and portfolio impact before any order.
- Support market, limit, stop, and stop-limit order models.
- Start with paper trading, then add live broker support behind explicit safeguards.

## Safety principles

This app is not financial advice and does not guarantee execution quality or prevent losses. Stop orders can fill below the stop price in fast markets, and stop-limit orders may not fill at all.

Before any live trading integration, the app should require:

- Paper mode testing.
- Broker read-only mode testing.
- Explicit live-trading opt-in.
- Typed confirmation for each live order.
- Audit logging.
- Maximum order-size and max-position-size checks.
