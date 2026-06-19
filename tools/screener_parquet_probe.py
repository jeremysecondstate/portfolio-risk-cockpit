from __future__ import annotations

import argparse

try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:
    pass

from app.analytics.market_screener import (
    build_market_screener_records,
    market_screener_diagnostics_detail_lines,
    market_screener_snapshot_from_records,
    merge_market_data_records_into_screener_records,
)
from app.data.market_data_provider import configured_market_data_provider
from app.data.market_screener_parquet_store import MarketScreenerParquetStore
from app.data.market_universe import MarketUniverseEntry


DEFAULT_SYMBOLS = "MSFT AAPL NVDA GOOG GOOGL AMZN META BRK.B LLY AVGO TSLA JPM V".split()

PHASES = {
    "profile": ("profile_classification", "profile_classification"),
    "quote": ("quote_tape", "quote_tape"),
    "fundamentals": ("fundamentals", "fundamentals"),
    "all": ("quote_fundamentals", None),
}


def symbols_from_args(values: list[str]) -> tuple[str, ...]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        for raw in value.replace(",", " ").split():
            symbol = raw.strip().upper().replace("/", ".")
            if symbol and symbol not in seen:
                seen.add(symbol)
                result.append(symbol)
    return tuple(result)


def base_records(symbols: tuple[str, ...]):
    return build_market_screener_records(
        [MarketUniverseEntry(symbol=symbol, source="terminal parquet probe") for symbol in symbols]
    )


def print_provider_snapshot(label: str, snapshot) -> None:
    print(f"\n[{label}] provider records={len(snapshot.records)} fetched_at={snapshot.fetched_at}")

    for status in snapshot.statuses:
        print(f"- {status.source}: {status.status} :: {status.message}")

    for error in tuple(snapshot.errors)[:12]:
        print(f"! {error}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--phase", choices=PHASES, required=True)
    parser.add_argument("--symbols", nargs="*", default=DEFAULT_SYMBOLS)
    parser.add_argument("--reset", action="store_true")
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--max-symbols", type=int, default=100)
    parser.add_argument("--batch-size", type=int, default=25)
    args = parser.parse_args()

    symbols = symbols_from_args(args.symbols) or tuple(DEFAULT_SYMBOLS)
    store = MarketScreenerParquetStore()

    if args.reset or not store.current_exists():
        records = list(base_records(symbols))
    else:
        wanted = set(symbols)
        loaded = [row for row in store.load_current() if (row.symbol or "").upper() in wanted]
        records = loaded or list(base_records(symbols))

    provider = configured_market_data_provider(
        include_fallback_provider=False,
        fmp_symbol_limit=args.max_symbols,
        databento_symbol_limit=args.max_symbols,
        cache_ttl_seconds=0 if args.force else None,
        batch_size=args.batch_size,
    )

    method_name, family = PHASES[args.phase]
    snapshot = getattr(provider, method_name)(
        symbols,
        force_refresh=args.force,
        max_symbols=args.max_symbols,
    )

    print_provider_snapshot(args.phase, snapshot)

    records = merge_market_data_records_into_screener_records(
        records,
        snapshot.records,
        fetched_at=snapshot.fetched_at,
        family=family,
    )

    records = sorted(records, key=lambda row: (-(row.market_cap or 0), row.symbol or ""))
    store.save_current(records)

    final = market_screener_snapshot_from_records(records, fetched_at=snapshot.fetched_at)

    print(f"\nSaved {len(final.records)} row(s) -> {store.current_path}")

    interesting_prefixes = (
        "Total screener rows",
        "Rows with profile/classification",
        "Rows with price",
        "Rows with volume",
        "Rows with avg volume",
        "Rows with fundamentals",
        "Rows with market cap",
        "Rows missing",
        "Major U.S. large-cap symbols",
    )

    for line in market_screener_diagnostics_detail_lines(final.diagnostics):
        if line.startswith(interesting_prefixes):
            print(line)


if __name__ == "__main__":
    main()