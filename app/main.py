from __future__ import annotations

from app.data.sec_cache_layout import install_sec_cache_layout
from app.ui.registry import install_ui_extensions
from app.ui.trading_cockpit import SchwabTradingCockpitApp


def main() -> None:
    install_sec_cache_layout()
    install_ui_extensions(SchwabTradingCockpitApp)
    app = SchwabTradingCockpitApp()
    app.mainloop()


if __name__ == "__main__":
    main()
