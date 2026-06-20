from __future__ import annotations

import argparse
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterable

try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:
    pass

DEFAULT_DATASET = "EQUS.MINI"
DEFAULT_SCHEMA = "ohlcv-1m"
ROOT = Path("data") / "market_screener" / "current"
DATABENTO_DEFINITIONS_PATH = ROOT / "databento_definitions_current.parquet"
DATABENTO_DATA_PATH = ROOT / "databento_tickerdata_parquet.parquet"
FMP_SEC_PATH = ROOT / "fmpsec_filings_parquet.parquet"
LEGACY_FMP_SEC_PATH = ROOT / "fmpsec_filings_parquet"
COMPARE_PATH = ROOT / "databento_vs_fmp_sec_compare.parquet"


def parse_time(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def window(args: argparse.Namespace, minutes: int) -> tuple[str, str]:
    end_text = args.end or os.getenv("DATABENTO_EQUITIES_QUERY_END_UTC", "")
    if end_text:
        end = parse_time(end_text)
    else:
        now = datetime.now(timezone.utc)
        end = now.replace(hour=20, minute=0, second=0, microsecond=0)
        if end > now:
            end -= timedelta(days=1)
    start_text = args.start or os.getenv("DATABENTO_EQUITIES_QUERY_START_UTC", "")
    start = parse_time(start_text) if start_text else end - timedelta(minutes=minutes)
    return start.isoformat(), end.isoformat()


def db_client():
    import databento as db
    key = os.getenv("DATABENTO_API_KEY", "").strip()
    if not key:
        raise RuntimeError("Missing DATABENTO_API_KEY")
    return db.Historical(key=key)


def to_frame(store):
    frame = store.to_df()
    return frame.reset_index() if hasattr(frame, "reset_index") else frame


def write_parquet(frame, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    frame.to_parquet(path, index=False)
    print(f"Saved {len(frame)} row(s) -> {path}")


def read_parquet(path: Path):
    import pandas as pd
    if not path.exists():
        raise RuntimeError(f"Missing parquet: {path}")
    return pd.read_parquet(path)


def first_col(frame, names: Iterable[str]) -> str | None:
    cols = {str(c).lower(): str(c) for c in frame.columns}
    for name in names:
        if name.lower() in cols:
            return cols[name.lower()]
    return None


def build_definitions(args: argparse.Namespace) -> None:
    dataset = args.dataset or os.getenv("DATABENTO_EQUITIES_DATASET", DEFAULT_DATASET)
    start, end = window(args, args.definition_lookback_minutes)
    print(f"[databento-definitions] dataset={dataset} start={start} end={end}")
    store = db_client().timeseries.get_range(
        dataset=dataset,
        schema="definition",
        start=start,
        end=end,
        limit=args.definition_row_limit,
    )
    write_parquet(to_frame(store), Path(args.databento_definitions_path))


def databento_symbols(args: argparse.Namespace) -> list[str]:
    frame = read_parquet(Path(args.databento_definitions_path))
    col = first_col(frame, ("raw_symbol", "symbol", "ticker"))
    if not col:
        raise RuntimeError(f"No Databento symbol column found. Columns={list(frame.columns)}")
    symbols: list[str] = []
    seen: set[str] = set()
    for raw in frame[col].dropna().astype(str):
        sym = raw.strip().upper()
        if sym and sym not in seen:
            symbols.append(sym)
            seen.add(sym)
        if args.symbol_limit and len(symbols) >= args.symbol_limit:
            break
    print(f"Loaded {len(symbols)} Databento-native symbol(s) from {args.databento_definitions_path}")
    return symbols


def build_data(args: argparse.Namespace) -> None:
    dataset = args.dataset or os.getenv("DATABENTO_EQUITIES_DATASET", DEFAULT_DATASET)
    schema = args.schema or os.getenv("DATABENTO_EQUITIES_SCHEMA", DEFAULT_SCHEMA)
    symbols = databento_symbols(args)
    if not symbols:
        raise RuntimeError("Databento definitions produced zero symbols")
    start, end = window(args, args.data_lookback_minutes)
    print(f"[databento-data] dataset={dataset} schema={schema} start={start} end={end} symbols={len(symbols)}")
    store = db_client().timeseries.get_range(
        dataset=dataset,
        schema=schema,
        symbols=symbols,
        stype_in="raw_symbol",
        start=start,
        end=end,
        limit=args.data_row_limit,
    )
    write_parquet(to_frame(store), Path(args.databento_data_path))


def compare(args: argparse.Namespace) -> None:
    import pandas as pd
    dbf = read_parquet(Path(args.databento_data_path))
    fmp_path = Path(args.fmp_sec_path)
    if not fmp_path.exists() and Path(args.legacy_fmp_sec_path).exists():
        fmp_path = Path(args.legacy_fmp_sec_path)
    fmp = read_parquet(fmp_path)
    db_col = first_col(dbf, ("raw_symbol", "symbol", "ticker"))
    fmp_col = first_col(fmp, ("symbol",))
    if not db_col or not fmp_col:
        raise RuntimeError(f"Missing symbol columns. databento={list(dbf.columns)} fmp_sec={list(fmp.columns)}")
    db_symbols = set(dbf[db_col].dropna().astype(str).str.upper())
    fmp_symbols = set(fmp[fmp_col].dropna().astype(str).str.upper())
    out = pd.DataFrame([
        {"metric": "databento_rows", "value": len(dbf)},
        {"metric": "databento_symbols", "value": len(db_symbols)},
        {"metric": "fmp_sec_rows", "value": len(fmp)},
        {"metric": "fmp_sec_symbols", "value": len(fmp_symbols)},
        {"metric": "overlap_symbols", "value": len(db_symbols & fmp_symbols)},
        {"metric": "only_databento_symbols", "value": len(db_symbols - fmp_symbols)},
        {"metric": "only_fmp_sec_symbols", "value": len(fmp_symbols - db_symbols)},
    ])
    write_parquet(out, Path(args.compare_path))
    print(out.to_string(index=False))


def main() -> None:
    parser = argparse.ArgumentParser(description="Build Databento-native parquet, then compare to FMP/SEC parquet.")
    parser.add_argument("command", choices=("definitions", "data", "build", "compare"))
    parser.add_argument("--dataset", default=None)
    parser.add_argument("--schema", default=None)
    parser.add_argument("--start", default=None)
    parser.add_argument("--end", default=None)
    parser.add_argument("--definition-lookback-minutes", type=int, default=10080)
    parser.add_argument("--data-lookback-minutes", type=int, default=390)
    parser.add_argument("--definition-row-limit", type=int, default=1_000_000)
    parser.add_argument("--data-row-limit", type=int, default=1_000_000)
    parser.add_argument("--symbol-limit", type=int, default=10000)
    parser.add_argument("--databento-definitions-path", default=str(DATABENTO_DEFINITIONS_PATH))
    parser.add_argument("--databento-data-path", default=str(DATABENTO_DATA_PATH))
    parser.add_argument("--fmp-sec-path", default=str(FMP_SEC_PATH))
    parser.add_argument("--legacy-fmp-sec-path", default=str(LEGACY_FMP_SEC_PATH))
    parser.add_argument("--compare-path", default=str(COMPARE_PATH))
    args = parser.parse_args()
    if args.command in {"definitions", "build"}:
        build_definitions(args)
    if args.command in {"data", "build"}:
        build_data(args)
    if args.command == "compare":
        compare(args)


if __name__ == "__main__":
    main()
