from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
from html import escape
import json
from pathlib import Path
import re
import secrets
from typing import Any, Iterable, Mapping

from app.analytics.option_contract_inspector import parse_occ_option_symbol


REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DATA_DIR = REPO_ROOT / "data" / "trade_memory"
DEFAULT_SNAPSHOT_PATH = DEFAULT_DATA_DIR / "schwab_trade_snapshots.jsonl"
DEFAULT_REPORTS_DIR = DEFAULT_DATA_DIR / "reports"

_SECRET_KEY_PARTS = (
    "access_token",
    "refresh_token",
    "token",
    "secret",
    "authorization",
    "auth_code",
    "client_id",
    "client_secret",
    "api_key",
    "password",
    "credential",
    "cookie",
    "hashvalue",
    "hash_value",
    "accountnumber",
    "account_number",
    "account_id",
    "account_hash",
)


class TradeMemoryStore:
    def __init__(
        self,
        snapshot_path: Path | str = DEFAULT_SNAPSHOT_PATH,
        reports_dir: Path | str = DEFAULT_REPORTS_DIR,
    ) -> None:
        self.snapshot_path = Path(snapshot_path)
        self.reports_dir = Path(reports_dir)

    def save_snapshot(self, snapshot: Mapping[str, Any]) -> dict[str, Any]:
        record = normalize_snapshot(snapshot)
        record["report_path"] = str(write_trade_thesis_report(record, self.reports_dir))
        self.snapshot_path.parent.mkdir(parents=True, exist_ok=True)
        with self.snapshot_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=True, sort_keys=True) + "\n")
        return record

    def load_snapshots(self) -> list[dict[str, Any]]:
        if not self.snapshot_path.exists():
            return []
        records: list[dict[str, Any]] = []
        with self.snapshot_path.open("r", encoding="utf-8") as handle:
            for line in handle:
                stripped = line.strip()
                if not stripped:
                    continue
                try:
                    payload = json.loads(stripped)
                except json.JSONDecodeError:
                    continue
                if isinstance(payload, dict):
                    records.append(payload)
        return records

    def update_snapshot(self, snapshot_id: str, updates: Mapping[str, Any]) -> dict[str, Any] | None:
        records = self.load_snapshots()
        updated: dict[str, Any] | None = None
        for index, record in enumerate(records):
            if str(record.get("snapshot_id") or "") != snapshot_id:
                continue
            merged = deepcopy(record)
            merged.update(dict(updates))
            updated = normalize_snapshot(merged)
            updated["report_path"] = str(write_trade_thesis_report(updated, self.reports_dir))
            records[index] = updated
            break
        if updated is None:
            return None

        self.snapshot_path.parent.mkdir(parents=True, exist_ok=True)
        with self.snapshot_path.open("w", encoding="utf-8") as handle:
            for record in records:
                handle.write(json.dumps(sanitize_for_storage(record), ensure_ascii=True, sort_keys=True) + "\n")
        return updated

    def find_snapshot_for_order(self, order: Mapping[str, Any], *, now: datetime | None = None) -> dict[str, Any] | None:
        return match_snapshot_to_order(order, self.load_snapshots(), now=now)

    def find_snapshots_for_symbol(self, symbol: str) -> list[dict[str, Any]]:
        clean = _normalize_symbol(symbol)
        if not clean:
            return []
        matches: list[dict[str, Any]] = []
        for snapshot in self.load_snapshots():
            candidates = {
                _normalize_symbol(snapshot.get("symbol")),
                _normalize_symbol(snapshot.get("underlying_symbol")),
                _normalize_symbol((snapshot.get("option_details") or {}).get("occ_symbol") if isinstance(snapshot.get("option_details"), dict) else ""),
            }
            if clean in candidates:
                matches.append(snapshot)
        return sorted(matches, key=lambda item: str(item.get("created_at") or ""), reverse=True)


def generate_snapshot_id(symbol: str, created_at: datetime | None = None, *, suffix: str | None = None) -> str:
    timestamp = created_at or datetime.now(timezone.utc)
    safe_symbol = re.sub(r"[^A-Z0-9]+", "-", str(symbol or "UNKNOWN").upper()).strip("-") or "UNKNOWN"
    random_suffix = suffix or secrets.token_hex(3)
    return f"{timestamp.strftime('%Y%m%d-%H%M%S')}-{safe_symbol}-{random_suffix}"


