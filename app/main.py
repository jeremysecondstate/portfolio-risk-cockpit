from __future__ import annotations

from app.ui.trading_cockpit import SchwabTradingCockpitApp


def main() -> None:
    app = SchwabTradingCockpitApp()
    app.mainloop()


if __name__ == "__main__":
    main()
