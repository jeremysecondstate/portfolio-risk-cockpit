from __future__ import annotations

from datetime import datetime, timedelta, timezone
import re
import tkinter as tk
from tkinter import messagebox
from typing import Any, Callable, Type

from app.brokers.schwab.account_adapter import (
    format_schwab_account_snapshot,
    portfolio_from_schwab_account,
)


# Only true authorization failures should clear cached Schwab tokens.
# Provider/account-service 5xx responses are transient sync failures, not proof
# that the saved OAuth grant is invalid.
_REAUTH_STATUS_CODES = {401, 403}
_TEMPORARY_PROVIDER_STATUS_CODES = {500, 502, 503, 504}
_MATERIAL_DAY_PNL_DIFF_DOLLARS = 5.0
_MATERIAL_DAY_PNL_DIFF_RATIO = 0.25


def install_schwab_sync_report_extension(app_cls: Type[tk.Tk]) -> None:
    """Give Schwab sync the same rich report treatment as Hyperliquid sync."""

    previous_sync_snapshot = getattr(app_cls, "_sync_schwab_account_snapshot", None)
    if callable(previous_sync_snapshot):
        app_cls._sync_schwab_account_snapshot = _wrap_schwab_snapshot_sync(previous_sync_snapshot)  # type: ignore[method-assign]

    app_cls.connect_schwab = _connect_schwab_with_report  # type: ignore[method-assign]
    app_cls.refresh_schwab_account = _refresh_schwab_account_with_report  # type: ignore[method-assign]


def _connect_schwab_with_report(self: tk.Tk) -> None:
    try:
        session = self._authorize_schwab_session()
        if session is None:
            return
        self.schwab_session = session
        report = _sync_schwab_account_report_with_reauth_fallback(self, session)
        if report is None:
            return
        _mark_schwab_sync_status(self, "success")
        self.schwab_status_var.set("Schwab session: connected")
        self._set_preview_text(report)
    except Exception as exc:
        if _is_temporary_schwab_provider_error(exc):
            _mark_schwab_sync_status(self, "failure")
            self.schwab_status_var.set("Schwab session: connected; retry account sync")
            self._set_preview_text(_schwab_account_refresh_failure_report(exc))
            return
        _mark_schwab_sync_status(self, "failure")
        self.schwab_session = None
        self.schwab_status_var.set("Schwab session: not connected")
        messagebox.showerror("Schwab connect failed", str(exc))


def _refresh_schwab_account_with_report(self: tk.Tk) -> None:
    try:
        session = self._authorize_schwab_session()
        if session is None:
            return
        report = _sync_schwab_account_report_with_reauth_fallback(self, session)
        if report is None:
            return
        _mark_schwab_sync_status(self, "success")
        self.schwab_status_var.set("Schwab session: connected")
        self._set_preview_text(report)
    except Exception as exc:
        if _is_temporary_schwab_provider_error(exc):
            _mark_schwab_sync_status(self, "failure")
            self.schwab_status_var.set("Schwab session: connected; retry account sync")
            self._set_preview_text(_schwab_account_refresh_failure_report(exc))
            return
        _mark_schwab_sync_status(self, "failure")
        messagebox.showerror("Schwab account refresh failed", str(exc))


def _wrap_schwab_snapshot_sync(previous_sync_snapshot: Callable[[tk.Tk, object], str]) -> Callable[[tk.Tk, object], str]:
    def sync_snapshot_with_reauth_fallback(self: tk.Tk, session: object) -> str:
        try:
            result = previous_sync_snapshot(self, session)
            _mark_schwab_sync_status(self, "success")
            return result
        except Exception as exc:
            if not _should_force_schwab_reauthorization(exc):
                _mark_schwab_sync_status(self, "failure")
                raise

            retry_session = _force_schwab_reauthorization(self, session, exc)
            if retry_session is None:
                _mark_schwab_sync_status(self, "failure")
                raise RuntimeError("Schwab reauthorization canceled; no authorization was provided.") from exc
            try:
                result = previous_sync_snapshot(self, retry_session)
                _mark_schwab_sync_status(self, "success")
                return result
            except Exception:
                _mark_schwab_sync_status(self, "failure")
                raise

    return sync_snapshot_with_reauth_fallback


def _sync_schwab_account_report_with_reauth_fallback(self: tk.Tk, session) -> str | None:
    """Sync once, but force a fresh browser authorization only for auth failures."""

    try:
        return _sync_schwab_account_report(self, session)
    except Exception as exc:
        if not _should_force_schwab_reauthorization(exc):
            raise

        retry_session = _force_schwab_reauthorization(self, session, exc)
        if retry_session is None:
            return None

        return _sync_schwab_account_report(self, retry_session)