def normalize_snapshot(snapshot: Mapping[str, Any]) -> dict[str, Any]:
    record = sanitize_for_storage(dict(snapshot or {}))
    created_at = str(record.get("created_at") or _utc_now_iso())
    record["created_at"] = created_at
    record["broker"] = str(record.get("broker") or "Schwab")
    symbol = str(record.get("symbol") or "UNKNOWN").strip().upper() or "UNKNOWN"
    record["symbol"] = symbol
    if not record.get("snapshot_id"):
        record["snapshot_id"] = generate_snapshot_id(symbol, _parse_datetime(created_at) or datetime.now(timezone.utc))

    order_ticket = record.get("order_ticket")
    if not isinstance(order_ticket, dict):
        order_ticket = {}
    record["order_ticket"] = sanitize_for_storage(order_ticket)

    option_details = record.get("option_details")
    if not isinstance(option_details, dict):
        option_details = {}
    parsed = parse_occ_option_symbol(str(option_details.get("occ_symbol") or symbol))
    if parsed is not None:
        option_details.update(
            {
                "expiration": parsed.expiration.isoformat(),
                "call_put": parsed.option_type,
                "strike": parsed.strike,
                "occ_symbol": parsed.raw_symbol,
            }
        )
        record["underlying_symbol"] = str(record.get("underlying_symbol") or parsed.underlying)
        record["instrument_type"] = "option"
    else:
        record["instrument_type"] = str(record.get("instrument_type") or "unknown")
    record["option_details"] = sanitize_for_storage(option_details)

    thesis_report = str(record.get("research_report_text") or "").strip()
    thesis_status = str(record.get("thesis_status") or "").strip().lower()
    if not thesis_status:
        thesis_status = "saved" if thesis_report else "missing_analysis"
    record["thesis_status"] = thesis_status
    record["plain_english_summary"] = str(record.get("plain_english_summary") or _summary_from_report(thesis_report) or "No research thesis was available at snapshot time.")
    record["notes"] = _normalize_notes(record.get("notes"))
    record["tags"] = [str(tag).strip() for tag in record.get("tags", []) if str(tag).strip()] if isinstance(record.get("tags"), list) else []
    return record


def sanitize_for_storage(value: Any) -> Any:
    if isinstance(value, Mapping):
        clean: dict[str, Any] = {}
        for key, item in value.items():
            key_text = str(key)
            if _is_secret_key(key_text):
                continue
            clean[key_text] = sanitize_for_storage(item)
        return clean
    if isinstance(value, list):
        return [sanitize_for_storage(item) for item in value]
    if isinstance(value, tuple):
        return [sanitize_for_storage(item) for item in value]
    if isinstance(value, str):
        return _sanitize_text(value)
    return value


def match_snapshot_to_order(
    order: Mapping[str, Any],
    snapshots: Iterable[Mapping[str, Any]],
    *,
    now: datetime | None = None,
    max_age_seconds: int = 7 * 24 * 60 * 60,
) -> dict[str, Any] | None:
    identity = order_identity(order)
    if identity["order_id"] or identity["order_location"]:
        for snapshot in snapshots:
            snapshot_order_id = str(snapshot.get("order_id") or "").strip()
            snapshot_location = str(snapshot.get("order_location") or "").strip()
            identity_order_id = str(identity["order_id"] or "").strip()
            identity_location = str(identity["order_location"] or "").strip()
            if _same_identifier(identity_order_id, snapshot_order_id):
                return dict(snapshot)
            if _same_identifier(identity_location, snapshot_location):
                return dict(snapshot)
            if identity_order_id and _same_identifier(identity_order_id, _order_id_from_location(snapshot_location)):
                return dict(snapshot)
            if snapshot_order_id and _same_identifier(snapshot_order_id, _order_id_from_location(identity_location)):
                return dict(snapshot)

    order_time = _parse_datetime(str(order.get("enteredTime") or order.get("entered_time") or "")) or now
    best: tuple[float, Mapping[str, Any]] | None = None
    for snapshot in snapshots:
        if not _fallback_identity_matches(identity, snapshot):
            continue
        snapshot_time = _parse_datetime(str(snapshot.get("created_at") or ""))
        age = 0.0
        if order_time is not None and snapshot_time is not None:
            age = abs((order_time - snapshot_time).total_seconds())
            if age > max_age_seconds:
                continue
        score = age
        if best is None or score < best[0]:
            best = (score, snapshot)
    return dict(best[1]) if best is not None else None


