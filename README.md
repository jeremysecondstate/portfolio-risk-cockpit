# Portfolio Risk Cockpit

A guarded desktop cockpit for **manual Robinhood order planning**, portfolio risk checks, position sizing, stop planning, and paper-mode testing.

## Current status

This version is intentionally safe:

- Runs in **Robinhood Manual Mode / Paper Mode** by default.
- Does **not** place Robinhood trades.
- Uses a local portfolio snapshot file when available.
- Blocks orders that fail basic risk rules.
- Requires typing `CONFIRM` before a paper order can be submitted.
- Generates a copy/paste manual Robinhood order checklist.
- Writes every submitted paper order to a local audit log.

Live broker trading is intentionally out of scope for Robinhood because Robinhood does not offer a normal public stock/ETF trading API for this workflow.

## Quick start in PyCharm

1. Open this project in PyCharm.
2. Make sure your interpreter is the project virtual environment.
3. Pull the latest code from GitHub.
4. Run:

```bash
python -m app.main
```

No third-party packages are required for v0.2.

## Portfolio snapshot workflow

The app first looks for:

```text
data/portfolio_snapshot.csv
```

That file is intentionally ignored by git so private account data does not get committed.

A template is available here:

```text
templates/portfolio_snapshot.sample.csv
```

To use your own local snapshot:

1. Copy `templates/portfolio_snapshot.sample.csv` to `data/portfolio_snapshot.csv`.
2. Edit the cash and position rows.
3. Open the app.
4. Click **Reload Snapshot**.

Example format:

```csv
type,symbol,quantity,average_cost,last_price,notes
cash,CASH,,,116838.39,cash available for planning
position,AMD,3,323.89,450.45,
position,NVDA,1.01,213.41,218.22,
```

## Manual Robinhood workflow

Use the app to decide:

- ticker
- side: buy or sell
- order type: market, limit, stop, or stop-limit
- quantity
- estimated/reference price
- limit price
- stop price
- position size
- cash impact
- concentration impact

Then click **Manual Checklist** and manually enter the order in Robinhood / Robinhood Legend.

## Project goals

- Keep margin disabled / cash-only by default.
- Make order entry deliberate and hard to fat-finger.
- Show position sizing and portfolio impact before any order.
- Support market, limit, stop, and stop-limit order models.
- Generate safe manual order checklists for Robinhood.
- Preserve paper trading as a safe simulator.
- Add official broker integrations later only after read-only testing and explicit opt-in.

## Safety principles

This app is not financial advice and does not guarantee execution quality or prevent losses. Stop orders can fill below the stop price in fast markets, and stop-limit orders may not fill at all.

The app should not:

- store brokerage passwords
- automate Robinhood browser clicks
- scrape Robinhood login/MFA flows
- place live Robinhood stock trades through unofficial libraries

Before any future live broker integration, the app should require:

- Paper mode testing.
- Broker read-only mode testing.
- Explicit live-trading opt-in.
- Typed confirmation for each live order.
- Final modal confirmation.
- Audit logging.
- Maximum order-size and max-position-size checks.
- Cash-only / margin-off checks.
