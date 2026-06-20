from __future__ import annotations


def normalize_symbol(value: object) -> str:
    """Return the canonical app symbol form used for parquet joins."""
    return str(value or "").strip().upper().replace("/", ".")
