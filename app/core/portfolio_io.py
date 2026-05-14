from __future__ import annotations

import csv
from pathlib import Path

from app.core.portfolio import Portfolio, Position, current_foundation_portfolio

PROJECT_ROOT = Path(__file__).resolve().parents[2]
SNAPSHOT_PATH = PROJECT_ROOT / "data" / "portfolio_snapshot.csv"
SAMPLE_PATH = PROJECT_ROOT / "templates" / "portfolio_snapshot.sample.csv"


def load_portfolio_snapshot(path: str | Path = SNAPSHOT_PATH) -> tuple[Portfolio, str]:
    """Load a local portfolio snapshot.

    The app intentionally uses a local CSV file instead of brokerage passwords
    or browser automation. If the snapshot does not exist yet, we return the
    seeded foundation portfolio and tell the caller what happened.
    """
    snapshot_path = Path(path)
    if not snapshot_path.exists():
        display_path = snapshot_path.relative_to(PROJECT_ROOT) if snapshot_path.is_absolute() else snapshot_path
        return current_foundation_portfolio(), f"Seeded foundation portfolio; no {display_path} found"

    cash = 0.0
    positions: dict[str, Position] = {}

    with snapshot_path.open("r", newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        required = {"type", "symbol", "quantity", "average_cost", "last_price"}
        if not required.issubset(set(reader.fieldnames or [])):
            missing = sorted(required - set(reader.fieldnames or []))
            raise ValueError(f"Portfolio snapshot is missing columns: {', '.join(missing)}")

        for row in reader:
            row_type = (row.get("type") or "").strip().lower()
            if row_type == "cash":
                cash = float(row.get("last_price") or row.get("quantity") or 0)
                continue

            if row_type != "position":
                continue

            symbol = (row.get("symbol") or "").strip().upper()
            if not symbol:
                continue

            quantity = float(row.get("quantity") or 0)
            if quantity <= 0:
                continue

            positions[symbol] = Position(
                symbol=symbol,
                quantity=quantity,
                average_cost=float(row.get("average_cost") or 0),
                last_price=float(row.get("last_price") or 0),
            )

    display_path = snapshot_path.relative_to(PROJECT_ROOT) if snapshot_path.is_absolute() else snapshot_path
    return Portfolio(cash=round(cash, 2), positions=positions), f"Loaded {display_path}"


def write_sample_snapshot(path: str | Path = SAMPLE_PATH) -> Path:
    """Write a sample snapshot template for manual editing/import."""
    sample_path = Path(path)
    sample_path.parent.mkdir(parents=True, exist_ok=True)
    portfolio = current_foundation_portfolio()

    with sample_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["type", "symbol", "quantity", "average_cost", "last_price", "notes"])
        writer.writerow(["cash", "CASH", "", "", f"{portfolio.cash:.2f}", "cash available for planning"])
        for symbol in sorted(portfolio.positions):
            position = portfolio.positions[symbol]
            writer.writerow(
                [
                    "position",
                    position.symbol,
                    f"{position.quantity:g}",
                    f"{position.average_cost:.4f}",
                    f"{position.last_price:.4f}",
                    "",
                ]
            )

    return sample_path
