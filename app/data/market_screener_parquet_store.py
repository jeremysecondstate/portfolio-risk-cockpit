from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any, Iterable, Mapping

from app.analytics.market_screener import MarketScreenerRecord


MARKET_SCREENER_PARQUET_ROOT_ENV = "MARKET_SCREENER_PARQUET_ROOT"
MARKET_SCREENER_USE_PARQUET_SNAPSHOT_ENV = "MARKET_SCREENER_USE_PARQUET_SNAPSHOT"
MARKET_SCREENER_PARQUET_HISTORY_ENABLED_ENV = "MARKET_SCREENER_PARQUET_HISTORY_ENABLED"
DEFAULT_MARKET_SCREENER_PARQUET_ROOT = Path("data") / "market_screener"
CURRENT_SNAPSHOT_RELATIVE_PATH = Path("current") / "fmpsec_filings_parquet"

_JSON_COLUMNS = {"signals", "risk_flags", "sources", "source_links", "field_provenance"}
_FLOAT_COLUMNS = {
    "price",
    "market_cap",
    "volume",
    "avg_volume",
    "change_percent",
    "pe_ratio",
    "eps",
    "revenue_growth",
    "shares_float",
    "shares_outstanding",
    "portfolio_quantity",
    "portfolio_average_cost",
    "portfolio_market_value",
    "portfolio_unrealized_pnl",
    "portfolio_weight",
    "market_cap_rank_value",
}
_BOOL_COLUMNS = {"market_cap_rank_trusted", "is_adr", "is_etf", "is_fund", "is_otc"}
_RECORD_COLUMNS = (
    "symbol",
    "company_name",
    "exchange",
    "sector",
    "industry",
    "price",
    "market_cap",
    "volume",
    "avg_volume",
    "change_percent",
    "pe_ratio",
    "eps",
    "revenue_growth",
    "shares_float",
    "shares_outstanding",
    "next_earnings_date",
    "recent_filing_date",
    "recent_filing_type",
    "signals",
    "risk_flags",
    "sources",
    "source_links",
    "fetched_at",
    "cik",
    "source_excerpt",
    "portfolio_quantity",
    "portfolio_average_cost",
    "portfolio_market_value",
    "portfolio_unrealized_pnl",
    "portfolio_weight",
    "field_provenance",
    "market_cap_currency",
    "market_cap_rank_value",
    "market_cap_rank_currency",
    "market_cap_rank_trusted",
    "market_cap_rank_reason",
    "instrument_type",
    "country",
    "is_adr",
    "is_etf",
    "is_fund",
    "is_otc",
)


class MarketScreenerParquetStoreError(RuntimeError):
    """Raised when the Market Screener Parquet snapshot cannot be read or written."""


class MarketScreenerParquetStore:
    def __init__(self, root: str | Path | None = None) -> None:
        configured_root = root or os.getenv(MARKET_SCREENER_PARQUET_ROOT_ENV, "")
        self.root = Path(configured_root) if configured_root else DEFAULT_MARKET_SCREENER_PARQUET_ROOT
        self.current_path = self.root / CURRENT_SNAPSHOT_RELATIVE_PATH

    def current_exists(self) -> bool:
        return self.current_path.is_file()

    def load_current(self) -> list[MarketScreenerRecord]:
        if not self.current_exists():
            return []
        _pa, pq = _require_pyarrow()
        try:
            table = pq.read_table(self.current_path)
        except Exception as exc:
            raise MarketScreenerParquetStoreError(f"Could not read Market Screener snapshot {self.current_path}: {exc}") from exc
        if "symbol" not in table.column_names:
            raise MarketScreenerParquetStoreError(f"Malformed Market Screener snapshot {self.current_path}: missing symbol column.")
        try:
            return [_record_from_row(row) for row in table.to_pylist() if _row_has_symbol(row)]
        except Exception as exc:
            raise MarketScreenerParquetStoreError(f"Could not decode Market Screener snapshot {self.current_path}: {exc}") from exc

    def save_current(self, records: Iterable[MarketScreenerRecord]) -> None:
        pa, pq = _require_pyarrow()
        rows = [_record_to_row(record) for record in records]
        table = pa.Table.from_pylist(rows, schema=_schema(pa))
        self.current_path.parent.mkdir(parents=True, exist_ok=True)
        temp_path: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                prefix="screener_current.",
                suffix=".tmp.parquet",
                dir=self.current_path.parent,
                delete=False,
            ) as handle:
                temp_path = Path(handle.name)
            pq.write_table(table, temp_path)
            os.replace(temp_path, self.current_path)
            temp_path = None
        except Exception as exc:
            raise MarketScreenerParquetStoreError(f"Could not write Market Screener snapshot {self.current_path}: {exc}") from exc
        finally:
            if temp_path is not None:
                try:
                    temp_path.unlink(missing_ok=True)
                except OSError:
                    pass


