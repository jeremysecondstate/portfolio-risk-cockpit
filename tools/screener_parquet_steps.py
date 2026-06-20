from __future__ import annotations

import argparse
import os
import time
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any, Callable, Iterable, Mapping

import requests

try:
    from dotenv import load_dotenv

    load_dotenv()
except Exception:
    pass

from app.analytics.market_screener import MarketScreenerRecord, merge_market_data_records_into_screener_records
from app.data.databento_provider import configured_databento_equities_provider
from app.data.market_screener_parquet_store import MarketScreenerParquetStore


FMP_BASE_URL = os.getenv("FMP_BASE_URL", "https://financialmodelingprep.com/stable").rstrip("/")
FMP_API_KEY = os.getenv("FMP_API_KEY", "").strip()
DEFAULT_SLEEP_SECONDS = 0.05
DEFAULT_BATCH_SIZE = 100
DEFAULT_SAVE_EVERY = 100


@dataclass(frozen=True)
class StepContext:
    sleep_seconds: float
    batch_size: int
    save_every: int
    force: bool = False


def clean_symbol(value: object) -> str:
    return str(value or "").strip().upper().replace("/", ".")


def chunks(items: Iterable[str], size: int) -> Iterable[tuple[str, ...]]:
    batch: list[str] = []
    for item in items:
        clean = clean_symbol(item)
        if not clean:
            continue
        batch.append(clean)
        if len(batch) >= max(1, size):
            yield tuple(batch)
            batch = []
    if batch:
        yield tuple(batch)


def fmp_get(endpoint: str, params: Mapping[str, Any] | None = None) -> Any:
    if not FMP_API_KEY:
        raise RuntimeError("Missing FMP_API_KEY")

    request_params = dict(params or {})
    request_params["apikey"] = FMP_API_KEY

    response = requests.get(
        f"{FMP_BASE_URL}/{endpoint.strip('/')}",
        params=request_params,
        headers={"User-Agent": "portfolio-risk-cockpit/1.0"},
        timeout=30,
    )
    if response.status_code != 200:
        raise RuntimeError(f"FMP {endpoint} returned HTTP {response.status_code}: {response.text[:300]}")
    try:
        return response.json()
    except ValueError as exc:
        raise RuntimeError(f"FMP {endpoint} returned non-JSON response") from exc


def rows_from_payload(payload: Any) -> list[Mapping[str, Any]]:
    if isinstance(payload, list):
        return [row for row in payload if isinstance(row, Mapping)]
    if isinstance(payload, Mapping):
        for key in ("data", "results", "records", "historical"):
            value = payload.get(key)
            if isinstance(value, list):
                return [row for row in value if isinstance(row, Mapping)]
        return [payload]
    return []


def first_row(payload: Any) -> Mapping[str, Any] | None:
    rows = rows_from_payload(payload)
    return rows[0] if rows else None


def optional_float(value: Any) -> float | None:
    if value in (None, ""):
        return None
    text = str(value).replace(",", "").replace("$", "").strip()
    if text.endswith("%"):
        text = text[:-1]
    try:
        return float(text)
    except ValueError:
        return None


def percent_value(value: Any) -> float | None:
    parsed = optional_float(value)
    if parsed is None:
        return None
    return parsed * 100 if abs(parsed) <= 1 else parsed


def row_has_values(row: Mapping[str, Any], fields: Iterable[str]) -> bool:
    return all(row.get(field) not in (None, "", [], ()) for field in fields)


def load_rows() -> dict[str, dict[str, Any]]:
    store = MarketScreenerParquetStore()
    if not store.current_exists():
        return {}
    rows: dict[str, dict[str, Any]] = {}
    for record in store.load_current():
        payload = record.to_dict()
        symbol = clean_symbol(payload.get("symbol"))
        if symbol:
            payload["symbol"] = symbol
            rows[symbol] = payload
    return rows


def save_rows(rows: Mapping[str, Mapping[str, Any]]) -> None:
    store = MarketScreenerParquetStore()
    records = [
        MarketScreenerRecord.from_dict(dict(row))
        for _symbol, row in sorted(rows.items())
        if clean_symbol(row.get("symbol"))
    ]
    records.sort(key=lambda row: (-(row.market_cap or 0), row.symbol or ""))
    store.save_current(records)
    print(f"Saved {len(records)} rows -> {store.current_path}")


