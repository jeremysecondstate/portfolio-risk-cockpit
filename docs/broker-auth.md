# Broker authentication notes

## Current decision

The app remains **paper mode by default**. Live trading must be added in stages:

1. Paper trading only.
2. Read-only live account connection.
3. Live order preview.
4. Live order submission with hard confirmations.

## Robinhood status

Robinhood has an official **Crypto Trading API** for eligible Robinhood Crypto customers. That API supports crypto market data, crypto account/holding/order reads, and crypto order placement.

Robinhood's own third-party connections help page says it does not allow trading APIs to be linked to a Robinhood account without written authorization, and it does not allow third-party applications to control or take action on the Robinhood app. For crypto API details, Robinhood directs users to the Robinhood Crypto Trading API.

Practical interpretation for this project:

- Robinhood crypto integration may be possible through official API credentials.
- Robinhood equities/options trading should be treated as unsupported unless Robinhood provides written authorization or an official documented API for that account type.
- Do not use username/password scraping or unofficial app-control automation for live trading.

## Credential handling rules

- Never commit real API keys, private keys, account IDs, passwords, or MFA backup codes.
- Use `.env` for local development secrets.
- Keep `.env` ignored by git.
- Prefer read-only credentials first.
- Use separate credentials for paper/sandbox and live modes whenever the broker supports that.
- Add a kill switch before any live order-submission code.

## Robinhood Crypto setup checklist

When ready to test official crypto API access:

1. Sign in to Robinhood on desktop web.
2. Go to crypto account settings.
3. Find the API trading / API credentials area.
4. Add a key.
5. Enable only the minimum actions needed.
6. Start with read-only actions.
7. Store credentials locally in `.env`; do not commit them.

## Equities/options integration

For stock and ETF automation, prefer brokers with official, documented trading APIs such as Alpaca, Interactive Brokers, Tradier, or other supported providers.

Before adding any live equities broker adapter, the app must include:

- Paper mode default.
- Live trading disabled by default.
- Explicit config opt-in.
- Typed confirmation on every live order.
- Final modal confirmation.
- Cash-only/margin-off checks.
- Max order size checks.
- Max position percent checks.
- Audit logging.
