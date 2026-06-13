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
DEFAULT_REPORTS_DIR_GD = Path("I:/My Drive/PRC/REPORTS/TRADE-MEMORY")
DEFAULT_SNAPSHOT_PATH_GD = Path("I:/My Drive/PRC/REPORTS/SNAPSHOTS") / "schwab_trade_snapshots.jsonl"


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

_SCREENSHOT_KEY_PARTS = (
    "screenshot",
    "screen_shot",
    "image_base64",
    "image_data",
    "png_base64",
    "jpeg_base64",
)

_RESEARCH_TAB_TITLES = (
    "Overview",
    "Evidence Desk",
    "Technicals",
    "Risk Scenarios",
    "Options Strategy",
    "Greeks",
    "Earnings / News",
    "Fundamentals",
    "Macro Context",
)

_TAB_INTROS = {
    "Overview": "Decision summary, operator read, recommendation engine, and what matters most.",
    "Evidence Desk": "Saved trade evidence, contradictions, execution risks, and change-of-mind triggers.",
    "Technicals": "Chart read, technical command center notes, levels, momentum, and invalidation context.",
    "Risk Scenarios": "Scenario math, stop/risk planning, and position impact captured at snapshot time.",
    "Options Strategy": "Saved option vehicle notes, candidate readouts, and ticket quality context.",
    "Greeks": "Option sensitivity notes for delta, gamma, theta, vega, and related warnings.",
    "Earnings / News": "Event freshness, earnings/news context, and source status at capture time.",
    "Fundamentals": "Fundamental read, action bias, confidence, and investment-versus-trade context.",
    "Macro Context": "Macro backdrop, rates/inflation/liquidity context, and market regime notes.",
}

_LEGACY_TAB_ALIASES = {
    "trade evidence": "Evidence Desk",
    "schwab trade evidence desk": "Evidence Desk",
    "technical readout": "Technicals",
    "technical command center": "Technicals",
    "what the chart is saying": "Technicals",
    "scenario": "Risk Scenarios",
    "risk scenario": "Risk Scenarios",
    "risk scenarios": "Risk Scenarios",
    "options strategy explanation": "Options Strategy",
    "option strategy": "Options Strategy",
    "decision from the greeks": "Greeks",
    "option sensitivities": "Greeks",
    "earnings": "Earnings / News",
    "earnings news": "Earnings / News",
    "news": "Earnings / News",
    "fundamentals explanation": "Fundamentals",
    "fundamental read": "Fundamentals",
    "official macro snapshot": "Macro Context",
    "macro explanation": "Macro Context",
    "macro": "Macro Context",
}

_TAB_EXCERPT_KEYWORDS = {
    "Evidence Desk": ("evidence", "supporting", "contradiction", "verdict", "posture"),
    "Technicals": ("technical", "chart", "trend", "momentum", "support", "resistance", "vwap"),
    "Risk Scenarios": ("risk scenario", "scenario", "risk", "stop", "invalidation", "position impact"),
    "Options Strategy": ("options strategy", "option", "call", "put", "spread", "contract", "vehicle"),
    "Greeks": ("greeks", "delta", "gamma", "theta", "vega", "implied volatility", "rho"),
    "Earnings / News": ("earnings", "news", "guidance", "release", "freshness", "event"),
    "Fundamentals": ("fundamental", "revenue", "margin", "cash", "debt", "valuation", "action bias"),
    "Macro Context": ("macro", "rates", "inflation", "liquidity", "market regime", "backdrop"),
}