def save_checkpoint(rows: Mapping[str, Mapping[str, Any]], processed: int, context: StepContext) -> None:
    if context.save_every > 0 and processed > 0 and processed % context.save_every == 0:
        save_rows(rows)


def add_source(row: dict[str, Any], source: str) -> None:
    raw_sources = row.get("sources") or []
    sources = list(raw_sources) if isinstance(raw_sources, (list, tuple)) else [str(raw_sources)]
    if source not in sources:
        sources.append(source)
    row["sources"] = sources


def add_source_link(row: dict[str, Any], link: Any) -> None:
    text = str(link or "").strip()
    if not text:
        return
    raw_links = row.get("source_links") or []
    links = list(raw_links) if isinstance(raw_links, (list, tuple)) else [str(raw_links)]
    if text not in links:
        links.append(text)
    row["source_links"] = links


def update_if_present(row: dict[str, Any], field: str, value: Any, source: str) -> bool:
    if value in (None, "", [], ()):  # keep existing value when provider has no data
        return False
    row[field] = value
    add_source(row, source)
    return True


def current_symbols(limit: int | None = None) -> tuple[str, ...]:
    symbols = tuple(load_rows().keys())
    return symbols[:limit] if limit else symbols


def rows_to_records(rows: Mapping[str, Mapping[str, Any]]) -> list[MarketScreenerRecord]:
    return [MarketScreenerRecord.from_dict(dict(row)) for row in rows.values() if clean_symbol(row.get("symbol"))]


def step_00_init_universe(limit: int) -> None:
    print(f"Fetching FMP stock-list universe, limit={limit}...")
    payload = fmp_get("stock-list")
    rows = load_rows()
    added = 0

    for item in rows_from_payload(payload):
        symbol = clean_symbol(item.get("symbol") or item.get("ticker"))
        if not symbol:
            continue
        row = rows.setdefault(symbol, {"symbol": symbol})
        changed = False
        changed |= update_if_present(row, "symbol", symbol, "FMP stock-list")
        changed |= update_if_present(row, "company_name", item.get("name") or item.get("companyName"), "FMP stock-list")
        changed |= update_if_present(row, "exchange", item.get("exchangeShortName") or item.get("exchange"), "FMP stock-list")
        if changed:
            added += 1
        if added >= limit:
            break

    save_rows(rows)
    print(f"[init] universe rows added/updated: {added}")


def step_01_profile(symbols: Iterable[str], context: StepContext) -> None:
    """Batch FMP profile/classification for company, exchange, sector, industry, and CIK."""
    rows = load_rows()
    updated = 0
    processed = 0
    profile_fields = ("company_name", "exchange", "sector", "industry")

    for batch_index, batch in enumerate(chunks(symbols, context.batch_size), start=1):
        needed = tuple(
            symbol
            for symbol in batch
            if context.force or not row_has_values(rows.get(symbol, {}), profile_fields)
        )
        if not needed:
            processed += len(batch)
            continue

        print(f"[profile] batch {batch_index}: {len(needed)} symbol(s)")
        try:
            payload = fmp_get("profile", {"symbol": ",".join(needed)})
            items = rows_from_payload(payload)

            needed_set = {clean_symbol(symbol) for symbol in needed}
            returned_symbols = {
                clean_symbol(item.get("symbol") or item.get("ticker"))
                for item in items
                if clean_symbol(item.get("symbol") or item.get("ticker"))
            }
            missing_symbols = tuple(symbol for symbol in needed if clean_symbol(symbol) not in returned_symbols)

            if missing_symbols and len(needed) > 1:
                print(
                    f"[profile] batch returned {len(returned_symbols)}/{len(needed_set)} symbol(s); "
                    f"falling back for {len(missing_symbols)} missing symbol(s)"
                )
                for symbol in missing_symbols:
                    one = first_row(fmp_get("profile", {"symbol": symbol}))
                    if one:
                        items.append(one)
                    time.sleep(context.sleep_seconds)

            for item in items:
                symbol = clean_symbol(item.get("symbol") or item.get("ticker"))
                if not symbol:
                    continue
                row = rows.setdefault(symbol, {"symbol": symbol})
                changed = False
                changed |= update_if_present(row, "company_name", item.get("companyName") or item.get("company_name") or item.get("name"), "FMP profile")
                changed |= update_if_present(row, "exchange", item.get("exchangeShortName") or item.get("exchange"), "FMP profile")
                changed |= update_if_present(row, "sector", item.get("sector"), "FMP profile")
                changed |= update_if_present(row, "industry", item.get("industry"), "FMP profile")
                changed |= update_if_present(row, "cik", item.get("cik") or item.get("CIK"), "FMP profile")
                changed |= update_if_present(row, "market_cap", optional_float(item.get("marketCap")), "FMP profile")
                if changed:
                    updated += 1
        except Exception as exc:
            print(f"! profile batch {batch_index}: {exc}")

        processed += len(batch)
        save_checkpoint(rows, processed, context)
        time.sleep(context.sleep_seconds)

    save_rows(rows)
    print(f"[profile] updated rows: {updated}")


