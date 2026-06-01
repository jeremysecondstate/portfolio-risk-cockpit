from __future__ import annotations

import tkinter as tk
from tkinter import ttk
import unittest
from types import SimpleNamespace

from app.ui.schwab_research_workspace_extension import _build_research_right_panel, _risk_scenario_popout_text


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
                ["Overview", "Technicals", "Risk Scenarios", "Options Strategy", "Greeks", "Earnings / News", "Fundamentals", "Macro Context"],
            )
            self.assertTrue(hasattr(root, "schwab_research_greeks_frame"))
        finally:
            root.destroy()

    def test_risk_scenario_popout_includes_greeks_decision_before_detail(self) -> None:
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
            "Decision from the Greeks\n\nThe active contract is a short-dated speed trade.",
        )

        self.assertIn("Decision from the Greeks", text)
        self.assertIn("Original / detailed readout", text)
        self.assertLess(text.index("Decision from the Greeks"), text.index("Original / detailed readout"))


if __name__ == "__main__":
    unittest.main()
