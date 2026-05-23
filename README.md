# Portfolio Risk Cockpit

A local desktop cockpit for Schwab-backed portfolio review, risk previews, guarded trading workflows, and read-only crypto portfolio sync.

## Windows desktop shortcut

To create a desktop shortcut that launches the cockpit:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\create_desktop_shortcut.ps1
```

This creates **Portfolio Risk Cockpit.lnk** on your Desktop. Double-clicking it runs:

```powershell
python -m app.main
```

The launcher sets the working directory to the repository root so local `.env`, `data/`, and Schwab token-cache files resolve correctly.

If the repository has a `.venv`, the launcher uses `.venv\Scripts\pythonw.exe` first so the cockpit opens without an extra console window.

## Hyperliquid read-only sync

The cockpit can now merge a Hyperliquid account snapshot into the left-side portfolio/risk table.

Use the **Sync Hyperliquid** button in the trading cockpit and enter your Hyperliquid master or sub-account wallet address.

Important: this is read-only and uses Hyperliquid's public info endpoint. It does **not** need an API wallet/private key, and you should not paste the API/agent wallet address for portfolio sync.

Optional `.env` setting:

```env
HYPERLIQUID_USER_ADDRESS=0xYourMasterOrSubAccountAddress
```

When the env var is set, the Sync Hyperliquid button uses it directly. Otherwise, the app prompts for the address.

Loaded Hyperliquid rows are prefixed with `HL:` so they stay visually separate from Schwab/stock rows, for example `HL:BTC-PERP`, `HL:ETH-PERP-SHORT`, or `HL:HYPE-SPOT`.