def step_02_batch_quote(symbols: Iterable[str], context: StepContext) -> None:
    """FMP batch quote fallback for price, volume, avg volume, change %, P/E, EPS, and market cap."""
    rows = load_rows()
    updated = 0
    processed = 0
    quote_fields = ("price", "volume", "change_percent")

    for batch_index, batch in enumerate(chunks(symbols, context.batch_size), start=1):
        needed = tuple(
            symbol
            for symbol in batch
            if context.force or not row_has_values(rows.get(symbol, {}), quote_fields)
        )
        if not needed:
            processed += len(batch)
            continue

        print(f"[batch-quote] batch {batch_index}: {len(needed)} symbol(s)")
        try:
            payload = fmp_get("batch-quote", {"symbols": ",".join(needed)})
            items = rows_from_payload(payload)
            for item in items:
                symbol = clean_symbol(item.get("symbol") or item.get("ticker"))
                if not symbol:
                    continue
                row = rows.setdefault(symbol, {"symbol": symbol})
                changed = False
                changed |= update_if_present(row, "price", optional_float(item.get("price")), "FMP batch quote")
                changed |= update_if_present(row, "volume", optional_float(item.get("volume")), "FMP batch quote")
                changed |= update_if_present(row, "avg_volume", optional_float(item.get("avgVolume") or item.get("averageVolume")), "FMP batch quote")
                changed |= update_if_present(row, "change_percent", percent_value(item.get("changesPercentage") or item.get("changePercentage") or item.get("changePercent")), "FMP batch quote")
                changed |= update_if_present(row, "eps", optional_float(item.get("eps") or item.get("epsTTM")), "FMP batch quote")
                changed |= update_if_present(row, "pe_ratio", optional_float(item.get("pe") or item.get("peRatio")), "FMP batch quote")
                changed |= update_if_present(row, "market_cap", optional_float(item.get("marketCap")), "FMP batch quote")
                if changed:
                    updated += 1
        except Exception as exc:
            print(f"! batch-quote batch {batch_index}: {exc}")

        processed += len(batch)
        save_checkpoint(rows, processed, context)
        time.sleep(context.sleep_seconds)

    save_rows(rows)
    print(f"[batch-quote] updated rows: {updated}")


def step_02_quote(symbols: Iterable[str], context: StepContext) -> None:
    """One-symbol FMP quote fallback. Prefer batch_quote or databento_prices for production refreshes."""
    rows = load_rows()
    updated = 0
    quote_fields = ("price", "volume", "change_percent")

    for index, symbol in enumerate(symbols, start=1):
        symbol = clean_symbol(symbol)
        if not symbol:
            continue
        row = rows.setdefault(symbol, {"symbol": symbol})
        if not context.force and row_has_values(row, quote_fields):
            continue

        print(f"[quote] {index}: {symbol}")
        try:
            item = first_row(fmp_get("quote", {"symbol": symbol}))
            if not item:
                continue
            changed = False
            changed |= update_if_present(row, "price", optional_float(item.get("price")), "FMP quote")
            changed |= update_if_present(row, "volume", optional_float(item.get("volume")), "FMP quote")
            changed |= update_if_present(row, "avg_volume", optional_float(item.get("avgVolume") or item.get("averageVolume")), "FMP quote")
            changed |= update_if_present(row, "change_percent", percent_value(item.get("changesPercentage") or item.get("changePercentage") or item.get("changePercent")), "FMP quote")
            changed |= update_if_present(row, "eps", optional_float(item.get("eps") or item.get("epsTTM")), "FMP quote")
            changed |= update_if_present(row, "pe_ratio", optional_float(item.get("pe") or item.get("peRatio")), "FMP quote")
            changed |= update_if_present(row, "market_cap", optional_float(item.get("marketCap")), "FMP quote")
            if changed:
                updated += 1
        except Exception as exc:
            print(f"! quote {symbol}: {exc}")
        save_checkpoint(rows, index, context)
        time.sleep(context.sleep_seconds)

    save_rows(rows)
    print(f"[quote] updated rows: {updated}")


