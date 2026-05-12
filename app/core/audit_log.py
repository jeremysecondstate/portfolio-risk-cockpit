from __future__ import annotations

import csv
from pathlib import Path

from app.core.order_models import SubmittedOrder


class AuditLog:
    def __init__(self, path: str | Path = "data/audit_log.csv") -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._ensure_header()

    def _ensure_header(self) -> None:
        if self.path.exists():
            return
        with self.path.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.writer(handle)
            writer.writerow(
                [
                    "submitted_at",
                    "order_id",
                    "mode",
                    "status",
                    "symbol",
                    "side",
                    "order_type",
                    "quantity",
                    "estimated_price",
                    "estimated_notional",
                    "limit_price",
                    "stop_price",
                    "time_in_force",
                ]
            )

    def append(self, submitted_order: SubmittedOrder) -> None:
        order = submitted_order.order
        with self.path.open("a", newline="", encoding="utf-8") as handle:
            writer = csv.writer(handle)
            writer.writerow(
                [
                    submitted_order.submitted_at.isoformat(),
                    submitted_order.id,
                    submitted_order.broker_mode,
                    submitted_order.status,
                    order.symbol,
                    order.side.value,
                    order.order_type.value,
                    order.quantity,
                    order.estimated_price,
                    order.estimated_notional,
                    order.limit_price or "",
                    order.stop_price or "",
                    order.time_in_force.value,
                ]
            )
