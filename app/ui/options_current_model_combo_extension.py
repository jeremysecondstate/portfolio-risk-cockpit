from __future__ import annotations

import re
import tkinter as tk
from types import SimpleNamespace
from typing import Any, Type

from app.analytics import research_workspace_insights as insights
from app.ui import schwab_research_workspace_extension as research_ui

_ORIGINAL_COLUMNS = (
    "move",
    "price",
    "contracts",
    "current_stock",
    "model_stock",
    "option",
    "current_combined",
    "model_combined",
    "read",
)
_NEW_COLUMN = "current_model_combined"
_SCENARIO_COLUMN_SPECS = {
    "move": ("Move", 75, tk.W, False),
    "price": ("Stock Price", 105, tk.E, False),
    "contracts": ("Contracts", 85, tk.E, False),
    "current_stock": ("Current Stock", 105, tk.E, False),
    "model_stock": ("Model Stock", 105, tk.E, False),
    "option": ("Option Payoff", 105, tk.E, False),
    "current_combined": ("Current Combo", 112, tk.E, False),
    "model_combined": ("Model Combo", 112, tk.E, False),
    _NEW_COLUMN: ("Current + Model Combo", 165, tk.E, False),
    "read": ("Plain-English Read", 340, tk.W, True),
}

_installed = False
_original_combined_current_model_option_scenarios = None
_original_options_strategy_tab = None
_original_show_selected_option_candidate = None
_original_normalized_candidate_bar_rows = None


def install_options_current_model_combo_extension(app_cls: Type[tk.Tk]) -> None:
    """Add a current-shares + model-shares + option payoff view to option scenarios."""

    global _installed
    global _original_combined_current_model_option_scenarios
    global _original_options_strategy_tab
    global _original_show_selected_option_candidate
    global _original_normalized_candidate_bar_rows

    if _installed:
        return

    _original_combined_current_model_option_scenarios = insights.combined_current_model_option_scenarios
    _original_options_strategy_tab = research_ui._options_strategy_tab
    _original_show_selected_option_candidate = research_ui._show_selected_option_candidate
    _original_normalized_candidate_bar_rows = getattr(research_ui, "_normalized_candidate_bar_rows", None)

    insights.combined_current_model_option_scenarios = _combined_current_model_option_scenarios_with_total  # type: ignore[assignment]
    research_ui.combined_current_model_option_scenarios = _combined_current_model_option_scenarios_with_total  # type: ignore[assignment]
    research_ui._options_strategy_tab = _options_strategy_tab_with_current_model_combo  # type: ignore[assignment]
    research_ui._show_selected_option_candidate = _show_selected_option_candidate_with_current_model_combo  # type: ignore[assignment]
    if _original_normalized_candidate_bar_rows is not None:
        research_ui._normalized_candidate_bar_rows = _normalized_candidate_bar_rows_with_current_model_combo  # type: ignore[attr-defined]

    _installed = True


def _combined_current_model_option_scenarios_with_total(*args: Any, **kwargs: Any) -> list[Any]:
    if _original_combined_current_model_option_scenarios is None:
        return []
    rows = _original_combined_current_model_option_scenarios(*args, **kwargs)
    enriched: list[Any] = []
    for row in rows:
        data = dict(getattr(row, "__dict__", {}))
        if not data:
            enriched.append(row)
            continue
        current_stock = _optional_float(data.get("current_stock_pnl"))
        model_stock = _optional_float(data.get("model_stock_pnl"))
        option_pnl = _optional_float(data.get("option_pnl"))
        current_combined = _optional_float(data.get("current_combined_pnl"))
        portfolio_value = _portfolio_value_from_row(data)
        total_stock = None
        total_combo = None
        total_impact = None
        if current_stock is not None and model_stock is not None and option_pnl is not None:
            total_stock = current_stock + model_stock
            total_combo = total_stock + option_pnl
            if portfolio_value is not None and portfolio_value > 0:
                total_impact = total_combo / portfolio_value
        data["current_plus_model_stock_pnl"] = total_stock
        data["current_plus_model_combined_pnl"] = total_combo
        data["current_plus_model_portfolio_impact"] = total_impact
        if total_combo is not None:
            data["read"] = _combined_total_read(str(data.get("read", "")), total_combo, current_combined)
        enriched.append(SimpleNamespace(**data))
    return enriched


def _options_strategy_tab_with_current_model_combo(*args: Any, **kwargs: Any) -> Any:
    if _original_options_strategy_tab is None:
        raise RuntimeError("Options Strategy tab extension was not initialized.")
    frame = _original_options_strategy_tab(*args, **kwargs)
    _ensure_current_model_combo_column(frame)
    return frame


def _show_selected_option_candidate_with_current_model_combo(app: tk.Tk, *args: Any, **kwargs: Any) -> None:
    if _original_show_selected_option_candidate is None:
        return
    _original_show_selected_option_candidate(app, *args, **kwargs)
    frame = getattr(app, "schwab_research_options_frame", None)
    if frame is None:
        return
    _ensure_current_model_combo_column(frame)
    _repair_current_model_combo_rows(frame)
    _refresh_current_model_combo_bars(frame)


def _normalized_candidate_bar_rows_with_current_model_combo(rows: Any) -> list[tuple[str, float, str]]:
    if _original_normalized_candidate_bar_rows is None:
        return []
    base_rows = list(_original_normalized_candidate_bar_rows(rows))
    updated: list[tuple[str, float, str]] = []
    for index, base in enumerate(base_rows):
        label, value, display = base
        try:
            source = rows[index]
        except Exception:
            updated.append(base)
            continue
        total_value = _optional_float(getattr(source, "current_plus_model_combined_pnl", None))
        if total_value is None:
            total_value = _optional_float(getattr(source, "current_model_combined_pnl", None))
        if total_value is None:
            updated.append((label, value, display))
        else:
            updated.append((label, total_value, _money(total_value)))
    return updated


