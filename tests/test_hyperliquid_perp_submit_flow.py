from __future__ import annotations

from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest import TestCase, main
from unittest.mock import Mock, patch

from app.brokers.hyperliquid.trading import HyperliquidOrderTicket
from app.core.portfolio import Portfolio
from app.ui.hyperliquid_assessment_extension import (
    HyperliquidAccountTarget,
    _copy_hyperliquid_open_orders_for_account,
    _sync_hyperliquid_account_with_assessment,
)
from app.ui.trading_workspace_extension import _populate_workspace_open_orders_table
from app.ui import hyperliquid_submit_flow as flow
from app.ui import hyperliquid_trading_extension as trading_ui
from app.ui.hyperliquid_trading_extension import (
    _block_hyperliquid_account_live_action,
    _load_hyperliquid_open_orders,
    _matching_tpsl_orders,
    _submit_cockpit_selected_venue,
    normalize_hyperliquid_open_order,
)


class _Var:
    def __init__(self, value: object = "") -> None:
        self.value = value

    def get(self) -> object:
        return self.value

    def set(self, value: object) -> None:
        self.value = value


class _PerpSubmitApp:
    def __init__(self, ticket: HyperliquidOrderTicket, *, attach_tpsl: bool = False) -> None:
        self.ticket = ticket
        self.preview_texts: list[str] = []
        self.sync_calls = 0
        self.hyperliquid_status_var = _Var("")
        self.hyperliquid_leverage_var = _Var("3")
        self.hyperliquid_margin_mode_var = _Var("Cross")
        self.hyperliquid_attach_tpsl_var = _Var(attach_tpsl)
        self.hyperliquid_target_price_var = _Var("")
        self.hyperliquid_bad_price_var = _Var("")
        self.stop_price_var = _Var("")
        self.limit_price_var = _Var(str(ticket.limit_price))
        self.quantity_var = _Var(str(ticket.size))

    def parse_hyperliquid_ticket(self) -> HyperliquidOrderTicket:
        return self.ticket

    def parse_hyperliquid_spot_ticket(self) -> HyperliquidOrderTicket:
        raise AssertionError("perp live submit must not parse the spot ticket")

    def _set_preview_text(self, text: str) -> None:
        self.preview_texts.append(text)

    def sync_hyperliquid_account(self) -> None:
        self.sync_calls += 1


class _OpenOrdersTable:
    def __init__(self, orders_by_oid: dict[str, dict[str, object]]) -> None:
        self._hyperliquid_open_order_by_oid = orders_by_oid


class _FakeOpenOrdersTree:
    def __init__(self) -> None:
        self.columns = ("account", "time", "type", "coin", "direction", "size", "price", "edit", "ro", "trigger", "tpsl", "oid")
        self.rows: dict[str, dict[str, object]] = {}

    def __getitem__(self, key: str) -> object:
        if key == "columns":
            return self.columns
        raise KeyError(key)

    def get_children(self) -> tuple[str, ...]:
        return tuple(self.rows)

    def delete(self, row_id: str) -> None:
        self.rows.pop(row_id, None)

    def insert(self, parent: str, index: object, *, iid: str, values: tuple[object, ...], tags: tuple[str, ...] = ()) -> None:
        self.rows[iid] = {"parent": parent, "index": index, "values": values, "tags": tags}

    def item(self, row_id: str, option: str) -> object:
        if option == "values":
            return self.rows[row_id]["values"]
        raise KeyError(option)


class _OpenOrdersApp:
    def __init__(self, table_orders: dict[str, dict[str, object]]) -> None:
        self.hyperliquid_open_order_by_oid: dict[str, dict[str, object]] = {}
        self.hyperliquid_workspace_open_orders_table = _OpenOrdersTable(table_orders)


class _PreviewApp:
    def __init__(self) -> None:
        self.preview_texts: list[str] = []
        self.hyperliquid_status_var = _Var("")

    def _set_preview_text(self, text: str) -> None:
        self.preview_texts.append(text)