def order_identity(order: Mapping[str, Any]) -> dict[str, Any]:
    legs = order.get("orderLegCollection") or order.get("orderLegs") or []
    first_leg = legs[0] if isinstance(legs, list) and legs else {}
    first_leg = first_leg if isinstance(first_leg, Mapping) else {}
    instrument = first_leg.get("instrument") if isinstance(first_leg.get("instrument"), Mapping) else {}
    symbol = str(instrument.get("symbol") or first_leg.get("finalSymbol") or order.get("symbol") or "").strip().upper()
    side = str(first_leg.get("instruction") or order.get("side") or "").strip().upper()
    quantity = _first_number(first_leg.get("quantity"), order.get("quantity"))
    price = _first_number(order.get("price"), order.get("limit_price"), order.get("limitPrice"))
    order_id = str(order.get("orderId") or order.get("order_id") or "").strip()
    order_location = str(order.get("location") or order.get("orderLocation") or order.get("order_location") or "").strip()
    return {
        "order_id": order_id,
        "order_location": order_location,
        "symbol": symbol,
        "side": normalize_order_side(side),
        "quantity": quantity,
        "limit_price": price,
        "status": str(order.get("status") or order.get("order_status") or "").strip().upper(),
    }


def compare_then_now(snapshot: Mapping[str, Any], current: Mapping[str, Any] | None) -> dict[str, Any]:
    if not current:
        return {
            "available": False,
            "summary": "Current comparison unavailable. Refresh Schwab/research data to compare.",
            "changes": [],
        }
    changes: list[str] = []
    original_price = _first_number((snapshot.get("market_snapshot") or {}).get("symbol_price") if isinstance(snapshot.get("market_snapshot"), Mapping) else None)
    current_price = _first_number(current.get("symbol_price"), current.get("last_price"))
    if original_price is not None and current_price is not None:
        changes.append(f"Symbol price: {_money(original_price)} then vs {_money(current_price)} now ({_signed_percent((current_price - original_price) / max(original_price, 0.01))}).")

    option_details = snapshot.get("option_details") if isinstance(snapshot.get("option_details"), Mapping) else {}
    original_dte = _first_number(option_details.get("dte"), option_details.get("days_to_expiration"))
    current_dte = _first_number(current.get("dte"))
    if original_dte is not None and current_dte is not None:
        changes.append(f"DTE: {original_dte:g} then vs {current_dte:g} now.")

    original_verdict = str(snapshot.get("original_posture") or snapshot.get("thesis_posture") or "").strip()
    current_verdict = str(current.get("posture") or current.get("verdict") or "").strip()
    if original_verdict or current_verdict:
        changes.append(f"Verdict/posture: {original_verdict or '--'} then vs {current_verdict or '--'} now.")

    return {
        "available": bool(changes),
        "summary": "Then-vs-now comparison generated." if changes else "Current comparison unavailable. Refresh Schwab/research data to compare.",
        "changes": changes,
    }


def render_trade_thesis_html(snapshot: Mapping[str, Any]) -> str:
    record = normalize_snapshot(snapshot)
    sections = [
        ("Trade identity", _identity_lines(record)),
        ("Order ticket", _order_ticket_lines(record)),
        ("Original verdict", [record.get("plain_english_summary") or "--"]),
        ("Supporting evidence", _tab_or_report_lines(record, "Evidence Desk")),
        ("Contradictions", _extract_named_lines(record, ("Contradictions", "Biggest risk", "Risk"))),
        ("Risk posture", _extract_named_lines(record, ("Risk", "Risk read", "Position risk"))),
        ("Options/Greeks if applicable", _tab_or_report_lines(record, "Greeks")),
        ("Macro/event context", _tab_or_report_lines(record, "Macro Context") + _tab_or_report_lines(record, "Earnings / News")),
        ("What would make this trade dumb?", _extract_named_lines(record, ("What would make this trade dumb", "What can go wrong", "Biggest risk"))),
        ("What would change my mind?", _extract_named_lines(record, ("What would change my mind", "Changes mind", "Invalidation"))),
        ("Raw Schwab preview/order details", _raw_order_lines(record)),
    ]
    body = "\n".join(_html_section(title, lines) for title, lines in sections)
    return (
        "<!doctype html>\n"
        "<html><head><meta charset=\"utf-8\"><title>Trade Thesis Snapshot</title>"
        "<style>"
        "body{font-family:Segoe UI,Arial,sans-serif;background:#f8fafc;color:#0f172a;margin:0;padding:28px;}"
        "main{max-width:1000px;margin:0 auto;background:#fff;border:1px solid #cbd5e1;padding:26px;}"
        "h1{margin:0 0 6px;font-size:28px;} h2{border-top:1px solid #e2e8f0;padding-top:16px;margin-top:20px;}"
        ".muted{color:#64748b}.badge{display:inline-block;background:#eef2ff;color:#1d4ed8;padding:4px 8px;font-weight:700}"
        "pre{white-space:pre-wrap;background:#f1f5f9;padding:12px;border:1px solid #e2e8f0;}"
        "li{margin:4px 0}"
        "</style></head><body><main>"
        "<h1>Trade Thesis Snapshot</h1>"
        "<p class=\"muted\">This is what the cockpit saw at the time of the order. It is a saved snapshot, not a current recommendation.</p>"
        f"<p><span class=\"badge\">{escape(str(record.get('broker') or 'Schwab'))}</span> "
        f"<span class=\"badge\">{escape(str(record.get('thesis_status') or 'unknown').upper())}</span></p>"
        f"{body}"
        "</main></body></html>"
    )