class TradeMemoryStore:
    def __init__(
            self,
            snapshot_path: Path | str = DEFAULT_SNAPSHOT_PATH_GD,
            reports_dir: Path | str = DEFAULT_REPORTS_DIR_GD,
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

    def find_snapshot_for_order(self, order: Mapping[str, Any], *, now: datetime | None = None) -> dict[
                                                                                                       str, Any] | None:
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
                _normalize_symbol((snapshot.get("option_details") or {}).get("occ_symbol") if isinstance(
                    snapshot.get("option_details"), dict) else ""),
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
    record["plain_english_summary"] = str(record.get("plain_english_summary") or _summary_from_report(
        thesis_report) or "No research thesis was available at snapshot time.")
    record["notes"] = _normalize_notes(record.get("notes"))
    record["tags"] = [str(tag).strip() for tag in record.get("tags", []) if str(tag).strip()] if isinstance(
        record.get("tags"), list) else []
    return record


def sanitize_for_storage(value: Any) -> Any:
    if isinstance(value, Mapping):
        clean: dict[str, Any] = {}
        for key, item in value.items():
            key_text = str(key)
            if _is_secret_key(key_text) or _is_screenshot_key(key_text):
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
    order_location = str(
        order.get("location") or order.get("orderLocation") or order.get("order_location") or "").strip()
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
    original_price = _first_number(
        (snapshot.get("market_snapshot") or {}).get("symbol_price") if isinstance(snapshot.get("market_snapshot"),
                                                                                  Mapping) else None)
    current_price = _first_number(current.get("symbol_price"), current.get("last_price"))
    if original_price is not None and current_price is not None:
        changes.append(
            f"Symbol price: {_money(original_price)} then vs {_money(current_price)} now ({_signed_percent((current_price - original_price) / max(original_price, 0.01))}).")

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
    tabs = _snapshot_tab_texts(record)
    symbol = str(record.get("symbol") or "UNKNOWN")
    created = str(record.get("created_at") or "--")
    broker = str(record.get("broker") or "Schwab")
    thesis_status = str(record.get("thesis_status") or "unknown")
    instrument = str(record.get("instrument_type") or "unknown")
    source = str(record.get("source") or "snapshot")
    tab_sections = "\n".join(_html_tab_section(title, tabs.get(title, "")) for title in _RESEARCH_TAB_TITLES)
    raw_payload = _raw_snapshot_json(record)
    return (
        "<!doctype html>\n"
        "<html lang=\"en\"><head><meta charset=\"utf-8\">"
        "<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">"
        f"<title>{escape(symbol)} Trade Memory Snapshot</title>"
        f"<style>{_trade_memory_report_css()}</style></head><body>"
        "<main class=\"workspace-shell\">"
        "<header class=\"workspace-header\">"
        "<div class=\"header-copy\">"
        "<p class=\"eyebrow\">Schwab Research + Risk Workspace</p>"
        f"<h1>{escape(symbol)}</h1>"
        f"<p class=\"subtitle\">Trade Memory snapshot captured {escape(created)}</p>"
        "</div>"
        "<div class=\"header-badges\" aria-label=\"Snapshot badges\">"
        f"{_html_badge(broker, 'good')}"
        f"{_html_badge(thesis_status.upper().replace('_', ' '), _status_tone(thesis_status))}"
        f"{_html_badge(instrument.upper(), 'info')}"
        f"{_html_badge(source.replace('_', ' ').title(), 'neutral')}"
        "</div>"
        "</header>"
        "<section class=\"snapshot-warning\" aria-label=\"Snapshot warning\">"
        "<strong>Saved snapshot, not live advice.</strong> "
        "This report preserves what the cockpit saw when the snapshot was saved. Refresh Schwab, market data, "
        "and the Research Workspace before using it for any current decision."
        "</section>"
        f"{_html_identity_band(record)}"
        f"{_html_at_glance(record, tabs)}"
        "<section class=\"tab-map\" aria-label=\"Saved research tabs\">"
        "<h2>Saved Research Tabs</h2>"
        "<div class=\"tab-chip-row\">"
        + "".join(f"<a href=\"#{_safe_id(title)}\">{escape(title)}</a>" for title in _RESEARCH_TAB_TITLES)
        + "</div></section>"
        f"{tab_sections}"
        "<details class=\"raw-details\">"
        "<summary>Raw Schwab/order JSON</summary>"
        "<p>Sanitized raw order and Schwab response payloads are retained here for audit context.</p>"
        f"<pre>{escape(raw_payload)}</pre>"
        "</details>"
        "</main></body></html>"
    )


def write_trade_thesis_report(snapshot: Mapping[str, Any], reports_dir: Path | str = DEFAULT_REPORTS_DIR_GD) -> Path:
    record = normalize_snapshot(snapshot)
    reports = Path(reports_dir)
    reports.mkdir(parents=True, exist_ok=True)
    path = reports / f"{record['snapshot_id']}.html"
    path.write_text(render_trade_thesis_html(record), encoding="utf-8")
    return path


def _trade_memory_report_css() -> str:
    return """
:root{
  color-scheme:dark;
  --canvas:#070d19;
  --surface:#0f172a;
  --panel:#111827;
  --panel-alt:#1f2937;
  --border:#334155;
  --border-soft:#243244;
  --text:#e5e7eb;
  --muted:#94a3b8;
  --accent:#60a5fa;
  --accent-strong:#2563eb;
  --positive:#34d399;
  --negative:#fb7185;
  --warning:#fbbf24;
  --input:#08111f;
}
*{box-sizing:border-box}
html{background:var(--canvas)}
body{
  margin:0;
  min-height:100vh;
  background:
    radial-gradient(circle at top left,rgba(37,99,235,.18),transparent 34rem),
    linear-gradient(180deg,#07101f 0%,var(--canvas) 42rem);
  color:var(--text);
  font-family:"Segoe UI",Arial,sans-serif;
  line-height:1.5;
}
.workspace-shell{
  width:min(1360px,calc(100% - 36px));
  margin:0 auto;
  padding:22px 0 34px;
}
.workspace-header{
  display:flex;
  gap:18px;
  align-items:flex-start;
  justify-content:space-between;
  background:var(--surface);
  border:1px solid var(--border);
  border-radius:8px;
  padding:20px;
  box-shadow:0 18px 44px rgba(0,0,0,.32);
}
.header-copy{min-width:260px}
.eyebrow{
  margin:0 0 6px;
  color:var(--accent);
  font-size:12px;
  font-weight:700;
  letter-spacing:.08em;
  text-transform:uppercase;
}
h1{margin:0;font-size:34px;line-height:1.08;letter-spacing:0}
.subtitle{margin:8px 0 0;color:var(--muted)}
.header-badges,.tab-chip-row{
  display:flex;
  flex-wrap:wrap;
  gap:8px;
  justify-content:flex-end;
}
.badge,.tab-chip-row a{
  display:inline-flex;
  align-items:center;
  min-height:28px;
  border:1px solid var(--border);
  border-radius:999px;
  padding:4px 10px;
  background:var(--panel-alt);
  color:var(--text);
  font-size:12px;
  font-weight:700;
  text-decoration:none;
}
.badge-good{border-color:rgba(52,211,153,.38);background:#052e2b;color:var(--positive)}
.badge-bad{border-color:rgba(251,113,133,.42);background:#3b0a19;color:var(--negative)}
.badge-warn{border-color:rgba(251,191,36,.42);background:#30240a;color:var(--warning)}
.badge-info{border-color:rgba(96,165,250,.42);background:#0b2447;color:#bfdbfe}
.badge-neutral{color:var(--muted)}
.snapshot-warning{
  margin-top:12px;
  padding:12px 14px;
  border:1px solid rgba(251,191,36,.42);
  border-left:4px solid var(--warning);
  border-radius:8px;
  background:#211b0d;
  color:#fde68a;
}
.identity-grid,.glance-grid,.content-grid{
  display:grid;
  grid-template-columns:repeat(12,1fr);
  gap:12px;
}
.identity-grid{margin-top:12px}
.detail-card,.glance-card,.content-card,.tab-map,.workspace-section,.raw-details{
  border:1px solid var(--border);
  border-radius:8px;
  background:rgba(17,24,39,.96);
  box-shadow:0 10px 30px rgba(0,0,0,.22);
}
.detail-card{grid-column:span 4;padding:14px}
.detail-card h2,.tab-map h2,.workspace-section h2{
  margin:0;
  font-size:17px;
  line-height:1.25;
  letter-spacing:0;
}
.kv-table,.mini-table,.markdown-table{
  width:100%;
  border-collapse:collapse;
  margin-top:10px;
  overflow:hidden;
  border-radius:6px;
}
.kv-table th,.kv-table td,.mini-table th,.mini-table td,.markdown-table th,.markdown-table td{
  border-bottom:1px solid var(--border-soft);
  padding:7px 8px;
  vertical-align:top;
  text-align:left;
}
.kv-table th,.mini-table th{
  width:36%;
  color:var(--muted);
  font-weight:600;
}
.markdown-table th{
  color:#cbd5e1;
  background:#172033;
  font-weight:700;
}
.kv-table tr:last-child th,.kv-table tr:last-child td,.mini-table tr:last-child th,.mini-table tr:last-child td,.markdown-table tr:last-child td{border-bottom:0}
.glance-section,.tab-map,.workspace-section,.raw-details{margin-top:14px;padding:16px}
.section-heading{
  display:flex;
  gap:12px;
  align-items:flex-start;
  justify-content:space-between;
  margin-bottom:12px;
}
.section-heading p{margin:4px 0 0;color:var(--muted)}
.glance-grid{margin-top:12px}
.glance-card{
  grid-column:span 12;
  padding:14px;
  border-top:3px solid var(--accent);
}
@media (min-width:760px){.glance-card{grid-column:span 6}}
@media (min-width:1120px){.glance-card{grid-column:span 2}.glance-card:first-child{grid-column:span 4}}
.glance-card.good{border-top-color:var(--positive)}
.glance-card.bad{border-top-color:var(--negative)}
.glance-card.warn{border-top-color:var(--warning)}
.glance-card.info{border-top-color:var(--accent)}
.glance-card h3,.content-card h3{
  margin:0 0 6px;
  color:var(--muted);
  font-size:12px;
  font-weight:700;
  text-transform:uppercase;
  letter-spacing:.06em;
}
.glance-value{font-size:18px;font-weight:800;line-height:1.25}
.glance-detail{margin:8px 0 0;color:#cbd5e1;font-size:13px}
.tab-map{background:var(--surface)}
.tab-chip-row{justify-content:flex-start;margin-top:10px}
.tab-chip-row a:hover{border-color:var(--accent);color:#dbeafe}
.workspace-section{scroll-margin-top:18px}
.section-status{white-space:nowrap}
.content-card{
  grid-column:span 12;
  padding:14px;
  background:linear-gradient(180deg,rgba(31,41,55,.74),rgba(17,24,39,.98));
}
@media (min-width:920px){.content-card{grid-column:span 6}.content-card.wide{grid-column:span 12}}
.content-card p{margin:8px 0 0}
.content-card p:first-child{margin-top:0}
.content-card ul{margin:8px 0 0;padding-left:20px}
.content-card li{margin:5px 0}
.empty-card{color:var(--muted);border-style:dashed}
.section-overflow{
  grid-column:span 12;
  margin-top:2px;
  border:1px dashed var(--border);
  border-radius:8px;
  padding:10px 12px;
  background:#0b1220;
}
.section-overflow summary,.raw-details summary{
  cursor:pointer;
  color:#dbeafe;
  font-weight:800;
}
.raw-details{
  background:#0b1220;
  color:var(--muted);
}
.raw-details pre{
  overflow:auto;
  max-height:520px;
  margin:12px 0 0;
  padding:12px;
  border:1px solid var(--border);
  border-radius:8px;
  background:var(--input);
  color:#cbd5e1;
  white-space:pre-wrap;
  word-break:break-word;
}
@media (max-width:900px){
  .workspace-header{display:block}
  .header-badges{justify-content:flex-start;margin-top:14px}
  .detail-card{grid-column:span 12}
  h1{font-size:28px}
}
""".strip()


def _html_badge(text: Any, tone: str = "neutral") -> str:
    tone = tone if tone in {"good", "bad", "warn", "info", "neutral"} else "neutral"
    label = str(text or "--").strip() or "--"
    return f"<span class=\"badge badge-{tone}\">{escape(label)}</span>"


def _html_identity_band(record: Mapping[str, Any]) -> str:
    option = record.get("option_details") if isinstance(record.get("option_details"), Mapping) else {}
    ticket = record.get("order_ticket") if isinstance(record.get("order_ticket"), Mapping) else {}
    identity_rows = [
        ("Symbol", record.get("symbol")),
        ("Created", record.get("created_at")),
        ("Snapshot ID", record.get("snapshot_id")),
        ("Broker", record.get("broker")),
        ("Thesis status", str(record.get("thesis_status") or "unknown").upper()),
        ("Source", str(record.get("source") or "snapshot").replace("_", " ").title()),
    ]
    order_rows = [
        ("Side", ticket.get("side")),
        ("Quantity", ticket.get("quantity")),
        ("Order type", ticket.get("order_type")),
        ("Limit price", ticket.get("limit_price")),
        ("Stop price", ticket.get("stop_price")),
        ("Time in force", ticket.get("time_in_force")),
        ("Estimated notional", ticket.get("estimated_notional")),
        ("Order status", record.get("order_status")),
        ("Preview status", record.get("preview_status")),
        ("Order ID / link", record.get("order_id") or record.get("order_location")),
    ]
    contract_rows = [
        ("Instrument", record.get("instrument_type")),
        ("Underlying", record.get("underlying_symbol") or record.get("symbol")),
        ("Expiration", option.get("expiration")),
        ("Call / put", option.get("call_put")),
        ("Strike", option.get("strike")),
        ("OCC symbol", option.get("occ_symbol")),
    ]
    return (
        "<section class=\"identity-grid\" aria-label=\"Snapshot identity\">"
        f"{_html_detail_card('Snapshot', identity_rows)}"
        f"{_html_detail_card('Order Ticket', order_rows)}"
        f"{_html_detail_card('Contract / Market', contract_rows)}"
        "</section>"
    )


def _html_detail_card(title: str, rows: list[tuple[str, Any]]) -> str:
    return (
        "<article class=\"detail-card\">"
        f"<h2>{escape(title)}</h2>"
        f"{_html_kv_table(rows, css_class='kv-table')}"
        "</article>"
    )


def _html_kv_table(rows: list[tuple[str, Any]], *, css_class: str) -> str:
    body = []
    for label, value in rows:
        body.append(
            "<tr>"
            f"<th>{escape(str(label))}</th>"
            f"<td>{escape(_display_value(value))}</td>"
            "</tr>"
        )
    return f"<table class=\"{escape(css_class)}\"><tbody>{''.join(body)}</tbody></table>"


def _html_at_glance(record: Mapping[str, Any], tabs: Mapping[str, str]) -> str:
    cards = _glance_cards(record, tabs)
    body = "".join(
        "<article class=\"glance-card {tone}\">"
        "<h3>{title}</h3>"
        "<div class=\"glance-value\">{value}</div>"
        "<p class=\"glance-detail\">{detail}</p>"
        "</article>".format(
            tone=escape(tone),
            title=escape(title),
            value=escape(value),
            detail=escape(detail),
        )
        for title, value, detail, tone in cards
    )
    return (
        "<section class=\"glance-section\" aria-label=\"At a Glance\">"
        "<div class=\"section-heading\"><div>"
        "<h2>At a Glance</h2>"
        "<p>Condensed read from the saved Research Workspace text.</p>"
        "</div></div>"
        f"<div class=\"glance-grid\">{body}</div>"
        "</section>"
    )


def _glance_cards(record: Mapping[str, Any], tabs: Mapping[str, str]) -> list[tuple[str, str, str, str]]:
    all_text = "\n".join(
        str(value)
        for value in [
            record.get("plain_english_summary"),
            record.get("original_posture"),
            *tabs.values(),
        ]
        if str(value or "").strip()
    )
    summary = str(record.get("plain_english_summary") or "").strip()
    verdict = _first_nonempty_text(
        [
            _labeled_value(all_text, ("Operator verdict", "Evidence verdict", "Verdict", "Posture")),
            str(record.get("original_posture") or "").strip(),
            summary,
        ],
        "No verdict captured",
    )
    technical = _first_nonempty_text(
        [
            _labeled_value(all_text, ("Technical Read", "Command read", "Trend", "Momentum")),
            _first_meaningful_line(tabs.get("Technicals", "")),
        ],
        "No technical read captured",
    )
    thesis = _first_nonempty_text(
        [
            _labeled_value(all_text, ("Thesis Read", "Trade judgment", "Recommendation")),
            summary,
        ],
        "No thesis read captured",
    )
    action = _first_nonempty_text(
        [
            _labeled_value(all_text, ("Action Bias", "Primary action", "Right now", "Recommended action")),
            _labeled_value(tabs.get("Fundamentals", ""), ("Action bias",)),
        ],
        "No action bias captured",
    )
    risk = _first_nonempty_text(
        [
            _labeled_value(all_text, ("Risk Level", "Risk Heat", "Risk read", "Position risk", "Biggest risk")),
            _first_meaningful_line(tabs.get("Risk Scenarios", "")),
        ],
        "No risk level captured",
    )
    return [
        ("Verdict", _short_text(verdict, 120), _short_text(summary or verdict, 210), _value_tone(verdict)),
        ("Technical Read", _short_text(technical, 120), _short_text(_first_meaningful_line(tabs.get("Technicals", "")) or technical, 210), _value_tone(technical)),
        ("Thesis Read", _short_text(thesis, 120), _short_text(summary or thesis, 210), _value_tone(thesis)),
        ("Action Bias", _short_text(action, 120), "Saved action posture from the research workspace, if present.", _value_tone(action)),
        ("Risk Level", _short_text(risk, 120), "Snapshot risk context; verify current market data before acting.", _value_tone(risk)),
    ]


def _snapshot_tab_texts(record: Mapping[str, Any]) -> dict[str, str]:
    report = str(record.get("research_report_text") or "").strip()
    tabs: dict[str, str] = {title: "" for title in _RESEARCH_TAB_TITLES}
    raw_tabs = record.get("tab_summaries") if isinstance(record.get("tab_summaries"), Mapping) else {}

    if isinstance(raw_tabs, Mapping):
        unknown_blocks: list[str] = []
        for key, value in raw_tabs.items():
            text = str(value or "").strip()
            if not text:
                continue
            canonical = _canonical_tab_title(str(key))
            if canonical in tabs:
                tabs[canonical] = text
            else:
                unknown_blocks.append(f"{key}\n{text}")
        if unknown_blocks and not tabs["Overview"]:
            tabs["Overview"] = "\n\n".join(unknown_blocks)

    parsed = _parse_report_tab_sections(report)
    for title, text in parsed.items():
        if title in tabs and not tabs[title]:
            tabs[title] = text

    if report and not any(text.strip() for text in tabs.values()):
        tabs["Overview"] = report

    if report:
        for title in _RESEARCH_TAB_TITLES:
            if tabs[title].strip():
                continue
            excerpt = _legacy_tab_excerpt(report, title)
            if excerpt:
                tabs[title] = excerpt
    return tabs


def _parse_report_tab_sections(report: str) -> dict[str, str]:
    sections: dict[str, list[str]] = {}
    current: str | None = None
    lines = str(report or "").replace("\r\n", "\n").replace("\r", "\n").split("\n")
    index = 0
    while index < len(lines):
        line = lines[index]
        canonical = _canonical_tab_title(line)
        if canonical is not None:
            current = canonical
            sections.setdefault(current, [])
            if index + 1 < len(lines) and _is_separator_line(lines[index + 1]):
                index += 2
                continue
            index += 1
            continue
        if current is not None:
            sections[current].append(line)
        index += 1
    return {title: "\n".join(_trim_lines(lines)).strip() for title, lines in sections.items() if _trim_lines(lines)}


def _canonical_tab_title(value: str) -> str | None:
    clean = re.sub(r"^\s*#{1,6}\s*", "", str(value or "").strip())
    clean = clean.rstrip(":").strip()
    if not clean or _is_separator_line(clean):
        return None
    lowered = re.sub(r"\s+", " ", clean.lower())
    for title in _RESEARCH_TAB_TITLES:
        if lowered == title.lower():
            return title
    for alias, title in _LEGACY_TAB_ALIASES.items():
        if lowered == alias or lowered.startswith(alias + " -") or lowered.startswith(alias + ":"):
            return title
    return None


def _legacy_tab_excerpt(report: str, title: str) -> str:
    keywords = _TAB_EXCERPT_KEYWORDS.get(title, ())
    if not keywords:
        return ""
    lines = [line.strip() for line in str(report or "").splitlines()]
    useful = [line for line in lines if line and not _is_separator_line(line) and _canonical_tab_title(line) is None]
    excerpts: list[str] = []
    seen: set[str] = set()
    lowered_keywords = tuple(keyword.lower() for keyword in keywords)
    for index, line in enumerate(useful):
        lowered = line.lower()
        if not any(keyword in lowered for keyword in lowered_keywords):
            continue
        for candidate in useful[index:index + 4]:
            if candidate in seen:
                continue
            seen.add(candidate)
            excerpts.append(candidate)
        if len(excerpts) >= 28:
            break
    return "\n".join(excerpts[:28])


def _html_tab_section(title: str, text: str) -> str:
    has_text = bool(str(text or "").strip())
    status = _html_badge("Captured" if has_text else "Not saved", "good" if has_text else "neutral")
    body = _html_content_cards(title, text) if has_text else _html_empty_card(
        f"No {title} content was saved in this snapshot."
    )
    return (
        f"<section class=\"workspace-section\" id=\"{_safe_id(title)}\">"
        "<div class=\"section-heading\">"
        "<div>"
        f"<h2>{escape(title)}</h2>"
        f"<p>{escape(_TAB_INTROS.get(title, 'Saved research workspace content.'))}</p>"
        "</div>"
        f"<div class=\"section-status\">{status}</div>"
        "</div>"
        f"<div class=\"content-grid\">{body}</div>"
        "</section>"
    )


def _html_empty_card(message: str) -> str:
    return (
        "<article class=\"content-card empty-card wide\">"
        "<h3>Not Captured</h3>"
        f"<p>{escape(message)}</p>"
        "</article>"
    )


def _html_content_cards(section_title: str, text: str) -> str:
    blocks = _content_blocks(section_title, text)
    if not blocks:
        return _html_empty_card(f"No {section_title} content was saved in this snapshot.")
    visible = blocks[:18]
    overflow = blocks[18:]
    cards = "".join(
        _html_content_card(section_title, block, index)
        for index, block in enumerate(visible)
    )
    if overflow:
        overflow_text = "\n\n".join("\n".join(block) for block in overflow)
        cards += (
            "<details class=\"section-overflow\">"
            f"<summary>Additional captured text ({len(overflow)} more blocks)</summary>"
            f"{_html_plain_paragraphs(overflow_text)}"
            "</details>"
        )
    return cards


def _content_blocks(section_title: str, text: str) -> list[list[str]]:
    raw_lines = str(text or "").replace("\r\n", "\n").replace("\r", "\n").split("\n")
    blocks: list[list[str]] = []
    current: list[str] = []
    for line in raw_lines:
        if not line.strip():
            if current:
                blocks.append(current)
                current = []
            continue
        current.append(line.rstrip())
    if current:
        blocks.append(current)

    expanded: list[list[str]] = []
    for block in blocks:
        expanded.extend(_split_large_block(section_title, block))
    cleaned: list[list[str]] = []
    for block in expanded:
        clean = _clean_content_block(section_title, block)
        if clean:
            cleaned.append(clean)
    return cleaned


def _split_large_block(section_title: str, block: list[str]) -> list[list[str]]:
    clean = _clean_content_block(section_title, block)
    if len(clean) <= 12:
        return [clean]
    pieces: list[list[str]] = []
    current: list[str] = []
    for index, line in enumerate(clean):
        if index > 0 and current and _looks_card_heading(line):
            pieces.append(current)
            current = []
        current.append(line)
    if current:
        pieces.append(current)
    if len(pieces) == 1 and len(clean) > 18:
        return [clean[index:index + 8] for index in range(0, len(clean), 8)]
    return pieces


def _clean_content_block(section_title: str, block: list[str]) -> list[str]:
    lines = _trim_lines(block)
    while lines and (_is_separator_line(lines[0]) or _canonical_tab_title(lines[0]) == section_title):
        lines = lines[1:]
    while lines and _is_separator_line(lines[0]):
        lines = lines[1:]
    return _trim_lines(lines)


def _html_content_card(section_title: str, block: list[str], index: int) -> str:
    lines = _clean_content_block(section_title, block)
    if not lines:
        return ""
    card_title = "Captured Read" if index == 0 else f"Captured Detail {index + 1}"
    body_lines = lines
    first = lines[0].strip()
    if len(lines) > 1 and _is_separator_line(lines[1]):
        card_title = first.rstrip(":") or card_title
        body_lines = lines[2:]
    elif len(lines) == 1:
        label_value = _split_label_value(first)
        if label_value is not None:
            card_title, value = label_value
            body_lines = [value]
    elif first.endswith(":") and len(first) <= 90:
        card_title = first.rstrip(":")
        body_lines = lines[1:]
    elif _looks_card_heading(first):
        card_title = first.rstrip(":")
        body_lines = lines[1:]
    body_lines = _trim_lines(body_lines) or lines
    wide = " wide" if _body_should_be_wide(body_lines) else ""
    return (
        f"<article class=\"content-card{wide}\">"
        f"<h3>{escape(_short_text(card_title, 90))}</h3>"
        f"{_html_body_from_lines(body_lines)}"
        "</article>"
    )


def _html_body_from_lines(lines: list[str]) -> str:
    clean = [line.strip() for line in lines if line.strip() and not _is_separator_line(line)]
    if not clean:
        return "<p>--</p>"
    markdown_table = _markdown_table_html(clean)
    if markdown_table:
        return markdown_table
    kv_rows = _kv_rows(clean)
    if len(kv_rows) >= 2 and len(kv_rows) >= max(2, int(len(clean) * 0.6)):
        return _html_kv_table(kv_rows, css_class="mini-table")
    bullet_rows = [_clean_bullet(line) for line in clean if _is_bullet_line(line)]
    if len(bullet_rows) >= 2 and len(bullet_rows) >= max(2, int(len(clean) * 0.65)):
        return "<ul>" + "".join(f"<li>{escape(row)}</li>" for row in bullet_rows) + "</ul>"
    return _html_plain_paragraphs(" ".join(clean))


def _html_plain_paragraphs(text: str) -> str:
    chunks = _paragraph_chunks(" ".join(str(text or "").split()))
    if not chunks:
        return "<p>--</p>"
    return "".join(f"<p>{escape(chunk)}</p>" for chunk in chunks)


def _markdown_table_html(lines: list[str]) -> str:
    if len(lines) < 2 or not all("|" in line for line in lines):
        return ""
    rows: list[list[str]] = []
    for line in lines:
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        if cells and all(re.fullmatch(r":?-{2,}:?", cell.replace(" ", "")) for cell in cells):
            continue
        if len(cells) < 2:
            return ""
        rows.append(cells)
    if len(rows) < 2:
        return ""
    header = rows[0]
    body_rows = rows[1:]
    column_count = len(header)
    if any(len(row) != column_count for row in body_rows):
        return ""
    head_html = "<thead><tr>" + "".join(f"<th>{escape(cell)}</th>" for cell in header) + "</tr></thead>"
    body_html = "<tbody>" + "".join(
        "<tr>" + "".join(f"<td>{escape(cell)}</td>" for cell in row) + "</tr>"
        for row in body_rows
    ) + "</tbody>"
    return f"<table class=\"markdown-table\">{head_html}{body_html}</table>"


def _kv_rows(lines: list[str]) -> list[tuple[str, str]]:
    rows: list[tuple[str, str]] = []
    for line in lines:
        split = _split_label_value(_clean_bullet(line) if _is_bullet_line(line) else line)
        if split is not None:
            rows.append(split)
    return rows


def _split_label_value(line: str) -> tuple[str, str] | None:
    match = re.match(r"^\s*([^:\n]{2,52}):\s*(.+?)\s*$", str(line or ""))
    if not match:
        return None
    label = match.group(1).strip()
    value = match.group(2).strip()
    if not label or not value or label.lower().startswith(("http", "https")):
        return None
    return label, value


def _paragraph_chunks(text: str, *, max_len: int = 520) -> list[str]:
    clean = str(text or "").strip()
    if not clean:
        return []
    sentences = re.split(r"(?<=[.!?])\s+", clean)
    chunks: list[str] = []
    current = ""
    for sentence in sentences:
        if len(sentence) > max_len:
            if current:
                chunks.append(current.strip())
                current = ""
            chunks.extend(_word_chunks(sentence, max_len=max_len))
            continue
        candidate = f"{current} {sentence}".strip()
        if current and len(candidate) > max_len:
            chunks.append(current.strip())
            current = sentence
        else:
            current = candidate
    if current:
        chunks.append(current.strip())
    return chunks


def _word_chunks(text: str, *, max_len: int) -> list[str]:
    words = str(text or "").split()
    chunks: list[str] = []
    current = ""
    for word in words:
        candidate = f"{current} {word}".strip()
        if current and len(candidate) > max_len:
            chunks.append(current)
            current = word
        else:
            current = candidate
    if current:
        chunks.append(current)
    return chunks


def _body_should_be_wide(lines: list[str]) -> bool:
    return bool(_markdown_table_html([line.strip() for line in lines if line.strip()])) or len(lines) >= 7


def _looks_card_heading(line: str) -> bool:
    clean = str(line or "").strip()
    if not clean or _is_separator_line(clean) or _is_bullet_line(clean) or "|" in clean:
        return False
    if len(clean) > 92:
        return False
    if _split_label_value(clean) is not None:
        return False
    if clean.endswith(":"):
        return True
    if clean[-1:] in {".", "!", "?"}:
        return False
    return bool(re.search(r"[A-Za-z]", clean))


def _is_bullet_line(line: str) -> bool:
    return bool(re.match(r"^\s*(?:[-*]|\d+[.)])\s+", str(line or "")))


def _clean_bullet(line: str) -> str:
    return re.sub(r"^\s*(?:[-*]|\d+[.)])\s+", "", str(line or "")).strip()


def _is_separator_line(line: str) -> bool:
    clean = str(line or "").strip()
    return bool(clean) and set(clean) <= {"=", "-", "_", " "}


def _trim_lines(lines: Iterable[str]) -> list[str]:
    output = [str(line).rstrip() for line in lines]
    while output and not output[0].strip():
        output.pop(0)
    while output and not output[-1].strip():
        output.pop()
    return output


def _raw_snapshot_json(record: Mapping[str, Any]) -> str:
    payload = {
        "snapshot_id": record.get("snapshot_id"),
        "broker": record.get("broker"),
        "created_at": record.get("created_at"),
        "symbol": record.get("symbol"),
        "order_status": record.get("order_status"),
        "preview_status": record.get("preview_status"),
        "order_id": record.get("order_id"),
        "order_location": record.get("order_location"),
        "order_ticket": record.get("order_ticket"),
        "option_details": record.get("option_details"),
        "market_snapshot": record.get("market_snapshot"),
        "raw_order_json": record.get("raw_order_json"),
        "raw_preview_response": record.get("raw_preview_response"),
        "schwab_response_text": record.get("schwab_response_text"),
    }
    return json.dumps(sanitize_for_storage(payload), ensure_ascii=True, indent=2, sort_keys=True)


def _labeled_value(text: str, labels: tuple[str, ...]) -> str:
    source = str(text or "")
    for label in labels:
        pattern = re.compile(rf"^\s*(?:[-*]\s*)?{re.escape(label)}\s*[:|-]\s*(.+?)\s*$", re.IGNORECASE | re.MULTILINE)
        match = pattern.search(source)
        if match:
            return match.group(1).strip()
    return ""


def _first_meaningful_line(text: str) -> str:
    for line in str(text or "").splitlines():
        clean = line.strip(" -")
        if clean and not _is_separator_line(clean) and _canonical_tab_title(clean) is None:
            return clean
    return ""


def _first_nonempty_text(values: Iterable[str], fallback: str) -> str:
    for value in values:
        clean = str(value or "").strip()
        if clean:
            return clean
    return fallback


def _short_text(text: Any, limit: int) -> str:
    clean = " ".join(str(text or "").split())
    if len(clean) <= limit:
        return clean or "--"
    return clean[: max(0, limit - 3)].rstrip() + "..."


def _display_value(value: Any) -> str:
    if value is None or value == "":
        return "--"
    if isinstance(value, float):
        return f"{value:g}"
    return str(value)


def _status_tone(value: Any) -> str:
    text = str(value or "").lower()
    if any(token in text for token in ("saved", "filled", "working", "accepted", "open")):
        return "good"
    if any(token in text for token in ("missing", "rejected", "canceled", "cancelled", "failed", "expired")):
        return "bad"
    if any(token in text for token in ("review", "pending", "queued", "mixed")):
        return "warn"
    return "neutral"


def _value_tone(value: Any) -> str:
    text = str(value or "").lower()
    if any(token in text for token in ("avoid", "bear", "weak", "negative", "bad", "high risk", "risk high", "broken", "missing")):
        return "bad"
    if any(token in text for token in ("wait", "mixed", "watch", "review", "medium", "elevated", "warning")):
        return "warn"
    if any(token in text for token in ("buy", "bull", "constructive", "strong", "positive", "good", "allow", "support")):
        return "good"
    return "info"


def _safe_id(value: str) -> str:
    clean = re.sub(r"[^a-z0-9]+", "-", str(value or "").lower()).strip("-")
    return clean or "section"


def _is_screenshot_key(key: str) -> bool:
    compact = re.sub(r"[^a-z0-9]", "", key.lower())
    return any(part.replace("_", "") in compact for part in _SCREENSHOT_KEY_PARTS)


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
    return _close_number(_first_number(ticket.get("limit_price")), _first_number(identity.get("limit_price")),
                         tolerance=0.01)


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