def _force_schwab_reauthorization(self: tk.Tk, session, exc: Exception):
    # Clear cached auth only when Schwab explicitly rejects authorization. Do not
    # clear tokens for provider-side 5xx/account-sync outages.
    clear_cached_authorization = getattr(session, "clear_cached_authorization", None)
    if callable(clear_cached_authorization):
        clear_cached_authorization()
    self.schwab_session = None
    self.schwab_status_var.set("Schwab session: saved authorization rejected; login required")
    interactive = bool(getattr(self, "_schwab_auth_interactive", True))
    if not interactive:
        return None

    self._set_preview_text(_schwab_reauthorization_required_report(exc))

    retry_session = self._authorize_schwab_session()
    if retry_session is None:
        return None

    self.schwab_session = retry_session
    return retry_session


def _sync_schwab_account_report(self: tk.Tk, session) -> str:
    """Fetch Schwab once, update the cockpit, and return a detailed report."""

    status_code, account_payload = session.get_account(fields="positions")
    if status_code != 200:
        raise RuntimeError(f"Schwab account fetch returned HTTP {status_code}: {account_payload}")

    portfolio, source_message = portfolio_from_schwab_account(account_payload)
    _apply_quote_day_pnl_overrides(session, portfolio)
    self.broker.set_portfolio(portfolio, source_message)
    self.last_hyperliquid_cash_adjustment = 0.0
    self.refresh_portfolio()
    _mark_schwab_sync_status(self, "success")
    report = format_schwab_account_snapshot(account_payload, portfolio)
    return report + _format_quote_day_pnl_report(portfolio)


def _apply_quote_day_pnl_overrides(session: object, portfolio: object) -> None:
    """Prefer quote-derived day P&L for Schwab equities/ETFs.

    Overnight shares use quote `netChange` because that is per-share Last minus
    prior close. Shares opened today use Last minus today's fill price instead;
    otherwise a brand-new position incorrectly inherits the whole stock move
    from yesterday's close.
    """

    applied: list[str] = []
    material_differences: list[str] = []
    fallbacks: list[str] = []
    same_day_adjustments: list[str] = []
    fill_fetch_note = ""
    positions = getattr(portfolio, "positions", {})
    if not isinstance(positions, dict):
        return

    get_quote = getattr(session, "get_quote", None)
    if not callable(get_quote):
        return

    same_day_buy_fills, fill_fetch_note = _same_day_buy_fills_by_symbol(session)

    for symbol, position in sorted(positions.items()):
        display_symbol = str(getattr(position, "symbol", symbol)).strip().upper()
        if not display_symbol or not _quote_day_pnl_supported(position):
            continue

        try:
            status_code, quote_payload = get_quote(display_symbol)
        except Exception:
            fallbacks.append(display_symbol)
            continue

        if status_code != 200:
            fallbacks.append(display_symbol)
            continue

        quote = _extract_quote_fields(quote_payload, display_symbol)
        if quote is None:
            fallbacks.append(display_symbol)
            continue

        net_change = _first_quote_number(quote, "netChange", "markChange")
        if net_change is None:
            fallbacks.append(display_symbol)
            continue

        quantity = _position_quantity(position)
        if quantity is None:
            fallbacks.append(display_symbol)
            continue

        last_price = _first_quote_number(quote, "lastPrice", "mark")
        if last_price is None:
            last_price = _position_last_price(position)

        quote_day_pnl = round(quantity * net_change, 2)
        same_day_calc = _same_day_adjusted_day_pnl(
            current_quantity=quantity,
            net_change=net_change,
            last_price=last_price,
            buy_fills=same_day_buy_fills.get(display_symbol, []),
        )
        if same_day_calc is not None:
            quote_day_pnl = same_day_calc["day_pnl"]
            same_day_adjustments.append(
                f"{display_symbol}: {same_day_calc['today_quantity']:g} share(s) opened today at avg {_money(same_day_calc['today_average_price'])}"
            )

        account_day_pnl = getattr(position, "day_profit_loss", None)
        position.day_profit_loss = quote_day_pnl

        if same_day_calc is not None and same_day_calc["basis"] > 0:
            position.day_profit_loss_percent = round((quote_day_pnl / same_day_calc["basis"]) * 100.0, 2)
        else:
            quote_percent = _first_quote_number(quote, "netPercentChange", "markPercentChange")
            if quote_percent is not None:
                position.day_profit_loss_percent = quote_percent

        source = "Schwab quote netChange × overnight quantity + last-minus-fill for today's buys" if same_day_calc is not None else "Schwab quote netChange × quantity"
        setattr(position, "day_profit_loss_source", source)
        applied.append(display_symbol)

        if _material_day_pnl_difference(account_day_pnl, quote_day_pnl):
            material_differences.append(
                f"{display_symbol}: account {_optional_money(account_day_pnl)} → quote {_money(quote_day_pnl)}"
            )

    setattr(portfolio, "schwab_quote_day_pnl_symbols", applied)
    setattr(portfolio, "schwab_quote_day_pnl_differences", material_differences)
    setattr(portfolio, "schwab_quote_day_pnl_fallbacks", fallbacks)
    setattr(portfolio, "schwab_quote_day_pnl_same_day_adjustments", same_day_adjustments)
    setattr(portfolio, "schwab_quote_day_pnl_fill_fetch_note", fill_fetch_note)