def step_03_historical_eod(symbols: Iterable[str], context: StepContext) -> None:
    """FMP historical EOD fallback for price, volume, avg volume, and change %."""
    rows = load_rows()
    updated = 0
    historical_fields = ("price", "volume", "avg_volume", "change_percent")

    for index, symbol in enumerate(symbols, start=1):
        symbol = clean_symbol(symbol)
        if not symbol:
            continue
        row = rows.setdefault(symbol, {"symbol": symbol})
        if not context.force and row_has_values(row, historical_fields):
            continue

        print(f"[historical-eod] {index}: {symbol}")
        try:
            history = rows_from_payload(fmp_get("historical-price-eod/full", {"symbol": symbol}))
            if not history:
                continue
            history = sorted(history, key=lambda item: str(item.get("date") or ""), reverse=True)
            latest = history[0]
            previous = history[1] if len(history) > 1 else {}
            close = optional_float(latest.get("close") or latest.get("adjClose") or latest.get("price"))
            prior_close = optional_float(previous.get("close") or previous.get("adjClose") or previous.get("price"))
            volume = optional_float(latest.get("volume"))
            volumes = [parsed for item in history[:30] if (parsed := optional_float(item.get("volume"))) is not None]
            avg_volume = sum(volumes) / len(volumes) if volumes else None
            change_percent = None
            if close is not None and prior_close not in (None, 0):
                change_percent = ((close - prior_close) / abs(prior_close)) * 100

            changed = False
            changed |= update_if_present(row, "price", close, "FMP historical EOD")
            changed |= update_if_present(row, "volume", volume, "FMP historical EOD")
            changed |= update_if_present(row, "avg_volume", avg_volume, "FMP historical EOD")
            changed |= update_if_present(row, "change_percent", change_percent, "FMP historical EOD")
            if changed:
                updated += 1
        except Exception as exc:
            print(f"! historical-eod {symbol}: {exc}")
        save_checkpoint(rows, index, context)
        time.sleep(context.sleep_seconds)

    save_rows(rows)
    print(f"[historical-eod] updated rows: {updated}")


def step_04_market_cap(symbols: Iterable[str], context: StepContext) -> None:
    rows = load_rows()
    updated = 0

    for index, symbol in enumerate(symbols, start=1):
        symbol = clean_symbol(symbol)
        if not symbol:
            continue
        row = rows.setdefault(symbol, {"symbol": symbol})
        if not context.force and row_has_values(row, ("market_cap",)):
            continue

        print(f"[market-cap] {index}: {symbol}")
        try:
            item = first_row(fmp_get("market-capitalization", {"symbol": symbol}))
            if not item:
                continue
            if update_if_present(row, "market_cap", optional_float(item.get("marketCap") or item.get("market_cap")), "FMP market cap"):
                updated += 1
        except Exception as exc:
            print(f"! market-cap {symbol}: {exc}")
        save_checkpoint(rows, index, context)
        time.sleep(context.sleep_seconds)

    save_rows(rows)
    print(f"[market-cap] updated rows: {updated}")


def step_05_key_metrics(symbols: Iterable[str], context: StepContext) -> None:
    rows = load_rows()
    updated = 0
    metrics_fields = ("pe_ratio", "market_cap")

    for index, symbol in enumerate(symbols, start=1):
        symbol = clean_symbol(symbol)
        if not symbol:
            continue
        row = rows.setdefault(symbol, {"symbol": symbol})
        if not context.force and row_has_values(row, metrics_fields):
            continue

        print(f"[key-metrics] {index}: {symbol}")
        try:
            item = first_row(fmp_get("key-metrics", {"symbol": symbol}))
            if not item:
                continue
            changed = False
            changed |= update_if_present(row, "pe_ratio", optional_float(item.get("peRatio") or item.get("peRatioTTM")), "FMP key metrics")
            changed |= update_if_present(row, "market_cap", optional_float(item.get("marketCap") or item.get("marketCapTTM")), "FMP key metrics")
            if changed:
                updated += 1
        except Exception as exc:
            print(f"! key-metrics {symbol}: {exc}")
        save_checkpoint(rows, index, context)
        time.sleep(context.sleep_seconds)

    save_rows(rows)
    print(f"[key-metrics] updated rows: {updated}")


