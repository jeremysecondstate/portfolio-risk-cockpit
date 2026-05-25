from __future__ import annotations

from app.ui.registry import install_ui_extensions
from app.ui.trading_cockpit import SchwabTradingCockpitApp


def main() -> None:
    install_ui_extensions(SchwabTradingCockpitApp)
    app = SchwabTradingCockpitApp()
    app.mainloop()


if __name__ == "__main__":
    main()