def _same_day_buy_fills_by_symbol(session: object) -> tuple[dict[str, list[dict[str, float]]], str]:
    get_orders = getattr(session, "get_orders", None)
    if not callable(get_orders):
        return {}, ""

    now = datetime.now(timezone.utc)
    from_time = now.replace(hour=0, minute=0, second=0, microsecond=0)
    if from_time > now:
        from_time = now - timedelta(hours=24)

    try:
        status_code, payload = get_orders(from_entered_time=from_time, to_entered_time=now)
    except Exception as exc:
        return {}, f"same-day fills unavailable ({type(exc).__name__}: {exc})"

    if status_code != 200 or not isinstance(payload, list):
        return {}, f"same-day fills unavailable (orders HTTP {status_code})"

    fills: dict[str, list[dict[str, float]]] = {}
    for raw_order in payload:
        if isinstance(raw_order, dict):
            _collect_same_day_buy_fills(raw_order, fills)
    return fills, ""


def _collect_same_day_buy_fills(order: dict[str, Any], fills: dict[str, list[dict[str, float]]]) -> None:
    for child in order.get("childOrderStrategies") or []:
        if isinstance(child, dict):
            _collect_same_day_buy_fills(child, fills)

    status = str(order.get("status") or "").upper()
    if "FILLED" not in status:
        return

    fill_price = _order_average_fill_price(order)
    if fill_price is None or fill_price <= 0:
        return

    legs = order.get("orderLegCollection") or order.get("orderLegs") or []
    if not isinstance(legs, list):
        return

    for leg in legs:
        if not isinstance(leg, dict):
            continue
        instruction = str(leg.get("instruction") or order.get("instruction") or "").upper()
        if "BUY" not in instruction or "SELL" in instruction:
            continue
        if _leg_is_option(leg):
            continue
        symbol = _leg_symbol(leg, order)
        if not symbol:
            continue
        quantity = _leg_filled_quantity(order, leg)
        if quantity is None or quantity <= 0:
            continue
        fills.setdefault(symbol, []).append({"quantity": quantity, "price": fill_price})


def _leg_symbol(leg: dict[str, Any], order: dict[str, Any]) -> str:
    instrument = leg.get("instrument") if isinstance(leg.get("instrument"), dict) else {}
    return str(instrument.get("symbol") or leg.get("finalSymbol") or order.get("symbol") or "").strip().upper()


def _leg_is_option(leg: dict[str, Any]) -> bool:
    instrument = leg.get("instrument") if isinstance(leg.get("instrument"), dict) else {}
    pieces = [str(instrument.get("assetType") or ""), str(instrument.get("assetSubType") or instrument.get("type") or "")]
    return "OPTION" in " ".join(pieces).upper()


def _leg_filled_quantity(order: dict[str, Any], leg: dict[str, Any]) -> float | None:
    for value in (
        order.get("filledQuantity"),
        order.get("filled_quantity"),
        leg.get("filledQuantity"),
        leg.get("quantity"),
        order.get("quantity"),
    ):
        parsed = _to_float(value)
        if parsed is not None and parsed > 0:
            return parsed
    return None


