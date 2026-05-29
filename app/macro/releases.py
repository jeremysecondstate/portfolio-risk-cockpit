from __future__ import annotations

from app.macro.analysis import format_macro_report
from app.macro.models import MacroSnapshot
from app.macro.sources import collect_macro_snapshot


def fetch_macro_release_snapshot(*, force_refresh: bool = False, timeout_seconds: int = 12) -> MacroSnapshot:
    return collect_macro_snapshot(force_refresh=force_refresh, timeout_seconds=timeout_seconds)


def build_macro_report(*, force_refresh: bool = False, timeout_seconds: int = 12) -> str:
    return format_macro_report(fetch_macro_release_snapshot(force_refresh=force_refresh, timeout_seconds=timeout_seconds))
