from __future__ import annotations

from tkinter import messagebox


def install_hyperliquid_notifications_fix(app_cls):
    """Make Hyperliquid account sync quiet, while keeping live-submit feedback accurate."""
    original_sync = app_cls.sync_hyperliquid_account

    def sync_hyperliquid_account_quiet(self):
        original_showinfo = messagebox.showinfo
        try:
            messagebox.showinfo = lambda *args, **kwargs: None
            return original_sync(self)
        finally:
            messagebox.showinfo = original_showinfo

    app_cls.sync_hyperliquid_account = sync_hyperliquid_account_quiet