def write_trade_thesis_report(snapshot: Mapping[str, Any], reports_dir: Path | str = DEFAULT_REPORTS_DIR) -> Path:
    record = normalize_snapshot(snapshot)
    reports = Path(reports_dir)
    reports.mkdir(parents=True, exist_ok=True)
    path = reports / f"{record['snapshot_id']}.html"
    path.write_text(render_trade_thesis_html(record), encoding="utf-8")
    return path


def _identity_lines(record: Mapping[str, Any]) -> list[str]:
    option = record.get("option_details") if isinstance(record.get("option_details"), Mapping) else {}
    lines = [
        f"Snapshot ID: {record.get('snapshot_id')}",
        f"Created: {record.get('created_at')}",
        f"Broker: {record.get('broker')}",
        f"Symbol: {record.get('symbol')}",
        f"Instrument: {record.get('instrument_type')}",
        f"Source: {record.get('source') or '--'}",
        f"Order status: {record.get('order_status') or '--'}",
    ]
    if option:
        lines.extend(
            [
                f"Underlying: {record.get('underlying_symbol') or '--'}",
                f"Expiration: {option.get('expiration') or '--'}",
                f"Call/put: {option.get('call_put') or '--'}",
                f"Strike: {option.get('strike') or '--'}",
                f"OCC symbol: {option.get('occ_symbol') or '--'}",
            ]
        )
    return lines


def _order_ticket_lines(record: Mapping[str, Any]) -> list[str]:
    ticket = record.get("order_ticket") if isinstance(record.get("order_ticket"), Mapping) else {}
    return [
        f"Side: {ticket.get('side') or '--'}",
        f"Quantity: {ticket.get('quantity') or '--'}",
        f"Order type: {ticket.get('order_type') or '--'}",
        f"Limit price: {ticket.get('limit_price') or '--'}",
        f"Stop price: {ticket.get('stop_price') or '--'}",
        f"Time in force: {ticket.get('time_in_force') or '--'}",
        f"Estimated notional: {ticket.get('estimated_notional') or '--'}",
    ]


def _tab_or_report_lines(record: Mapping[str, Any], tab_name: str) -> list[str]:
    tabs = record.get("tab_summaries") if isinstance(record.get("tab_summaries"), Mapping) else {}
    text = str(tabs.get(tab_name) or "").strip()
    if text:
        return text.splitlines()[:80]
    return _extract_named_lines(record, (tab_name,))


def _extract_named_lines(record: Mapping[str, Any], names: tuple[str, ...]) -> list[str]:
    report = str(record.get("research_report_text") or "").strip()
    if not report:
        return ["--"]
    lowered_names = tuple(name.lower() for name in names)
    lines = [line.strip() for line in report.splitlines() if line.strip()]
    hits = [line for line in lines if any(name in line.lower() for name in lowered_names)]
    return hits[:20] or ["--"]


def _raw_order_lines(record: Mapping[str, Any]) -> list[str]:
    payload = {
        "preview_status": record.get("preview_status"),
        "order_id": record.get("order_id"),
        "order_location": record.get("order_location"),
        "raw_order_json": record.get("raw_order_json"),
        "raw_preview_response": record.get("raw_preview_response"),
        "schwab_response_text": record.get("schwab_response_text"),
    }
    return [json.dumps(sanitize_for_storage(payload), indent=2, sort_keys=True)]


