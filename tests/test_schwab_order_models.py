from __future__ import annotations

import unittest

from app.core.order_models import TimeInForce, normalize_time_in_force, schwab_equity_session_duration


class SchwabOrderModelTests(unittest.TestCase):
    def test_schwab_time_in_force_accepts_legacy_lowercase_values(self) -> None:
        self.assertEqual(normalize_time_in_force("day"), TimeInForce.DAY)
        self.assertEqual(normalize_time_in_force("gtc"), TimeInForce.GTC)

    def test_schwab_time_in_force_maps_normal_sessions(self) -> None:
        self.assertEqual(schwab_equity_session_duration("Day"), ("NORMAL", "DAY"))
        self.assertEqual(schwab_equity_session_duration("GTC"), ("NORMAL", "GOOD_TILL_CANCEL"))

    def test_schwab_time_in_force_maps_extended_sessions(self) -> None:
        self.assertEqual(schwab_equity_session_duration("Day (EXT 13h)"), ("SEAMLESS", "DAY"))
        self.assertEqual(schwab_equity_session_duration("GTC (EXT 13h)"), ("SEAMLESS", "GOOD_TILL_CANCEL"))
        self.assertEqual(schwab_equity_session_duration("Day (EXT AM)"), ("AM", "DAY"))
        self.assertEqual(schwab_equity_session_duration("Day (EXT PM)"), ("PM", "DAY"))


if __name__ == "__main__":
    unittest.main()
