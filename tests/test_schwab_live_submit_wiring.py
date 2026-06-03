from __future__ import annotations

from pathlib import Path
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from app.ui import account_sources_fix
from app.ui.schwab_mechanical_submit_extension import _submit_with_mechanical_checks
from app.ui.trading_cockpit import SchwabTradingCockpitApp


ROOT = Path(__file__).resolve().parents[1]


class SchwabLiveSubmitWiringTests(unittest.TestCase):
    def _source(self, path: str) -> str:
        return (ROOT / path).read_text(encoding="utf-8")

    def test_registry_preserves_current_schwab_submit_wrapper_order(self) -> None:
        source = self._source("app/ui/registry.py")

        mechanical_index = source.index("install_schwab_mechanical_submit_extension(app_cls)")
        memory_index = source.index("install_schwab_trade_memory_extension(app_cls)")
        self.assertLess(mechanical_index, memory_index)

    def test_schwab_tab_live_submit_uses_direct_schwab_handler(self) -> None:
        source = self._source("app/ui/options_lab_extension.py")

        self.assertIn(
            'text="LIVE Submit", command=schwab_action("submit_live_schwab_order_guarded")',
            source,
        )
        self.assertNotIn(
            'text="LIVE Submit", command=schwab_action("submit_selected_venue", "submit_live_schwab_order_guarded")',
            source,
        )

    def test_account_sources_rebuild_keeps_live_submit_direct(self) -> None:
        source = self._source("app/ui/account_sources_fix.py")

        self.assertIn(
            'text="LIVE Submit", command=lambda app=self: _run_schwab_workspace_action(app, "submit_live_schwab_order_guarded")',
            source,
        )
        self.assertNotIn(
            'text="LIVE Submit", command=schwab_action("submit_selected_venue", "submit_live_schwab_order_guarded")',
            source,
        )

    def test_account_sources_live_submit_button_invokes_direct_schwab_submit(self) -> None:
        captured_commands: dict[str, object] = {}
        routed_calls: list[tuple[object, tuple[str, ...]]] = []
        app = object()

        def capture_button(_parent: object, *, text: str, command: object, **_kwargs: object) -> None:
            captured_commands[text] = command

        def record_route(route_app: object, *names: str) -> None:
            routed_calls.append((route_app, names))

        with (
            patch.object(account_sources_fix.ttk, "LabelFrame", return_value=_FakeLabelFrame()),
            patch.object(account_sources_fix, "_add_action_button", capture_button),
            patch.object(account_sources_fix, "_run_schwab_workspace_action", record_route),
        ):
            account_sources_fix._build_schwab_action_grid(app, _FakeLabelFrame())
            live_submit = captured_commands["LIVE Submit"]
            assert callable(live_submit)
            live_submit()

        self.assertEqual(routed_calls, [(app, ("submit_live_schwab_order_guarded",))])
        self.assertNotIn("submit_selected_venue", routed_calls[0][1])

    def test_base_live_submit_is_one_click_without_confirmation_dialog(self) -> None:
        session = _AcceptedPreviewSession()
        app = _OneClickSubmitApp(session)

        with (
            patch.dict(
                "os.environ",
                {
                    "SCHWAB_ENABLE_LIVE_ORDERS": "true",
                    "SCHWAB_MAX_LIVE_ORDER_DOLLARS": "500",
                },
            ),
            patch(
                "app.ui.trading_cockpit.messagebox.askyesno",
                side_effect=AssertionError("LIVE Submit must not ask for a second confirmation."),
            ),
        ):
            SchwabTradingCockpitApp.submit_live_schwab_order_guarded(app)

        self.assertTrue(session.submitted)
        self.assertIn("LIVE SCHWAB ORDER SUBMIT RESULT", app.preview_text)

    def test_active_schwab_submit_emits_started_and_result_receipts(self) -> None:
        session = _AcceptedPreviewSession()
        app = _MechanicalSubmitApp(session)

        with patch.dict(
            "os.environ",
            {
                "SCHWAB_ENABLE_LIVE_ORDERS": "true",
                "SCHWAB_MAX_LIVE_ORDER_DOLLARS": "500",
            },
        ):
            _submit_with_mechanical_checks(app)

        self.assertTrue(session.submitted)
        self.assertIn("SCHWAB LIVE SUBMIT STARTED", app.output_records[0])
        self.assertIn("SCHWAB ORDER SUBMIT RESULT", app.output_records[-1])
        self.assertIn("HTTP Status: 201", app.output_records[-1])
        self.assertIn("/orders/123", app.output_records[-1])

    def test_active_schwab_submit_formats_preview_reject(self) -> None:
        session = _RejectedPreviewSession()
        app = _MechanicalSubmitApp(session)

        with patch.dict(
            "os.environ",
            {
                "SCHWAB_ENABLE_LIVE_ORDERS": "true",
                "SCHWAB_MAX_LIVE_ORDER_DOLLARS": "500",
            },
        ):
            _submit_with_mechanical_checks(app)

        self.assertFalse(session.submitted)
        self.assertIn("SCHWAB SUBMIT BLOCKED", app.output_records[-1])
        self.assertIn("Immediate Schwab preview was not accepted", app.output_records[-1])
        self.assertIn("SCHWAB PREVIEW RESULT", app.output_records[-1])

    def test_active_schwab_submit_formats_submit_exception(self) -> None:
        session = _SubmitExceptionSession()
        app = _MechanicalSubmitApp(session)

        with patch.dict(
            "os.environ",
            {
                "SCHWAB_ENABLE_LIVE_ORDERS": "true",
                "SCHWAB_MAX_LIVE_ORDER_DOLLARS": "500",
            },
        ):
            _submit_with_mechanical_checks(app)

        self.assertIn("SCHWAB SUBMIT ERROR", app.output_records[-1])
        self.assertIn("Schwab submit failed", app.output_records[-1])
        self.assertIn("RuntimeError: broker returned nothing useful", app.output_records[-1])
        self.assertIn("Submit status: UNKNOWN", app.output_records[-1])
        self.assertIn("Payload used for the submit attempt", app.output_records[-1])


