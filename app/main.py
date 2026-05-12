from __future__ import annotations

from app.ui.dashboard import PortfolioRiskCockpitApp


def main() -> None:
    app = PortfolioRiskCockpitApp()
    app.mainloop()


if __name__ == "__main__":
    main()
