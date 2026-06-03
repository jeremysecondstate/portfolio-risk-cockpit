from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import tempfile
import unittest

from app.storage.trade_memory_store import (
    TradeMemoryStore,
    compare_then_now,
    generate_snapshot_id,
    match_snapshot_to_order,
    normalize_snapshot,
    render_trade_thesis_html,
)


class TradeMemoryStoreTests(unittest.TestCase):
    def test_snapshot_id_creation_is_stable_shape(self) -> None:
        created = datetime(2026, 6, 3, 14, 5, 6, tzinfo=timezone.utc)

        snapshot_id = generate_snapshot_id("CRSP 260605C00055000", created, suffix="abc123")

        self.assertEqual(snapshot_id, "20260603-140506-CRSP-260605C00055000-abc123")

    def test_saving_and_loading_jsonl_snapshots(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            store = TradeMemoryStore(root / "snapshots.jsonl", root / "reports")

            saved = store.save_snapshot(
                {
                    "symbol": "HPE",
                    "order_ticket": {"side": "BUY", "quantity": 3, "limit_price": 55.25},
                    "research_report_text": "Original thesis\nRisk read: normal.",
                }
            )
            loaded = store.load_snapshots()

            self.assertEqual(len(loaded), 1)
            self.assertEqual(loaded[0]["snapshot_id"], saved["snapshot_id"])
            self.assertTrue(Path(saved["report_path"]).exists())

    def test_matching_by_order_id_or_location(self) -> None:
        snapshots = [
            {
                "snapshot_id": "snap-1",
                "symbol": "HPE",
                "order_id": "12345",
                "order_location": "https://api.schwabapi.com/trader/v1/accounts/redacted/orders/12345",
            }
        ]

        matched_by_id = match_snapshot_to_order({"orderId": "12345"}, snapshots)
        matched_by_location = match_snapshot_to_order({"location": "https://api.schwabapi.com/trader/v1/accounts/redacted/orders/12345"}, snapshots)

        self.assertEqual(matched_by_id["snapshot_id"], "snap-1")
        self.assertEqual(matched_by_location["snapshot_id"], "snap-1")

    def test_fallback_matching_by_ticket_and_nearby_timestamp(self) -> None:
        snapshots = [
            {
                "snapshot_id": "snap-2",
                "created_at": "2026-06-03T14:00:00+00:00",
                "symbol": "HPE",
                "order_ticket": {"side": "BUY", "quantity": 10, "limit_price": 55.25},
            }
        ]
        order = {
            "enteredTime": "2026-06-03T14:02:00+00:00",
            "price": 55.25,
            "quantity": 10,
            "orderLegCollection": [{"instruction": "BUY", "instrument": {"symbol": "HPE"}}],
        }

        matched = match_snapshot_to_order(order, snapshots)

        self.assertIsNotNone(matched)
        assert matched is not None
        self.assertEqual(matched["snapshot_id"], "snap-2")

    def test_occ_option_parsing_in_trade_memory_normalization(self) -> None:
        snapshot = normalize_snapshot({"symbol": "CRSP 260605C00055000", "order_ticket": {"side": "BUY"}})

        self.assertEqual(snapshot["instrument_type"], "option")
        self.assertEqual(snapshot["underlying_symbol"], "CRSP")
        self.assertEqual(snapshot["option_details"]["expiration"], "2026-06-05")
        self.assertEqual(snapshot["option_details"]["call_put"], "call")
        self.assertEqual(snapshot["option_details"]["strike"], 55.0)

    def test_html_report_generation(self) -> None:
        html = render_trade_thesis_html(
            {
                "symbol": "HPE",
                "order_ticket": {"side": "BUY", "quantity": 1, "limit_price": 55.0},
                "research_report_text": "Original thesis\nEvidence posture: Bullish.",
            }
        )

        self.assertIn("Trade Thesis Snapshot", html)
        self.assertIn("Trade identity", html)
        self.assertIn("This is what the cockpit saw", html)

    def test_missing_analysis_does_not_crash_and_marks_missing(self) -> None:
        snapshot = normalize_snapshot({"symbol": "HPE", "order_ticket": {"side": "BUY"}})

        self.assertEqual(snapshot["thesis_status"], "missing_analysis")
        self.assertIn("No research thesis", snapshot["plain_english_summary"])

    def test_no_secret_storage(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            store = TradeMemoryStore(root / "snapshots.jsonl", root / "reports")

            store.save_snapshot(
                {
                    "symbol": "HPE",
                    "raw_order_json": {
                        "accountNumber": "123456789",
                        "client_secret": "dont-store-me",
                        "headers": {"Authorization": "Bearer abc.def.secret"},
                        "orderLegCollection": [{"instrument": {"symbol": "HPE"}}],
                    },
                    "research_report_text": "SCHWAB_CLIENT_SECRET=dont-store-me thesis text",
                }
            )
            raw = (root / "snapshots.jsonl").read_text(encoding="utf-8")

            self.assertNotIn("123456789", raw)
            self.assertNotIn("dont-store-me", raw)
            self.assertNotIn("abc.def.secret", raw)
            self.assertIn("[REDACTED]", raw)

    def test_then_vs_now_handles_missing_current_data(self) -> None:
        comparison = compare_then_now({"symbol": "HPE"}, None)

        self.assertFalse(comparison["available"])
        self.assertIn("Current comparison unavailable", comparison["summary"])


if __name__ == "__main__":
    unittest.main()