def market_screener_parquet_snapshot_enabled() -> bool:
    return _env_bool(MARKET_SCREENER_USE_PARQUET_SNAPSHOT_ENV, default=True)


def market_screener_parquet_history_enabled() -> bool:
    return _env_bool(MARKET_SCREENER_PARQUET_HISTORY_ENABLED_ENV, default=False)


def parquet_engine_available() -> bool:
    try:
        _require_pyarrow()
    except MarketScreenerParquetStoreError:
        return False
    return True


def _require_pyarrow() -> tuple[Any, Any]:
    try:
        import pyarrow as pa
        import pyarrow.parquet as pq
    except ImportError as exc:
        raise MarketScreenerParquetStoreError(
            "pyarrow is required for Market Screener Parquet snapshots. Install project requirements or set "
            f"{MARKET_SCREENER_USE_PARQUET_SNAPSHOT_ENV}=false."
        ) from exc
    return pa, pq


def _schema(pa: Any) -> Any:
    fields = []
    for column in _RECORD_COLUMNS:
        if column in _FLOAT_COLUMNS:
            column_type = pa.float64()
        elif column in _BOOL_COLUMNS:
            column_type = pa.bool_()
        else:
            column_type = pa.string()
        fields.append((column, column_type))
    return pa.schema(fields)


def _record_to_row(record: MarketScreenerRecord) -> dict[str, Any]:
    payload = record.to_dict()
    row: dict[str, Any] = {}
    for column in _RECORD_COLUMNS:
        value = payload.get(column)
        if column in _JSON_COLUMNS:
            row[column] = json.dumps(_json_safe(value), ensure_ascii=True, sort_keys=True)
        elif column in _FLOAT_COLUMNS:
            row[column] = _optional_float(value)
        elif column in _BOOL_COLUMNS:
            row[column] = _optional_bool(value)
        else:
            row[column] = _optional_string(value)
    return row


def _record_from_row(row: Mapping[str, Any]) -> MarketScreenerRecord:
    payload: dict[str, Any] = {}
    for column in _RECORD_COLUMNS:
        if column not in row:
            continue
        value = row.get(column)
        payload[column] = _json_from_string(value) if column in _JSON_COLUMNS else value
    return MarketScreenerRecord.from_dict(payload)


def _row_has_symbol(row: Mapping[str, Any]) -> bool:
    return bool(str(row.get("symbol") or "").strip())


def _json_safe(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_json_safe(item) for item in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    if hasattr(value, "to_dict") and callable(value.to_dict):
        return _json_safe(value.to_dict())
    if hasattr(value, "__dict__"):
        return _json_safe({key: item for key, item in vars(value).items() if not key.startswith("_")})
    return str(value)


def _json_from_string(value: Any) -> Any:
    if not isinstance(value, str):
        return value
    text = value.strip()
    if not text:
        return ()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return value


def _optional_string(value: Any) -> str | None:
    text = str(value or "").strip()
    return text or None


def _optional_float(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _optional_bool(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    if value in (None, ""):
        return None
    text = str(value).strip().lower()
    if text in {"1", "true", "t", "yes", "y", "on"}:
        return True
    if text in {"0", "false", "f", "no", "n", "off"}:
        return False
    return None


def _env_bool(name: str, *, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return str(value).strip().lower() not in {"0", "false", "no", "n", "off"}