def step_06_income_statement(symbols: Iterable[str], context: StepContext) -> None:
    rows = load_rows()
    updated = 0
    income_fields = ("eps", "shares_outstanding")

    for index, symbol in enumerate(symbols, start=1):
        symbol = clean_symbol(symbol)
        if not symbol:
            continue
        row = rows.setdefault(symbol, {"symbol": symbol})
        if not context.force and row_has_values(row, income_fields):
            continue

        print(f"[income-statement] {index}: {symbol}")
        try:
            item = first_row(fmp_get("income-statement", {"symbol": symbol}))
            if not item:
                continue
            changed = False
            changed |= update_if_present(row, "eps", optional_float(item.get("eps") or item.get("epsdiluted") or item.get("epsDiluted")), "FMP income statement")
            changed |= update_if_present(row, "shares_outstanding", optional_float(item.get("weightedAverageShsOut") or item.get("weightedAverageShsOutDil")), "FMP income statement")
            if changed:
                updated += 1
        except Exception as exc:
            print(f"! income-statement {symbol}: {exc}")
        save_checkpoint(rows, index, context)
        time.sleep(context.sleep_seconds)

    save_rows(rows)
    print(f"[income-statement] updated rows: {updated}")


def step_07_revenue_growth(symbols: Iterable[str], context: StepContext) -> None:
    rows = load_rows()
    updated = 0

    for index, symbol in enumerate(symbols, start=1):
        symbol = clean_symbol(symbol)
        if not symbol:
            continue
        row = rows.setdefault(symbol, {"symbol": symbol})
        if not context.force and row_has_values(row, ("revenue_growth",)):
            continue

        print(f"[income-growth] {index}: {symbol}")
        try:
            item = first_row(fmp_get("income-statement-growth", {"symbol": symbol}))
            if not item:
                continue
            if update_if_present(row, "revenue_growth", percent_value(item.get("growthRevenue") or item.get("revenueGrowth")), "FMP income growth"):
                updated += 1
        except Exception as exc:
            print(f"! income-growth {symbol}: {exc}")
        save_checkpoint(rows, index, context)
        time.sleep(context.sleep_seconds)

    save_rows(rows)
    print(f"[income-growth] updated rows: {updated}")


def step_08_shares_float(symbols: Iterable[str], context: StepContext) -> None:
    rows = load_rows()
    updated = 0
    share_fields = ("shares_float", "shares_outstanding")

    for index, symbol in enumerate(symbols, start=1):
        symbol = clean_symbol(symbol)
        if not symbol:
            continue
        row = rows.setdefault(symbol, {"symbol": symbol})
        if not context.force and row_has_values(row, share_fields):
            continue

        print(f"[shares-float] {index}: {symbol}")
        try:
            item = first_row(fmp_get("shares-float", {"symbol": symbol}))
            if not item:
                continue
            changed = False
            changed |= update_if_present(row, "shares_float", optional_float(item.get("floatShares") or item.get("sharesFloat") or item.get("float")), "FMP shares float")
            changed |= update_if_present(row, "shares_outstanding", optional_float(item.get("outstandingShares") or item.get("sharesOutstanding")), "FMP shares float")
            if changed:
                updated += 1
        except Exception as exc:
            print(f"! shares-float {symbol}: {exc}")
        save_checkpoint(rows, index, context)
        time.sleep(context.sleep_seconds)

    save_rows(rows)
    print(f"[shares-float] updated rows: {updated}")


def step_09_recent_filings(symbols: Iterable[str], context: StepContext) -> None:
    rows = load_rows()
    updated = 0
    to_date = date.today()
    from_date = to_date - timedelta(days=180)
    filing_fields = ("recent_filing_date", "recent_filing_type")

    for index, symbol in enumerate(symbols, start=1):
        symbol = clean_symbol(symbol)
        if not symbol:
            continue
        row = rows.setdefault(symbol, {"symbol": symbol})
        if not context.force and row_has_values(row, filing_fields):
            continue

        print(f"[filings] {index}: {symbol}")
        try:
            item = first_row(
                fmp_get(
                    "sec-filings-search/symbol",
                    {"symbol": symbol, "from": from_date.isoformat(), "to": to_date.isoformat(), "page": 0, "limit": 10},
                )
            )
            if not item:
                continue
            filing_date = item.get("filingDate") or item.get("fillingDate") or item.get("acceptedDate") or item.get("date")
            filing_type = item.get("form") or item.get("type") or item.get("filingType")
            changed = False
            changed |= update_if_present(row, "recent_filing_date", str(filing_date or "")[:10], "FMP SEC filings")
            changed |= update_if_present(row, "recent_filing_type", filing_type, "FMP SEC filings")
            add_source_link(row, item.get("finalLink") or item.get("link") or item.get("url"))
            if changed:
                updated += 1
        except Exception as exc:
            print(f"! filings {symbol}: {exc}")
        save_checkpoint(rows, index, context)
        time.sleep(context.sleep_seconds)

    save_rows(rows)
    print(f"[filings] updated rows: {updated}")