def _ensure_current_model_combo_column(frame: Any) -> None:
    tree = getattr(frame, "candidate_scenario_tree", None)
    if tree is None:
        return
    try:
        columns = list(tree["columns"])
    except Exception:
        return
    if _NEW_COLUMN not in columns:
        insert_at = columns.index("read") if "read" in columns else len(columns)
        columns.insert(insert_at, _NEW_COLUMN)
        tree.configure(columns=columns)
    _apply_scenario_column_headings(tree)


def _apply_scenario_column_headings(tree: Any) -> None:
    """Reapply every heading after changing Treeview columns.

    Tk can drop existing heading text when the column list is reconfigured. The
    new combo column is injected after the original table is built, so this pass
    restores all headers instead of only setting the new one.
    """

    try:
        columns = list(tree["columns"])
    except Exception:
        return
    for column in columns:
        label, width, anchor, stretch = _SCENARIO_COLUMN_SPECS.get(
            column,
            (column.replace("_", " ").title(), 105, tk.E, False),
        )
        try:
            tree.heading(column, text=label)
            tree.column(column, width=width, anchor=anchor, stretch=stretch)
        except tk.TclError:
            continue


def _repair_current_model_combo_rows(frame: Any) -> None:
    tree = getattr(frame, "candidate_scenario_tree", None)
    if tree is None:
        return
    try:
        columns = list(tree["columns"])
        if _NEW_COLUMN not in columns:
            return
    except Exception:
        return

    for item_id in tree.get_children(""):
        values = list(tree.item(item_id, "values"))
        if not values:
            continue
        mapped = _scenario_value_map(columns, values)
        combo_value = _current_model_combo_value(mapped)
        mapped[_NEW_COLUMN] = "--" if combo_value is None else _money(combo_value)
        if not str(mapped.get("read", "")).strip() and len(values) == len(_ORIGINAL_COLUMNS):
            mapped["read"] = str(values[-1])
        new_values = [mapped.get(column, "") for column in columns]
        tags = list(tree.item(item_id, "tags") or ())
        if combo_value is not None:
            tags = [tag for tag in tags if tag not in {"positive", "negative"}]
            tags.append("positive" if combo_value >= 0 else "negative")
        tree.item(item_id, values=new_values, tags=tuple(tags))


def _refresh_current_model_combo_bars(frame: Any) -> None:
    tree = getattr(frame, "candidate_scenario_tree", None)
    bars = getattr(frame, "candidate_bars", None)
    if tree is None or bars is None or not hasattr(bars, "set_rows"):
        return
    try:
        columns = list(tree["columns"])
        index = {column: position for position, column in enumerate(columns)}
    except Exception:
        return
    if _NEW_COLUMN not in index:
        return
    rows: list[tuple[str, float, str]] = []
    for item_id in tree.get_children(""):
        values = list(tree.item(item_id, "values"))
        if len(values) <= index[_NEW_COLUMN]:
            continue
        label = str(values[index.get("move", 0)])
        value = _parse_money(values[index[_NEW_COLUMN]])
        if value is None:
            value = _parse_money(values[index["model_combined"]]) if "model_combined" in index else None
        if value is None:
            continue
        rows.append((label, value, _money(value)))
    if rows:
        bars.set_rows(rows)


def _scenario_value_map(columns: list[str], values: list[Any]) -> dict[str, Any]:
    if len(values) == len(_ORIGINAL_COLUMNS):
        return dict(zip(_ORIGINAL_COLUMNS, values))
    mapped = dict(zip(columns, values))
    if _NEW_COLUMN in mapped and "read" in columns:
        new_value = str(mapped.get(_NEW_COLUMN, ""))
        read_value = str(mapped.get("read", ""))
        if new_value and not read_value and _parse_money(new_value) is None:
            mapped["read"] = new_value
    return mapped


def _current_model_combo_value(mapped: dict[str, Any]) -> float | None:
    current_combo = _parse_money(mapped.get("current_combined"))
    model_stock = _parse_money(mapped.get("model_stock"))
    if current_combo is None or model_stock is None:
        return None
    return current_combo + model_stock


def _portfolio_value_from_row(data: dict[str, Any]) -> float | None:
    current_combined = _optional_float(data.get("current_combined_pnl"))
    current_impact = _optional_float(data.get("current_portfolio_impact"))
    if current_combined is not None and current_impact not in {None, 0.0}:
        return current_combined / current_impact
    model_combined = _optional_float(data.get("model_combined_pnl"))
    model_impact = _optional_float(data.get("model_portfolio_impact"))
    if model_combined is not None and model_impact not in {None, 0.0}:
        return model_combined / model_impact
    return None


def _combined_total_read(base_read: str, total_combo: float, current_combined: float | None) -> str:
    label = "Current+model combo helps" if total_combo > 0 else "Current+model combo hurts" if total_combo < 0 else "Current+model combo flat"
    if current_combined is not None:
        delta = total_combo - current_combined
        label = f"{label}; model stock changes current combo by {_money(delta)}"
    if base_read and label.lower() not in base_read.lower():
        return f"{label}; {base_read}"
    return label


def _optional_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return _parse_money(value)


def _parse_money(value: Any) -> float | None:
    text = str(value or "").strip()
    if not text or text == "--":
        return None
    cleaned = re.sub(r"[^0-9.\-]", "", text)
    if not cleaned or cleaned in {"-", ".", "-."}:
        return None
    try:
        return float(cleaned)
    except ValueError:
        return None


def _money(value: float) -> str:
    return f"${value:,.2f}" if value >= 0 else f"-${abs(value):,.2f}"