def _order_average_fill_price(order: dict[str, Any]) -> float | None:
    execution_value = 0.0
    execution_quantity = 0.0
    for activity in order.get("orderActivityCollection") or []:
        if not isinstance(activity, dict):
            continue
        for execution in activity.get("executionLegs") or []:
            if not isinstance(execution, dict):
                continue
            price = _to_float(execution.get("price"))
            quantity = _to_float(execution.get("quantity")) or _to_float(execution.get("legQuantity"))
            if price is None or price <= 0:
                continue
            if quantity is None or quantity <= 0:
                quantity = 1.0
            execution_value += price * quantity
            execution_quantity += quantity
    if execution_quantity > 0:
        return execution_value / execution_quantity

    for key in ("averagePrice", "avgPrice", "price", "netPrice"):
        price = _to_float(order.get(key))
        if price is not None and price > 0:
            return price
    return None


def _same_day_adjusted_day_pnl(
    *,
    current_quantity: float,
    net_change: float,
    last_price: float | None,
    buy_fills: list[dict[str, float]],
) -> dict[str, float] | None:
    if current_quantity <= 0 or last_price is None or not buy_fills:
        return None

    total_buy_quantity = sum(fill["quantity"] for fill in buy_fills if fill.get("quantity", 0.0) > 0)
    total_buy_cost = sum(fill["quantity"] * fill["price"] for fill in buy_fills if fill.get("quantity", 0.0) > 0 and fill.get("price", 0.0) > 0)
    if total_buy_quantity <= 0 or total_buy_cost <= 0:
        return None

    today_quantity = min(current_quantity, total_buy_quantity)
    if today_quantity <= 0:
        return None

    today_average_price = total_buy_cost / total_buy_quantity
    overnight_quantity = max(current_quantity - today_quantity, 0.0)
    day_pnl = round((overnight_quantity * net_change) + (today_quantity * (last_price - today_average_price)), 2)
    basis = abs((overnight_quantity * max(last_price - net_change, 0.0)) + (today_quantity * today_average_price))
    return {
        "day_pnl": day_pnl,
        "today_quantity": today_quantity,
        "today_average_price": today_average_price,
        "basis": basis,
    }


def _quote_day_pnl_supported(position: object) -> bool:
    asset_type = str(getattr(position, "asset_type", "") or "").upper()
    if "OPTION" in asset_type:
        return False
    return True


def _position_quantity(position: object) -> float | None:
    try:
        return float(getattr(position, "quantity"))
    except (TypeError, ValueError):
        return None


def _position_last_price(position: object) -> float | None:
    try:
        return float(getattr(position, "last_price"))
    except (TypeError, ValueError):
        return None


def _extract_quote_fields(payload: Any, symbol: str) -> dict[str, Any] | None:
    if not isinstance(payload, dict):
        return None

    for key in (symbol, symbol.upper(), symbol.lower()):
        quote = _quote_dict_from_entry(payload.get(key))
        if quote is not None:
            return quote

    for value in payload.values():
        quote = _quote_dict_from_entry(value)
        if quote is not None:
            return quote

    return _quote_dict_from_entry(payload)


def _quote_dict_from_entry(entry: Any) -> dict[str, Any] | None:
    if not isinstance(entry, dict):
        return None

    nested_quote = entry.get("quote")
    if isinstance(nested_quote, dict):
        return nested_quote

    if any(
        key in entry
        for key in (
            "netChange",
            "netPercentChange",
            "markChange",
            "markPercentChange",
            "lastPrice",
            "mark",
            "closePrice",
        )
    ):
        return entry

    return None


def _first_quote_number(source: dict[str, Any], *keys: str) -> float | None:
    for key in keys:
        value = source.get(key)
        if value in (None, ""):
            continue
        try:
            return float(str(value).replace(",", ""))
        except (TypeError, ValueError):
            continue
    return None


