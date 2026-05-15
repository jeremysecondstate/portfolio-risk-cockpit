# Portfolio Risk Cockpit

A local desktop cockpit for Schwab-backed portfolio review, risk previews, and guarded trading workflows.

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