def step_10_databento_prices(symbols: Iterable[str], context: StepContext) -> None:
    """Databento tape refresh for price, volume, avg volume, and change % using the configured equities dataset/schema."""
    rows = load_rows()
    updated = 0
    processed = 0
    quote_fields = ("price", "volume", "change_percent")
    provider = configured_databento_equities_provider(
        symbol_limit=context.batch_size,
        cache_ttl_seconds=0 if context.force else None,
        batch_size=context.batch_size,
    )

    for batch_index, batch in enumerate(chunks(symbols, context.batch_size), start=1):
        needed = tuple(
            symbol
            for symbol in batch
            if context.force or not row_has_values(rows.get(symbol, {}), quote_fields)
        )
        if not needed:
            processed += len(batch)
            continue

        print(f"[databento-prices] batch {batch_index}: {len(needed)} symbol(s)")
        try:
            snapshot = provider.quote_tape(needed, force_refresh=context.force, max_symbols=len(needed))
            for status in snapshot.statuses:
                print(f"- {status.source}: {status.status} :: {status.message}")
            for error in tuple(snapshot.errors)[:8]:
                print(f"! {error}")

            before = rows_to_records(rows)
            after = merge_market_data_records_into_screener_records(
                before,
                snapshot.records,
                fetched_at=snapshot.fetched_at,
                family="quote_tape",
            )
            rows = {clean_symbol(record.symbol): record.to_dict() for record in after if clean_symbol(record.symbol)}
            updated += len(snapshot.records)
        except Exception as exc:
            print(f"! databento-prices batch {batch_index}: {exc}")

        processed += len(batch)
        save_checkpoint(rows, processed, context)
        time.sleep(context.sleep_seconds)

    save_rows(rows)
    print(f"[databento-prices] provider records merged: {updated}")


def step_11_local_quote_join(symbols: Iterable[str], context: StepContext) -> None:
    """No-op helper slot for future dedicated local quote snapshot joins."""
    del symbols, context
    print("local_quote_join is reserved for a future separate price snapshot parquet join.")


STEPS: dict[str, Callable[[Iterable[str], StepContext], None]] = {
    "profile": step_01_profile,
    "batch_quote": step_02_batch_quote,
    "quote": step_02_quote,
    "historical_eod": step_03_historical_eod,
    "market_cap": step_04_market_cap,
    "key_metrics": step_05_key_metrics,
    "income_statement": step_06_income_statement,
    "revenue_growth": step_07_revenue_growth,
    "shares_float": step_08_shares_float,
    "filings": step_09_recent_filings,
    "databento_prices": step_10_databento_prices,
    "local_quote_join": step_11_local_quote_join,
}


def main() -> None:
    parser = argparse.ArgumentParser(description="Stepwise Market Screener Parquet builder.")
    parser.add_argument("step", choices=("init", *STEPS.keys()))
    parser.add_argument("--limit", type=int, default=500)
    parser.add_argument("--sleep", type=float, default=DEFAULT_SLEEP_SECONDS)
    parser.add_argument("--batch-size", type=int, default=DEFAULT_BATCH_SIZE)
    parser.add_argument("--save-every", type=int, default=DEFAULT_SAVE_EVERY)
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()

    if args.step == "init":
        step_00_init_universe(args.limit)
        return

    symbols = current_symbols(args.limit)
    if not symbols:
        raise SystemExit("No symbols in Parquet yet. Run: python tools\\screener_parquet_steps.py init --limit 500")

    context = StepContext(
        sleep_seconds=max(0.0, args.sleep),
        batch_size=max(1, args.batch_size),
        save_every=max(0, args.save_every),
        force=bool(args.force),
    )
    STEPS[args.step](symbols, context)


if __name__ == "__main__":
    main()
