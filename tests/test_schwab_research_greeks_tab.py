from __future__ import annotations

import tkinter as tk
from tkinter import ttk
import unittest
from types import SimpleNamespace

from app.analytics.options_greeks import build_greek_summary
from app.ui.schwab_research_workspace_extension import _build_research_right_panel, _greek_metric_cards, _greeks_popout_text, _risk_scenario_popout_text


class SchwabResearchGreeksTabTests(unittest.TestCase):
    def test_greeks_tab_is_inserted_before_earnings(self) -> None:
        try:
            root = tk.Tk()
        except tk.TclError as exc:
            self.skipTest(f"Tk unavailable: {exc}")
        try:
            root.withdraw()
            root.schwab_research_max_risk_var = tk.StringVar(root, value="Run analysis")
            root.schwab_research_scenario_basis_var = tk.StringVar(root, value="Scenario moves pending.")
            parent = ttk.Frame(root)
            parent.grid(row=0, column=0, sticky="nsew")

            _build_research_right_panel(root, parent)
            notebook = root.schwab_research_tabs
            labels = [notebook.tab(tab_id, "text") for tab_id in notebook.tabs()]

            self.assertEqual(
                labels,
                ["Overview", "Evidence Desk", "Technicals", "Risk Scenarios", "Options Strategy", "Greeks", "Earnings / News", "Fundamentals", "Macro Context"],
            )
            self.assertTrue(hasattr(root, "schwab_research_greeks_frame"))
        finally:
            root.destroy()

    def test_risk_scenario_popout_excludes_greeks_decision(self) -> None:
        payload = SimpleNamespace(symbol="LHX")
        risk_plan = SimpleNamespace(
            recommendation="Consider starter",
            reason="Setup is mixed.",
            confirmation="Confirmation means a move above $315.29.",
            risk_line="Risk worsens below $314.75.",
        )
        text = _risk_scenario_popout_text(
            payload,
            risk_plan,
            ["Starter stock look: 7 shares."],
            "Recommended move:\n- Existing detail stays here.",
        )

        self.assertNotIn("Decision from the Greeks", text)
        self.assertNotIn("Greek approximation", text)
        self.assertIn("Original / detailed readout", text)

    def test_greeks_popout_includes_decision_section(self) -> None:
        summary = build_greek_summary(
            [
                {
                    "underlying": "LHX",
                    "expiration_label": "Jun 05 2026 (4d)",
                    "dte": 4,
                    "strike": 315.0,
                    "call": {"symbol": "LHX_CALL_315", "mark": 4.2, "impliedVolatility": 0.31, "delta": 0.491, "gamma": 0.034, "theta": -0.310, "vega": 0.172, "rho": 0.026},
                }
            ],
            315.18,
            selected_contract_symbol="LHX_CALL_315",
        )

        text = _greeks_popout_text(None, summary)

        self.assertIn("Option Sensitivities", text)
        self.assertIn("Decision from the Greeks", text)
        self.assertIn("Expected P/L from Greek approximation", text)

    def test_sentinel_values_do_not_appear_in_greek_cards_or_popout(self) -> None:
        summary = build_greek_summary(
            [
                {
                    "underlying": "LHX",
                    "expiration_label": "Jun 05 2026 (4d)",
                    "dte": 4,
                    "strike": 315.0,
                    "call": {"symbol": "LHX_CALL_315", "delta": -999, "gamma": "-999.000", "theta": -999.0, "vega": -999, "rho": -999},
                }
            ],
            None,
            selected_contract_symbol="LHX_CALL_315",
        )
        assert summary.selected is not None

        card_text = "\n".join(f"{card.label} {card.why}" for card in _greek_metric_cards(summary.selected))
        popout_text = _greeks_popout_text(None, summary)

        self.assertNotIn("-999", card_text)
        self.assertIn("--", card_text)
        self.assertIn("Unavailable for the active contract", card_text)
        self.assertNotIn("-999", popout_text)
        self.assertNotIn("$99,900", popout_text)
        self.assertIn("Greeks decision unavailable for the active contract", popout_text)


if __name__ == "__main__":
    unittest.main()