class _Var:
    def __init__(self, value: str = "") -> None:
        self.value = value

    def get(self) -> str:
        return self.value

    def set(self, value: str) -> None:
        self.value = value


class _FakeLabelFrame:
    def grid(self, *_args: object, **_kwargs: object) -> None:
        return None

    def columnconfigure(self, *_args: object, **_kwargs: object) -> None:
        return None


class _AcceptedPreviewSession:
    def __init__(self) -> None:
        self.submitted = False

    def preview_order(self, payload: dict[str, object]) -> tuple[int, dict[str, object]]:
        return 200, {"orderStrategy": {"status": "ACCEPTED"}}

    def submit_live_order(self, payload: dict[str, object]) -> tuple[int, object, str]:
        self.submitted = True
        return 201, None, "https://api.schwabapi.com/trader/v1/accounts/acct/orders/123"


class _RejectedPreviewSession(_AcceptedPreviewSession):
    def preview_order(self, payload: dict[str, object]) -> tuple[int, dict[str, object]]:
        return 200, {
            "orderStrategy": {"status": "REJECTED", "orderType": "LIMIT"},
            "orderValidationResult": {
                "rejects": [{"message": "Preview rejected by test."}],
            },
        }


class _SubmitExceptionSession(_AcceptedPreviewSession):
    def submit_live_order(self, payload: dict[str, object]) -> tuple[int, object, str]:
        raise RuntimeError("broker returned nothing useful")


class _OneClickSubmitApp:
    def __init__(self, session: _AcceptedPreviewSession) -> None:
        self.session = session
        self.confirmation_var = _Var("")
        self.schwab_status_var = _Var("")
        self.preview_text = ""

    def _parse_order(self) -> SimpleNamespace:
        return SimpleNamespace(
            symbol="SWMR",
            side=SimpleNamespace(value="buy"),
            order_type=SimpleNamespace(value="limit"),
            quantity=1,
            limit_price=68.795,
        )

    def build_schwab_order_json_from_ui(self) -> dict[str, object]:
        return {
            "orderType": "LIMIT",
            "price": "68.795",
            "orderLegCollection": [
                {
                    "instruction": "BUY",
                    "quantity": 1,
                    "instrument": {"symbol": "SWMR", "assetType": "EQUITY"},
                }
            ],
        }

    def _authorize_schwab_session(self) -> _AcceptedPreviewSession:
        return self.session

    def _record_schwab_preview_status(self, payload: dict[str, object]) -> None:
        self.last_schwab_preview_status = "ACCEPTED"

    def _set_preview_text(self, text: str) -> None:
        self.preview_text = text


class _MechanicalSubmitApp(_OneClickSubmitApp):
    def __init__(self, session: _AcceptedPreviewSession) -> None:
        super().__init__(session)
        self.output_records: list[str] = []

    def update_idletasks(self) -> None:
        return None

    def _set_preview_text(self, text: str) -> None:
        self.preview_text = text
        self.output_records.append(text)


if __name__ == "__main__":
    unittest.main()
