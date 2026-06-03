from __future__ import annotations

from collections.abc import Callable
from tkinter import messagebox


def install_hyperliquid_notifications(app_cls):
    """Quiet Connect Hyperliquid, but keep a useful LIVE Submit result popup."""
    original_sync = app_cls.sync_hyperliquid_account
    original_submit = app_cls.show_hyperliquid_live_submit_safety_review

    def _without_info_popup(action: Callable[[], object]) -> object:
        original_showinfo = messagebox.showinfo
        try:
            messagebox.showinfo = lambda *args, **kwargs: None
            return action()
        finally:
            messagebox.showinfo = original_showinfo

    def sync_hyperliquid_account_quiet(self):
        return _without_info_popup(lambda: original_sync(self))

    def show_hyperliquid_live_submit_with_result_popup(self):
        original_submit(self)

        status_var = getattr(self, "hyperliquid_status_var", None)
        status = status_var.get() if status_var is not None else ""
        if status != "Hyperliquid: submit attempted":
            return

        messagebox.showinfo(
            "Hyperliquid live submit",
            "Hyperliquid LIVE Submit was sent.\n\n"
            "The exchange response is shown in Analysis + Instructions, and the "
            "Hyperliquid account snapshot was refreshed in the portfolio panel.",
        )

    app_cls.sync_hyperliquid_account = sync_hyperliquid_account_quiet
    app_cls.show_hyperliquid_live_submit_safety_review = show_hyperliquid_live_submit_with_result_popup