def _html_section(title: str, lines: list[str]) -> str:
    if not lines:
        lines = ["--"]
    if len(lines) == 1 and ("\n" in lines[0] or lines[0].lstrip().startswith(("{", "["))):
        return f"<h2>{escape(title)}</h2><pre>{escape(lines[0])}</pre>"
    items = "".join(f"<li>{escape(str(line))}</li>" for line in lines if str(line).strip())
    return f"<h2>{escape(title)}</h2><ul>{items or '<li>--</li>'}</ul>"


def _fallback_identity_matches(identity: Mapping[str, Any], snapshot: Mapping[str, Any]) -> bool:
    ticket = snapshot.get("order_ticket") if isinstance(snapshot.get("order_ticket"), Mapping) else {}
    snapshot_symbol = _normalize_symbol(snapshot.get("symbol"))
    order_symbol = _normalize_symbol(identity.get("symbol"))
    if not order_symbol or snapshot_symbol != order_symbol:
        option = snapshot.get("option_details") if isinstance(snapshot.get("option_details"), Mapping) else {}
        if _normalize_symbol(option.get("occ_symbol")) != order_symbol:
            return False

    if normalize_order_side(ticket.get("side")) != normalize_order_side(identity.get("side")):
        return False
    if not _close_number(_first_number(ticket.get("quantity")), _first_number(identity.get("quantity"))):
        return False
    return _close_number(_first_number(ticket.get("limit_price")), _first_number(identity.get("limit_price")), tolerance=0.01)


def normalize_order_side(value: Any) -> str:
    text = str(value or "").strip().upper()
    if text in {"BUY", "BUY_TO_OPEN", "BUY_TO_CLOSE"}:
        return "BUY"
    if text in {"SELL", "SELL_TO_OPEN", "SELL_TO_CLOSE"}:
        return "SELL"
    if "BUY" in text:
        return "BUY"
    if "SELL" in text:
        return "SELL"
    return text


def _same_identifier(left: Any, right: Any) -> bool:
    return bool(str(left or "").strip() and str(left or "").strip() == str(right or "").strip())


def _order_id_from_location(value: str) -> str:
    match = re.search(r"/orders/([^/?#\s]+)", str(value or ""))
    return match.group(1) if match else ""


def _is_secret_key(key: str) -> bool:
    compact = re.sub(r"[^a-z0-9]", "", key.lower())
    return any(part.replace("_", "") in compact for part in _SECRET_KEY_PARTS)


def _sanitize_text(value: str) -> str:
    text = re.sub(r"Bearer\s+[A-Za-z0-9._~+/=-]+", "Bearer [REDACTED]", value)
    text = re.sub(r"(SCHWAB_[A-Z_]*(?:SECRET|TOKEN|CLIENT_ID|API_KEY)[A-Z_]*)=\S+", r"\1=[REDACTED]", text)
    return text


def _summary_from_report(report: str) -> str:
    if not report:
        return ""
    for line in report.splitlines():
        stripped = line.strip(" -")
        if len(stripped) >= 30 and not set(stripped) <= {"=", "-"}:
            return stripped[:500]
    return report[:500]


def _normalize_notes(value: Any) -> dict[str, str]:
    if not isinstance(value, Mapping):
        return {
            "original_reason": "",
            "invalidation_level": "",
            "target": "",
            "close_reduce": "",
            "add": "",
            "review_date": "",
            "freeform": str(value or ""),
        }
    return {
        "original_reason": str(value.get("original_reason") or ""),
        "invalidation_level": str(value.get("invalidation_level") or ""),
        "target": str(value.get("target") or ""),
        "close_reduce": str(value.get("close_reduce") or ""),
        "add": str(value.get("add") or ""),
        "review_date": str(value.get("review_date") or ""),
        "freeform": str(value.get("freeform") or value.get("notes") or ""),
    }


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _parse_datetime(value: str) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def _first_number(*values: Any) -> float | None:
    for value in values:
        if value is None or value == "":
            continue
        if isinstance(value, (int, float)):
            return float(value)
        try:
            text = str(value).strip().replace("$", "").replace(",", "").replace("%", "")
            if text:
                return float(text)
        except ValueError:
            continue
    return None


def _close_number(left: float | None, right: float | None, *, tolerance: float = 0.0001) -> bool:
    if left is None or right is None:
        return left is None and right is None
    return abs(left - right) <= tolerance


def _normalize_symbol(value: Any) -> str:
    return re.sub(r"\s+", "", str(value or "").strip().upper())


def _money(value: float) -> str:
    return f"${value:,.2f}"


def _signed_percent(value: float) -> str:
    return f"{value:+.2%}"