def _to_float(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(str(value).replace(",", ""))
    except (TypeError, ValueError):
        return None


def _material_day_pnl_difference(account_day_pnl: object, quote_day_pnl: float) -> bool:
    try:
        account_value = float(account_day_pnl)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return False

    difference = abs(account_value - quote_day_pnl)
    threshold = max(abs(quote_day_pnl) * _MATERIAL_DAY_PNL_DIFF_RATIO, _MATERIAL_DAY_PNL_DIFF_DOLLARS)
    return difference > threshold


def _format_quote_day_pnl_report(portfolio: object) -> str:
    symbols = list(getattr(portfolio, "schwab_quote_day_pnl_symbols", []) or [])
    differences = list(getattr(portfolio, "schwab_quote_day_pnl_differences", []) or [])
    fallbacks = list(getattr(portfolio, "schwab_quote_day_pnl_fallbacks", []) or [])
    same_day_adjustments = list(getattr(portfolio, "schwab_quote_day_pnl_same_day_adjustments", []) or [])
    fill_fetch_note = str(getattr(portfolio, "schwab_quote_day_pnl_fill_fetch_note", "") or "")
    if not symbols and not differences and not fallbacks and not same_day_adjustments and not fill_fetch_note:
        return ""

    lines = ["", "", "Day P&L reconciliation:"]
    if symbols:
        lines.append(f"- Used Schwab quote netChange for overnight shares and fill-price math for same-day buys across {len(symbols)} stock/ETF position(s): {', '.join(symbols)}.")
    if same_day_adjustments:
        shown_same_day = same_day_adjustments[:8]
        lines.append("- Same-day entry adjustment: " + "; ".join(shown_same_day) + ("; …" if len(same_day_adjustments) > len(shown_same_day) else "."))
    if differences:
        shown = differences[:8]
        lines.append("- Overrode materially different account-position Day P&L: " + "; ".join(shown) + ("; …" if len(differences) > len(shown) else "."))
    if fallbacks:
        unique_fallbacks = sorted(set(fallbacks))
        shown_fallbacks = unique_fallbacks[:8]
        lines.append("- Quote Day P&L unavailable for " + ", ".join(shown_fallbacks) + (", …" if len(unique_fallbacks) > len(shown_fallbacks) else "") + "; kept Schwab account-position Day P&L for those rows.")
    if fill_fetch_note:
        lines.append(f"- Same-day fill check: {fill_fetch_note}; pure quote Day P&L was used where quotes were available.")
    return "\n".join(lines)


def _mark_schwab_sync_status(self: tk.Tk, status: str) -> None:
    setter = getattr(self, "set_schwab_sync_status", None)
    if callable(setter):
        try:
            setter(status)
            return
        except Exception:
            pass
    var = getattr(self, "schwab_sync_status_var", None)
    if var is not None:
        try:
            var.set("✓ Synced" if status == "success" else "✕ Sync failed")
        except Exception:
            pass


def _should_force_schwab_reauthorization(exc: Exception) -> bool:
    status_code = _extract_http_status_code(exc)
    if status_code in _REAUTH_STATUS_CODES:
        return True
    if status_code in _TEMPORARY_PROVIDER_STATUS_CODES:
        return False

    text = str(exc).lower()
    auth_markers = (
        "invalid_grant",
        "invalid_token",
        "invalid token",
        "unauthorized",
        "forbidden",
        "token expired",
    )
    return any(marker in text for marker in auth_markers)


def _extract_http_status_code(exc: Exception) -> int | None:
    match = re.search(r"http\s+(\d{3})", str(exc), flags=re.IGNORECASE)
    if not match:
        return None
    try:
        return int(match.group(1))
    except ValueError:
        return None


def _is_temporary_schwab_provider_error(exc: Exception) -> bool:
    status_code = _extract_http_status_code(exc)
    if status_code in _TEMPORARY_PROVIDER_STATUS_CODES:
        return True
    text = str(exc).lower()
    return "temporarily unavailable" in text or "server error" in text


def _schwab_reauthorization_required_report(exc: Exception) -> str:
    return (
        "SCHWAB REAUTHORIZATION REQUIRED\n"
        "===============================\n\n"
        "Schwab rejected the saved authorization while the app tried to fetch balances and positions.\n\n"
        f"Provider response: {exc}\n\n"
        "What the app is doing now:\n"
        "- Cleared the in-memory Schwab session and saved local token cache.\n"
        "- Opened the Schwab authorization page so you can sign in again.\n"
        "- Will retry Sync Schwab with the new authorization code after you paste it.\n\n"
        "No order was previewed, submitted, replaced, or canceled."
    )


def _schwab_account_refresh_failure_report(exc: Exception) -> str:
    return (
        "SCHWAB ACCOUNT SYNC TEMPORARILY FAILED\n"
        "=====================================\n\n"
        "The saved Schwab authorization was kept because this looks like a provider/account-sync failure, not a rejected OAuth token.\n\n"
        f"Provider response: {exc}\n\n"
        "What the app did:\n"
        "- Kept the current local/cached portfolio visible.\n"
        "- Kept the saved Schwab authorization instead of forcing a browser login.\n"
        "- Did not submit, preview, replace, or cancel any order.\n\n"
        "Next step: click Sync Schwab again in a moment, or use Reset Session only if Schwab explicitly rejects authorization."
    )


def _money(value: float) -> str:
    return f"${value:,.2f}"


def _optional_money(value: object) -> str:
    try:
        return _money(float(value))  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return "--"