class HyperliquidPerpSubmitFlowTests(TestCase):
    def test_workspace_live_submit_buttons_are_explicitly_separate(self) -> None:
        source = Path("app/ui/trading_workspace_extension.py").read_text(encoding="utf-8")

        self.assertIn(
            'text="LIVE Submit", command=hyperliquid_action("spot", "show_hyperliquid_spot_live_submit_safety_review")',
            source,
        )
        self.assertIn(
            'text="LIVE Submit", command=hyperliquid_action("perp", "show_hyperliquid_perp_live_submit_safety_review")',
            source,
        )

    def test_selected_venue_dispatches_hyperliquid_spot_and_perp_separately(self) -> None:
        app = Mock()
        app.trade_venue_var = _Var("Hyperliquid")
        app.hyperliquid_workspace_active_ticket_var = _Var("spot")

        _submit_cockpit_selected_venue(app)
        app.show_hyperliquid_spot_live_submit_safety_review.assert_called_once_with()
        app.show_hyperliquid_perp_live_submit_safety_review.assert_not_called()

        app.show_hyperliquid_spot_live_submit_safety_review.reset_mock()
        app.hyperliquid_workspace_active_ticket_var.set("perp")

        _submit_cockpit_selected_venue(app)
        app.show_hyperliquid_perp_live_submit_safety_review.assert_called_once_with()
        app.show_hyperliquid_spot_live_submit_safety_review.assert_not_called()

    def test_perp_handler_uses_perp_parser_outputs_title_and_parent_only_when_tpsl_off(self) -> None:
        ticket = HyperliquidOrderTicket("BTC", True, 0.25, 100000.0, "Gtc", reduce_only=False)
        app = _PerpSubmitApp(ticket, attach_tpsl=False)
        adapter = Mock()
        adapter.update_leverage.return_value = {"leverage": "ok"}
        adapter.submit.return_value = {"parent": "ok"}

        with patch.object(flow, "HyperliquidExecutionAdapter", return_value=adapter):
            flow._show_hyperliquid_perp_live_submit_safety_review(app)  # type: ignore[arg-type]

        adapter.submit.assert_called_once()
        adapter.place_position_tpsl.assert_not_called()
        self.assertEqual(adapter.submit.call_args.args[0].coin, "BTC")
        self.assertIn("HYPERLIQUID PERP LIVE SUBMIT RESULT", app.preview_texts[-1])
        self.assertIn("This was submitted through the PERP order path", app.preview_texts[-1])
        self.assertIn("Parent submit result:", app.preview_texts[-1])
        self.assertEqual(app.sync_calls, 1)

    def test_attached_tpsl_builds_sell_reduce_only_triggers_for_long_parent(self) -> None:
        ticket = HyperliquidOrderTicket("ETH", True, 2.0, 3000.0, "Gtc")
        app = _PerpSubmitApp(ticket, attach_tpsl=True)
        app.hyperliquid_target_price_var.set("3300")
        app.hyperliquid_bad_price_var.set("2900")

        triggers = flow._attached_tpsl_tickets(app, ticket)  # type: ignore[arg-type]

        self.assertEqual([(trigger.is_buy, trigger.tpsl, trigger.trigger_price) for trigger in triggers], [(False, "tp", 3300.0), (False, "sl", 2900.0)])

    def test_attached_tpsl_builds_buy_reduce_only_triggers_for_short_parent(self) -> None:
        ticket = HyperliquidOrderTicket("SOL", False, 5.0, 150.0, "Gtc")
        app = _PerpSubmitApp(ticket, attach_tpsl=True)
        app.hyperliquid_target_price_var.set("125")
        app.hyperliquid_bad_price_var.set("170")

        triggers = flow._attached_tpsl_tickets(app, ticket)  # type: ignore[arg-type]

        self.assertEqual([(trigger.is_buy, trigger.tpsl, trigger.trigger_price) for trigger in triggers], [(True, "tp", 125.0), (True, "sl", 170.0)])

    def test_attached_tpsl_on_submits_children_after_parent(self) -> None:
        ticket = HyperliquidOrderTicket("ETH", True, 2.0, 3000.0, "Gtc")
        app = _PerpSubmitApp(ticket, attach_tpsl=True)
        app.hyperliquid_target_price_var.set("3300")
        app.hyperliquid_bad_price_var.set("2900")
        adapter = Mock()
        adapter.update_leverage.return_value = {"leverage": "ok"}
        adapter.submit.return_value = {"parent": "ok"}
        adapter.place_position_tpsl.return_value = {"children": "ok"}

        with patch.object(flow, "HyperliquidExecutionAdapter", return_value=adapter):
            flow._show_hyperliquid_perp_live_submit_safety_review(app)  # type: ignore[arg-type]

        adapter.submit.assert_called_once()
        adapter.place_position_tpsl.assert_called_once()
        child_tickets = adapter.place_position_tpsl.call_args.args[0]
        self.assertEqual([(child.is_buy, child.tpsl) for child in child_tickets], [(False, "tp"), (False, "sl")])
        self.assertIn("Child TP/SL result:", app.preview_texts[-1])

    def test_parent_submit_failure_prevents_child_tpsl_submit(self) -> None:
        ticket = HyperliquidOrderTicket("ETH", True, 2.0, 3000.0, "Gtc")
        app = _PerpSubmitApp(ticket, attach_tpsl=True)
        app.hyperliquid_target_price_var.set("3300")
        adapter = Mock()
        adapter.update_leverage.return_value = {"leverage": "ok"}
        adapter.submit.side_effect = RuntimeError("parent rejected")

        with (
            patch.object(flow, "HyperliquidExecutionAdapter", return_value=adapter),
            patch.object(flow.messagebox, "showerror") as showerror,
        ):
            flow._show_hyperliquid_perp_live_submit_safety_review(app)  # type: ignore[arg-type]

        adapter.place_position_tpsl.assert_not_called()
        showerror.assert_called_once()
        self.assertIn("perp live submit blocked", showerror.call_args.args[0])

    def test_position_tpsl_open_order_normalizes_as_reduce_only_trigger(self) -> None:
        order = normalize_hyperliquid_open_order(
            {
                "oid": "123",
                "coin": "HYPE",
                "side": "B",
                "sz": "100",
                "triggerPx": "72",
                "orderType": "Stop Market",
                "isPositionTpsl": True,
                "tpsl": "sl",
                "price": "Market",
            }
        )

        self.assertTrue(order.reduce_only)
        self.assertTrue(order.is_trigger)
        self.assertEqual(order.tpsl_label, "SL")

    def test_matching_tpsl_orders_uses_workspace_table_cache(self) -> None:
        app = _OpenOrdersApp(
            {
                "123": {
                    "oid": "123",
                    "coin": "HYPE",
                    "side": "B",
                    "sz": "100",
                    "triggerPx": "72",
                    "orderType": "Stop Market",
                    "isPositionTpsl": True,
                    "tpsl": "sl",
                }
            }
        )

        matches = _matching_tpsl_orders(app, "HYPE")  # type: ignore[arg-type]

        self.assertEqual([order.oid for order in matches], ["123"])

    def test_copy_open_orders_adds_account_metadata_without_changing_raw_lookup_fields(self) -> None:
        raw_order = {"oid": "101", "coin": "HYPE", "side": "B", "sz": "3", "limitPx": "40"}
        account = HyperliquidAccountTarget("Alex", "0xalex")

        copied = _copy_hyperliquid_open_orders_for_account([raw_order], account)

        self.assertEqual(copied[0]["oid"], "101")
        self.assertEqual(copied[0]["coin"], "HYPE")
        self.assertEqual(copied[0]["accountLabel"], "Alex")
        self.assertEqual(copied[0]["accountKey"], "alex")
        self.assertEqual(copied[0]["accountAddress"], "0xalex")
        self.assertNotIn("accountLabel", raw_order)

    def test_workspace_open_orders_table_keeps_combined_account_orders_separate(self) -> None:
        table = _FakeOpenOrdersTree()
        open_orders = [
            {
                "oid": "101",
                "coin": "HYPE",
                "side": "B",
                "sz": "3",
                "limitPx": "40",
                "accountLabel": "Jeremy",
                "accountKey": "jeremy",
                "accountAddress": "0xjeremy",
            },
            {
                "oid": "101",
                "coin": "BTC",
                "side": "A",
                "sz": "0.1",
                "limitPx": "100000",
                "accountLabel": "Alex",
                "accountKey": "alex",
                "accountAddress": "0xalex",
            },
        ]

        _populate_workspace_open_orders_table(table, open_orders)  # type: ignore[arg-type]

        self.assertEqual(set(table._hyperliquid_open_order_by_oid), {"jeremy:101", "alex:101"})
        self.assertEqual(table._hyperliquid_open_order_by_oid["jeremy:101"]["coin"], "HYPE")
        self.assertEqual(table._hyperliquid_open_order_by_oid["alex:101"]["coin"], "BTC")
        self.assertEqual(table._hyperliquid_open_order_coin_by_oid["alex:101"], "BTC")
        self.assertEqual(table._hyperliquid_open_order_key_by_iid["open_order_1"], "alex:101")
        alex_values = table.rows["open_order_1"]["values"]
        self.assertEqual(alex_values[0], "Alex")
        self.assertEqual(alex_values[7], "Read-only")
        self.assertEqual(alex_values[-1], "101")

    def test_explicit_open_orders_loads_all_configured_accounts(self) -> None:
        jeremy = HyperliquidAccountTarget("Jeremy", "0xjeremy")
        alex = HyperliquidAccountTarget("Alex", "0xalex")
        table = _FakeOpenOrdersTree()
        app = _PreviewApp()
        app.hyperliquid_workspace_open_orders_table = table
        app.hyperliquid_coin_var = _Var("")
        snapshots = {
            "0xjeremy": SimpleNamespace(
                user="0xjeremy000000000000000000000000000000000000",
                open_orders=[{"oid": "201", "coin": "HYPE", "side": "B", "sz": "2", "limitPx": "41"}],
                fetched_at=datetime(2026, 1, 1, 12, 0, 0),
            ),
            "0xalex": SimpleNamespace(
                user="0xalex0000000000000000000000000000000000000",
                open_orders=[{"oid": "301", "coin": "BTC", "side": "A", "sz": "0.2", "limitPx": "98000"}],
                fetched_at=datetime(2026, 1, 1, 12, 1, 0),
            ),
        }
        client = Mock()
        client.fetch_snapshot.side_effect = lambda address, include_open_orders=True: snapshots[address]

        with (
            patch("app.ui.hyperliquid_assessment_extension._hyperliquid_accounts_from_env", return_value=[jeremy, alex]),
            patch.object(trading_ui, "HyperliquidInfoClient", return_value=client),
        ):
            _load_hyperliquid_open_orders(app)  # type: ignore[arg-type]

        self.assertEqual([call.args[0] for call in client.fetch_snapshot.call_args_list], ["0xjeremy", "0xalex"])
        self.assertEqual(set(app.hyperliquid_open_order_by_oid), {"jeremy:201", "alex:301"})
        self.assertEqual(app.hyperliquid_status_var.get(), "Hyperliquid: 2 open orders")
        self.assertIn("Jeremy", app.preview_texts[-1])
        self.assertIn("Alex", app.preview_texts[-1])
        self.assertEqual(table.rows["open_order_1"]["values"][0], "Alex")

    def test_sync_hyperliquid_account_populates_combined_open_orders(self) -> None:
        jeremy = HyperliquidAccountTarget("Jeremy", "0xjeremy")
        alex = HyperliquidAccountTarget("Alex", "0xalex")
        table = _FakeOpenOrdersTree()
        app = _PreviewApp()
        app.hyperliquid_workspace_open_orders_table = table
        app.broker = SimpleNamespace(source_message="Base", set_portfolio=Mock())
        app._merge_hyperliquid_portfolio = Mock(return_value=Portfolio(cash=0.0))
        app.refresh_portfolio = Mock()
        snapshots = {
            "0xjeremy": SimpleNamespace(
                user="0xjeremy000000000000000000000000000000000000",
                open_orders=[{"oid": "401", "coin": "HYPE", "side": "B", "sz": "2", "limitPx": "41"}],
            ),
            "0xalex": SimpleNamespace(
                user="0xalex0000000000000000000000000000000000000",
                open_orders=[{"oid": "501", "coin": "BTC", "side": "A", "sz": "0.2", "limitPx": "98000"}],
            ),
        }
        client = Mock()
        client.fetch_snapshot.side_effect = lambda address: snapshots[address]

        with (
            patch("app.ui.hyperliquid_assessment_extension._hyperliquid_accounts_from_env", return_value=[jeremy, alex]),
            patch("app.ui.hyperliquid_assessment_extension.HyperliquidInfoClient", return_value=client),
            patch("app.ui.hyperliquid_assessment_extension.portfolio_from_hyperliquid_snapshot", return_value=(Portfolio(cash=0.0), "Loaded Hyperliquid account")),
            patch("app.ui.hyperliquid_assessment_extension.format_hyperliquid_snapshot", return_value="snapshot report"),
            patch("app.ui.hyperliquid_assessment_extension.format_hyperliquid_position_assessment", return_value="assessment"),
        ):
            _sync_hyperliquid_account_with_assessment(app)  # type: ignore[arg-type]

        self.assertEqual(set(app.hyperliquid_open_order_by_oid), {"jeremy:401", "alex:501"})
        self.assertEqual(table.rows["open_order_0"]["values"][0], "Jeremy")
        self.assertEqual(table.rows["open_order_1"]["values"][0], "Alex")
        app.broker.set_portfolio.assert_called_once()
        app.refresh_portfolio.assert_called_once()

    def test_alex_order_live_actions_are_blocked_before_signed_adapter(self) -> None:
        app = _PreviewApp()
        alex_order = {
            "oid": "601",
            "coin": "BTC",
            "accountLabel": "Alex",
            "accountKey": "alex",
            "accountAddress": "0xalex",
        }

        with patch.object(trading_ui.messagebox, "showinfo") as showinfo:
            blocked = _block_hyperliquid_account_live_action(app, "cancel", alex_order)  # type: ignore[arg-type]

        self.assertTrue(blocked)
        self.assertIn("Alex", app.hyperliquid_status_var.get())
        self.assertIn("No live request was sent", app.preview_texts[-1])
        showinfo.assert_called_once()


if __name__ == "__main__":
    main()
