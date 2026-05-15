from __future__ import annotations

from app.ui.polished_theme import install_polished_cockpit_theme
from app.ui.trading_cockpit import SchwabTradingCockpitApp


def main() -> None:
    install_polished_cockpit_theme(SchwabTradingCockpitApp)
    app = SchwabTradingCockpitApp()
    app.mainloop()


if __name__ == "__main__":
    main()
