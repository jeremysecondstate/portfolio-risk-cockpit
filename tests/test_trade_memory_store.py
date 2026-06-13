from __future__ import annotations

from pathlib import Path

from app.storage.trade_memory_store import render_trade_thesis_html, sanitize_for_storage, write_trade_thesis_report


def _snapshot(**overrides: object) -> dict[str, object]:
    base: dict[str, object] = {
        "snapshot_id": "20260612-153000-AAPL-abc123",
        "created_at": "2026-06-12T15:30:00+00:00",
        "broker": "Schwab",
        "symbol": "AAPL",
        "instrument_type": "equity",
        "source": "manual_snapshot",
        "thesis_status": "saved",
        "order_status": "OPEN",
        "preview_status": "OK",
        "order_id": "987654",
        "plain_english_summary": "Constructive setup if price holds support and confirmation improves.",
        "original_posture": "Constructive / defined-risk only",
        "order_ticket": {
            "side": "BUY",
            "quantity": 10,
            "order_type": "LIMIT",
            "limit_price": 195.25,
            "time_in_force": "DAY",
            "estimated_notional": 1952.50,
        },
        "raw_order_json": {
            "orderType": "LIMIT",
            "price": 195.25,
            "orderLegCollection": [{"instruction": "BUY", "quantity": 10}],
        },
        "raw_preview_response": {"status": "OK"},
        "schwab_response_text": "Schwab Status: OK",
        "tab_summaries": {
            "Overview": "Verdict: Constructive\n\nWhat Matters Most:\n- Momentum positive\n- Invalidation below 180",
            "Evidence Desk": "Scores\nCategory: Evidence\nConfidence: High\n\n| Scenario | Read |\n| --- | --- |\n| Base | Hold |",
            "Technicals": "Technical Read: Bullish above VWAP\nSupport: 190\nResistance: 205",
            "Risk Scenarios": "Risk Level: Medium\n- Gap below support would weaken the trade.",
            "Options Strategy": "Preferred vehicle: Equity first, options only after confirmation.",
            "Greeks": "Delta: Not applicable for equity snapshot.\nTheta: Not applicable.",
            "Earnings / News": "Freshness verdict: No same-day earnings event detected.",
            "Fundamentals": "Action bias: Supports owning small size.\nConfidence: Medium",
            "Macro Context": "Macro backdrop: Neutral liquidity conditions.",
        },
    }
    base.update(overrides)
    return base


def test_trade_memory_html_uses_workspace_shell_and_header_data() -> None:
    html = render_trade_thesis_html(_snapshot())

    assert "class=\"workspace-shell\"" in html
    assert "Schwab Research + Risk Workspace" in html
    assert "<h1>AAPL</h1>" in html
    assert "Saved snapshot, not live advice." in html
    assert "Order Ticket" in html
    assert "BUY" in html
    assert "OPEN" in html
    assert "At a Glance" in html
    assert "Technical Read" in html
    assert "Action Bias" in html
    assert "Risk Level" in html
    assert "<details class=\"raw-details\">" in html


def test_trade_memory_html_renders_saved_tabs_as_visual_sections() -> None:
    html = render_trade_thesis_html(_snapshot())

    for section_id in (
        "overview",
        "evidence-desk",
        "technicals",
        "risk-scenarios",
        "options-strategy",
        "greeks",
        "earnings-news",
        "fundamentals",
        "macro-context",
    ):
        assert f"id=\"{section_id}\"" in html

    assert html.count("class=\"workspace-section\"") == 9
    assert "class=\"content-card" in html
    assert "<li>Momentum positive</li>" in html
    assert "class=\"mini-table\"" in html
    assert "class=\"markdown-table\"" in html
    assert "<td>Base</td><td>Hold</td>" in html


def test_trade_memory_html_escapes_content_and_deemphasizes_sanitized_raw_payload() -> None:
    html = render_trade_thesis_html(
        _snapshot(
            symbol="x<script>",
            plain_english_summary="Summary with <script>alert(1)</script> markup.",
            tab_summaries={"Overview": "Verdict: <script>alert(1)</script>"},
            raw_order_json={
                "client_secret": "TOPSECRET",
                "screenshotBase64": "PNGDATA",
                "nested": {"authorization": "Bearer TOPSECRET", "image_data": "PNGDATA"},
            },
            schwab_response_text="Authorization: Bearer abc.def.ghi",
        )
    )

    assert "<script" not in html.lower()
    assert "X&lt;SCRIPT&gt;" in html
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in html
    assert "TOPSECRET" not in html
    assert "PNGDATA" not in html
    assert "client_secret" not in html
    assert "screenshotBase64" not in html
    assert "Bearer [REDACTED]" in html
    assert "<summary>Raw Schwab/order JSON</summary>" in html


def test_trade_memory_html_supports_legacy_report_only_snapshots(tmp_path: Path) -> None:
    legacy_report = """
Overview
========
Verdict: Wait for confirmation before adding risk.

Technicals
==========
Technical Read: Bullish above 200 with support near 190.

Risk: Losing support would invalidate the constructive thesis.
"""
    snapshot = {
        "snapshot_id": "legacy-1",
        "created_at": "2026-06-01T12:00:00+00:00",
        "symbol": "MSFT",
        "research_report_text": legacy_report,
    }

    html = render_trade_thesis_html(snapshot)
    path = write_trade_thesis_report(snapshot, tmp_path)

    assert "Wait for confirmation before adding risk." in html
    assert "Bullish above 200" in html
    assert "No Greeks content was saved in this snapshot." in html
    assert html.count("class=\"workspace-section\"") == 9
    assert path.exists()
    assert path.read_text(encoding="utf-8") == html


def test_sanitize_for_storage_drops_screenshot_like_blobs() -> None:
    clean = sanitize_for_storage(
        {
            "symbol": "AAPL",
            "screenshot": "PNGDATA",
            "screen_shot": "PNGDATA",
            "nested": {"image_base64": "PNGDATA", "safe": "kept"},
        }
    )

    assert clean == {"symbol": "AAPL", "nested": {"safe": "kept"}}
