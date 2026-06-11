from __future__ import annotations

import json
import os
from types import SimpleNamespace
from unittest.mock import patch

from app.analytics.symbol_chat import (
    SYMBOL_CHAT_REQUEST_CHAR_LIMIT,
    SYMBOL_CHAT_QUICK_PROMPTS,
    SYMBOL_CHAT_SYSTEM_PROMPT,
    OpenAiSymbolChatClient,
    OpenAiSymbolChatError,
    SymbolChatContext,
    SymbolChatSession,
    build_symbol_chat_context,
    render_symbol_chat_transcript_markdown,
    save_symbol_chat_transcript,
    symbol_chat_request_payload,
)
from app.analytics.symbol_web_enrichment import (
    SecEdgarSymbolWebEnrichmentProvider,
    SymbolWebEnrichment,
    SymbolWebSource,
    symbol_web_enrichment_to_payload,
)
from app.analytics.technical_analysis import Candle, build_technical_command_center_report
from app.core.portfolio import Portfolio, Position
from app.data.sec_edgar import SecCompany, SecFiling
from app.ui import schwab_trading_tab
from app.ui.symbol_chat_window import SymbolChatWindow


class FakeSecClient:
    def __init__(self, *, fail_company: bool = False, filings: list[object] | None = None) -> None:
        self.fail_company = fail_company
        self.filings = filings if filings is not None else []

    def company_for_ticker(self, ticker: str):
        if self.fail_company:
            raise LookupError(f"{ticker} not found")
        return SimpleNamespace(ticker=ticker, title=f"{ticker} Corp.")

    def recent_filings(self, ticker: str, *, forms=None, limit: int = 16):
        return self.filings[:limit]


class FakeBroker:
    def __init__(self, portfolio: Portfolio) -> None:
        self._portfolio = portfolio

    def get_portfolio(self) -> Portfolio:
        return self._portfolio


class FakeResponses:
    def __init__(self, *, answer: str = "Analysis-only answer.") -> None:
        self.answer = answer
        self.calls: list[dict] = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        return SimpleNamespace(output_text=self.answer, id=f"sym_resp_{len(self.calls)}")


class FakeOpenAiClient:
    def __init__(self, *, answer: str = "Analysis-only answer.") -> None:
        self.responses = FakeResponses(answer=answer)


class FakeStatus:
    def __init__(self) -> None:
        self.value = ""

    def set(self, value: str) -> None:
        self.value = value


class FakeText:
    def __init__(self, value: str) -> None:
        self.value = value

    def get(self, *_args):
        return self.value


class FakeLabel:
    def __init__(self, value: str) -> None:
        self.value = value

    def cget(self, option: str) -> str:
        assert option == "text"
        return self.value


class FakeTree:
    def __init__(self, columns, values=None, selection=("row1",), rows=None) -> None:
        self._columns = columns
        self._rows = rows if rows is not None else {"row1": values}
        self._selection = selection

    def selection(self):
        return self._selection

    def get_children(self):
        return tuple(self._rows)

    def item(self, row_id, option):
        assert option == "values"
        return self._rows[row_id]

    def __getitem__(self, key):
        assert key == "columns"
        return self._columns


def _candles(count: int, *, start: float = 100.0, step: float = 0.25) -> list[Candle]:
    rows: list[Candle] = []
    price = start
    for index in range(count):
        close = price + step
        rows.append(
            Candle(
                datetime_ms=index,
                open=price,
                high=max(price, close) + 0.50,
                low=min(price, close) - 0.50,
                close=close,
                volume=1_000_000 + index,
            )
        )
        price = close
    return rows


def test_symbol_chat_context_builds_symbol_only_bundle() -> None:
    context = build_symbol_chat_context(
        "amd",
        sec_client=FakeSecClient(fail_company=True),
        trade_memory_store=SimpleNamespace(find_snapshots_for_symbol=lambda _symbol: []),
    )

    assert context.symbol == "AMD"
    assert context.company_name == ""
    assert context.schwab_position == {}
    assert context.quote_snapshot == {}
    assert any("company_name" in item for item in context.source_metadata["unavailable"])


def test_symbol_chat_context_reuses_position_quote_technical_orders_and_filings() -> None:
    portfolio = Portfolio(
        cash=12_000,
        positions={
            "AMD": Position(
                symbol="AMD",
                quantity=10,
                average_cost=100,
                last_price=120,
                day_profit_loss=50,
            )
        },
    )
    report = build_technical_command_center_report("AMD", {"daily_1y": _candles(90), "timing_5m": _candles(80, start=118, step=0.05)})
    open_order = {
        "enteredTime": "2026-06-10T15:00:00Z",
        "orderId": "123",
        "status": "WORKING",
        "orderType": "LIMIT",
        "price": "119.50",
        "orderLegCollection": [{"instruction": "BUY", "quantity": 5, "instrument": {"symbol": "AMD"}}],
    }
    recent_order = {
        "enteredTime": "2026-06-09T15:00:00Z",
        "orderId": "122",
        "status": "FILLED",
        "orderType": "LIMIT",
        "price": "118.00",
        "orderLegCollection": [{"instruction": "BUY", "quantity": 3, "instrument": {"symbol": "AMD"}}],
    }
    filing = SimpleNamespace(
        form="10-Q",
        filing_date="2026-05-01",
        report_date="2026-03-31",
        description="Quarterly report",
        accession_number="0000000000-26-000001",
        filing_url="https://example.test/amd-10q.htm",
    )
    app = SimpleNamespace(
        broker=FakeBroker(portfolio),
        schwab_research_last_payload=SimpleNamespace(symbol="AMD", command_center_report=report),
        schwab_open_orders_table=SimpleNamespace(schwab_open_orders_by_iid={"open1": open_order}),
        schwab_recent_orders_table=SimpleNamespace(schwab_recent_orders_by_iid={"recent1": recent_order}),
    )
    session = SimpleNamespace(get_quote=lambda _symbol: (200, {"AMD": {"quote": {"lastPrice": 121.25, "bidPrice": 121.1, "askPrice": 121.4}}}))

    context = build_symbol_chat_context(
        "AMD",
        app_context=app,
        schwab_session=session,
        sec_client=FakeSecClient(filings=[filing]),
        trade_memory_store=SimpleNamespace(find_snapshots_for_symbol=lambda _symbol: []),
    )

    assert context.company_name == "AMD Corp."
    assert context.schwab_position["is_held"] is True
    assert context.schwab_position["quantity"] == 10
    assert context.quote_snapshot["last"] == 121.25
    assert context.technical_analysis["overall_read"]
    assert "TECHNICAL COMMAND CENTER" in context.technical_analysis["summary_text"]
    assert "Best action:" not in context.technical_analysis["summary_text"]
    assert "PLAIN-ENGLISH PLAN" not in context.technical_analysis["summary_text"]
    assert context.open_orders_summary[0]["order_id"] == "123"
    assert context.recent_orders_summary[0]["status"] == "FILLED"
    assert context.recent_filings_summary[0]["form"] == "10-Q"


def test_symbol_chat_context_extracts_visible_schwab_holdings_table_position() -> None:
    holdings = FakeTree(
        ("symbol", "type", "qty", "last", "value", "pnl"),
        rows={
            "cash": ("USD (Schwab)", "Cash", "101185.40", "$1.00", "$101,185.40", "--"),
            "goog": ("GOOG", "Equity", "20", "$353.65", "$7,073.00", "$2,426.77"),
        },
        selection=("goog",),
    )
    holdings._day_pnl_table = FakeTree(("day_pnl",), rows={"cash": ("--",), "goog": ("-$206.40",)}, selection=("goog",))
    app = SimpleNamespace(
        schwab_workspace_holdings_table=holdings,
        cash_value_label=FakeLabel("$101,185.40"),
        positions_value_label=FakeLabel("$33,057.13"),
        total_value_label=FakeLabel("$134,242.53"),
        pnl_value_label=FakeLabel("$2,147.90"),
    )

    context = build_symbol_chat_context(
        "goog",
        app_context=app,
        sec_client=FakeSecClient(),
        trade_memory_store=SimpleNamespace(find_snapshots_for_symbol=lambda _symbol: []),
    )

    position = context.schwab_position
    assert position["is_held"] is True
    assert position["source"] == "selected_schwab_holdings_table"
    assert position["quantity"] == 20
    assert position["last_price"] == 353.65
    assert position["market_value"] == 7073.0
    assert position["unrealized_pnl"] == 2426.77
    assert position["day_pnl"] == -206.40
    assert position["portfolio_cash"] == 101185.40
    assert position["portfolio_value"] == 134242.53
    assert abs(position["portfolio_weight"] - (7073.0 / 134242.53)) < 0.000001
    assert any("selected_schwab_holdings_table" in item for item in context.source_metadata["available"])


def test_symbol_chat_context_reuses_research_workspace_context_in_payload() -> None:
    badge = lambda title, label, why: SimpleNamespace(title=title, label=label, status="info", score=67, why=why)
    decision = SimpleNamespace(
        overall=badge("Overall Read", "Constructive", "trend and evidence are supportive"),
        risk_level=badge("Risk Level", "Moderate", "position size is manageable"),
        action_bias=badge("Action Bias", "Protect gains", "held winner with defined risk"),
        position_impact=badge("Position Impact", "Existing holding", "20 shares in portfolio"),
        macro_backdrop=badge("Macro Backdrop", "Neutral", "macro context is mixed"),
        thesis=SimpleNamespace(
            trade_judgment="Thesis read: constructive but verify catalysts.",
            recommendation="Keep analysis-only watchlist context.",
            preferred_vehicle="Stock",
            confidence="medium",
            invalidation="Break below the risk line would weaken the thesis.",
        ),
        summary=["Alphabet has a loaded deterministic workspace summary."],
        matters=["Search demand and AI capex matter most."],
        changes_view=["A material earnings miss would change the view."],
    )
    research_context = SimpleNamespace(
        symbol="GOOG",
        is_held=True,
        quantity=20,
        average_cost=232.31,
        last_price=353.65,
        market_value=7073.0,
        portfolio_value=134242.53,
        portfolio_weight=7073.0 / 134242.53,
        unrealized_pnl=2426.77,
        day_pnl=-206.40,
        cash_available=101185.40,
    )
    payload = SimpleNamespace(
        symbol="GOOG",
        company_name="Alphabet Inc.",
        context=research_context,
        indicators=SimpleNamespace(
            latest_close=353.65,
            trend="uptrend",
            momentum="positive",
            volatility="normal",
            support=340.0,
            resistance=360.0,
            week_52_high=360.0,
            week_52_low=200.0,
            rsi_14=61.5,
            atr_14=5.2,
            notes=["Momentum is positive but extended."],
        ),
        command_center_report=SimpleNamespace(
            symbol="GOOG",
            overall_read="Constructive trend",
            overall_score=67,
            confidence="medium",
            setup_classification=SimpleNamespace(setup="trend continuation", action_quality="watch for confirmation"),
            warnings=["Capital-structure scanner flag is unverified without filing text."],
        ),
        decision=decision,
        scenario_rows=[
            SimpleNamespace(scenario="+5%", symbol_price=371.33, position_pnl=353.65, portfolio_pnl_impact=0.0026, new_portfolio_value=134596.18)
        ],
        earnings_text="Earnings / News\nOfficial earnings digest loaded from deterministic sources.",
        fundamentals_text="Fundamentals\nRevenue and operating income context loaded from companyfacts.",
        macro_text="Official Macro Snapshot\nRates and inflation context loaded.",
        statuses=[SimpleNamespace(source="SEC companyfacts", status="fresh", fetched_at="2026-06-11T00:00:00Z", message="XBRL loaded.")],
        recommendation_engine_read=SimpleNamespace(
            recommendation_label="Constructive / defined-risk only",
            confidence="medium",
            confidence_score=70,
            evidence_score=66,
            confidence_adjusted_score=63,
            why=("Evidence is constructive.",),
            warnings=("Do not overstate filing keyword flags.",),
            confirmation_lines=("Confirm above resistance.",),
            invalidation_lines=("Break below support.",),
            position_sizing_notes=("Existing 20-share position is visible.",),
            what_would_change=("Earnings miss.",),
            components=(),
            expected_reward_risk=SimpleNamespace(label="Reward/risk defined", reward_risk_ratio=1.8, planning_probability=0.55, summary="Planning EV is positive.", reward_line="Upside scenario.", risk_line="Support break."),
            data_confidence=SimpleNamespace(grade="B", score=75, reason="Core app data loaded.", missing=(), stale=()),
        ),
        operator_verdict=SimpleNamespace(
            primary_action="WATCH / PROTECT",
            primary_label="Held winner",
            confidence="medium",
            confidence_score=68,
            summary="Use deterministic analysis to frame the position.",
            right_now=SimpleNamespace(label="Right Now", action="WATCH", detail="Protect gains; no order action.", severity="info"),
            reasons=("Position is held and research context is loaded.",),
            warnings=("Analysis-only.",),
            what_would_change=("Fresh earnings data.",),
        ),
        option_chain_rows=[],
        option_chain_underlying_price=353.65,
        greek_summary=SimpleNamespace(source="Schwab option chain", plain_english=["Greeks loaded."], warnings=[]),
        security_kind="equity",
        reporting_profile="domestic",
    )
    app = SimpleNamespace(
        schwab_research_last_payload=payload,
        schwab_research_overview_text=FakeText("Overview Explanation\nPlain-English summary from the Research Workspace."),
        schwab_research_earnings_text=FakeText("Earnings text from widget."),
        schwab_research_fundamentals_text=FakeText("Fundamentals text from widget."),
        schwab_research_macro_text=FakeText("Macro text from widget."),
        schwab_research_status_var=SimpleNamespace(get=lambda: "GOOG research updated at 2026-06-11 09:30:00"),
    )

    context = build_symbol_chat_context(
        "GOOG",
        app_context=app,
        sec_client=FakeSecClient(),
        trade_memory_store=SimpleNamespace(find_snapshots_for_symbol=lambda _symbol: []),
    )
    request_payload = symbol_chat_request_payload(context, "Generate an overview.", timeout_seconds=120)
    research = request_payload["symbol_context"]["research_workspace_context"]

    assert context.company_name == "Alphabet Inc."
    assert context.schwab_position["is_held"] is True
    assert context.schwab_position["quantity"] == 20
    assert research["portfolio_context"]["quantity"] == 20
    assert research["at_a_glance"]["thesis"]["trade_judgment"].startswith("Thesis read")
    assert "Official earnings digest" in research["earnings_news"]["summary_text"]
    assert "Research Workspace" in research["overview_text"]
    assert "research_workspace_context" in " ".join(context.source_metadata["available"])


def test_symbol_chat_validation_demotes_misaligned_quote_technical_52_week_and_option_context() -> None:
    payload = SimpleNamespace(
        symbol="SNDK",
        company_name="Sandisk Corp.",
        context=SimpleNamespace(symbol="SNDK", is_held=False, quantity=0, last_price=1887.19),
        quote={
            "SNDK": {
                "quote": {
                    "lastPrice": 1887.19,
                    "mark": 1887.19,
                    "quoteTimeInLong": 1781213400000,
                }
            }
        },
        indicators=SimpleNamespace(
            latest_close=1643.23,
            trend="mixed",
            momentum="neutral",
            volatility="elevated",
            support=1625.0,
            resistance=1661.0,
            week_52_high=1861.0,
            week_52_low=900.0,
            rsi_14=50.0,
            atr_14=25.0,
            notes=[],
        ),
        command_center_report=SimpleNamespace(
            symbol="SNDK",
            overall_read="Mixed technical context",
            overall_score=52,
            confidence="low",
            setup_classification=SimpleNamespace(
                setup="range",
                action_quality="wait",
                confirmation_level=1661.0,
                invalidation_level=1625.0,
            ),
            warnings=[],
        ),
        option_chain_rows=[],
        option_chain_underlying_price=1643.23,
        greek_summary=SimpleNamespace(source="Schwab option chain", plain_english=["Greeks loaded."], warnings=[]),
    )
    app = SimpleNamespace(schwab_research_last_payload=payload)

    context = build_symbol_chat_context(
        "SNDK",
        app_context=app,
        sec_client=FakeSecClient(),
        trade_memory_store=SimpleNamespace(find_snapshots_for_symbol=lambda _symbol: []),
    )
    request_payload = symbol_chat_request_payload(context, "Summarize the main risks.", timeout_seconds=120)
    symbol_context = request_payload["symbol_context"]
    validation = symbol_context["context_validation"]
    codes = {issue["code"] for issue in validation["issues"]}
    technicals = symbol_context["research_workspace_context"]["technicals"]
    options = symbol_context["research_workspace_context"]["options_strategy"]

    assert validation["status"] == "warning"
    assert "quote_technical_price_mismatch" in codes
    assert "quote_52_week_high_mismatch" in codes
    assert "technical_levels_track_stale_close" in codes
    assert "option_underlying_mismatch" in codes
    assert technicals["validation_status"] == "stale_or_misaligned"
    assert "support" not in technicals["indicator_snapshot"]
    assert "resistance" not in technicals["indicator_snapshot"]
    assert "week_52_high" not in technicals["indicator_snapshot"]
    assert technicals["demoted_indicator_levels"]["values"]["support"] == 1625.0
    assert options["validation_status"] == "stale_or_misaligned"
    assert "underlying_price" not in options
    assert "context_validation warnings" in " ".join(request_payload["grounding_rules"])

    fake_openai = FakeOpenAiClient(answer="Quote and technical levels conflict; stale levels are not used as current support.")
    session = SymbolChatSession(context, chat_client=OpenAiSymbolChatClient(openai_client=fake_openai, model="gpt-test"))
    response = session.ask("Summarize the main risks.")
    markdown = render_symbol_chat_transcript_markdown(session)

    assert any("context_validation_issue=warning | quote_technical_price_mismatch" in item for item in response.source_debug)
    assert "## Context Validation" in markdown
    assert "technical_levels: stale_or_misaligned" in markdown


def test_symbol_chat_validation_flags_stale_quote_and_candle_timestamps() -> None:
    context = SymbolChatContext(
        symbol="AMD",
        quote_snapshot={"symbol": "AMD", "last": 100.0, "quote_timestamp": "2026-05-30T20:00:00+00:00"},
        research_workspace_context={
            "source": "schwab_research_workspace",
            "symbol": "AMD",
            "technicals": {
                "indicator_snapshot": {
                    "latest_close": 100.0,
                    "latest_candle_timestamp": "2026-05-30T20:00:00+00:00",
                    "atr_percent": 2.0,
                    "support": 98.0,
                    "resistance": 102.0,
                }
            },
        },
        source_metadata={"loaded_at_utc": "2026-06-11T20:00:00+00:00"},
    )

    request_payload = symbol_chat_request_payload(context, "Check freshness.", timeout_seconds=120)
    validation = request_payload["symbol_context"]["context_validation"]
    codes = {issue["code"] for issue in validation["issues"]}
    technicals = request_payload["symbol_context"]["research_workspace_context"]["technicals"]

    assert "stale_quote_timestamp" in codes
    assert "stale_candle_timestamp" in codes
    assert technicals["validation_status"] == "stale_or_misaligned"


def test_symbol_chat_final_payload_budget_is_enforced_after_web_enrichment_and_validation() -> None:
    huge_text = "SEC filing summary detail. " * 7000
    context = SymbolChatContext(
        symbol="HUGE",
        quote_snapshot={"symbol": "HUGE", "last": 250.0},
        research_workspace_context={
            "source": "schwab_research_workspace",
            "symbol": "HUGE",
            "portfolio_context": {"symbol": "HUGE", "is_held": False},
            "at_a_glance": {"summary": ["Core deterministic read should remain."]},
            "technicals": {"indicator_snapshot": {"latest_close": 200.0, "support": 198.0, "resistance": 205.0, "week_52_high": 220.0}},
            "earnings_news": {"summary_text": huge_text},
            "fundamentals": {"summary_text": huge_text},
            "macro_context": {"summary_text": huge_text},
            "overview_text": huge_text,
        },
        technical_analysis={"source": "test", "summary_text": huge_text, "levels": {"support": 198.0, "resistance": 205.0}},
        web_enrichment={
            "mode": "enabled",
            "enabled": True,
            "status": "available",
            "provider_name": "mock_web",
            "sources": [{"title": f"Source {index}", "url": f"https://example.test/{index}"} for index in range(30)],
            "filing_summaries": [{"form": "10-Q", "summary": huge_text, "url": "https://example.test/10q"} for _ in range(8)],
            "recent_market_news": {
                "status": "unavailable",
                "provider_configured": False,
                "provider_name": "none",
                "reason": "No recent market/news provider is configured in this build.",
                "sources": [],
            },
        },
        saved_thesis_or_notes=huge_text,
        source_metadata={"loaded_at_utc": "2026-06-11T20:00:00+00:00", "available": [huge_text], "unavailable": [huge_text]},
    )

    request_payload = symbol_chat_request_payload(context, "Fit the budget.", timeout_seconds=120)
    serialized = json.dumps(request_payload, ensure_ascii=True, sort_keys=True, indent=2)

    assert len(serialized) <= SYMBOL_CHAT_REQUEST_CHAR_LIMIT
    assert request_payload["request_budget"]["pre_trim_payload_chars"] > SYMBOL_CHAT_REQUEST_CHAR_LIMIT
    assert request_payload["request_budget"]["final_payload_chars"] <= SYMBOL_CHAT_REQUEST_CHAR_LIMIT
    assert request_payload["request_budget"]["budget_trimmed"] is True
    assert request_payload["symbol_context"]["context_validation"]["status"] == "warning"


def test_symbol_chat_optional_web_enrichment_provider_is_labeled_separately() -> None:
    context = build_symbol_chat_context(
        "AMD",
        sec_client=FakeSecClient(),
        trade_memory_store=SimpleNamespace(find_snapshots_for_symbol=lambda _symbol: []),
        use_web_enrichment=True,
        web_enrichment_provider=lambda symbol: {
            "summary": f"Public web summary for {symbol}.",
            "sources": [{"title": "Example source", "url": "https://example.test/amd"}],
        },
    )

    assert context.web_enrichment["status"] == "available"
    assert context.web_enrichment["summary"] == "Public web summary for AMD."
    assert context.source_metadata["web_enrichment_mode"] == "requested"
    assert any("web_enrichment" in item for item in context.source_metadata["available"])


def test_symbol_chat_web_enrichment_unavailable_state_is_truthful() -> None:
    with patch.dict(os.environ, {"SYMBOL_CHAT_WEB_ENRICHMENT_PROVIDER": "none"}):
        context = build_symbol_chat_context(
            "AMD",
            sec_client=FakeSecClient(),
            trade_memory_store=SimpleNamespace(find_snapshots_for_symbol=lambda _symbol: []),
            use_web_enrichment=True,
        )

    payload = symbol_chat_request_payload(context, "Summarize risks.", timeout_seconds=120)
    web = payload["symbol_context"]["web_enrichment"]
    market_news = web["recent_market_news"]
    session = SymbolChatSession(context, chat_client=OpenAiSymbolChatClient(openai_client=FakeOpenAiClient(), model="gpt-test"))
    markdown = render_symbol_chat_transcript_markdown(session)

    assert web["mode"] == "requested_unavailable"
    assert web["status"] == "unavailable"
    assert web["provider_configured"] is False
    assert context.source_metadata["web_enrichment_provider"] == "none"
    assert "No web-enrichment provider is configured" in web["reason"]
    assert "## Web Enrichment" in markdown
    assert "Requested: yes" in markdown
    assert "Provider configured: no" in markdown
    assert "Status: unavailable" in markdown
    assert market_news["status"] == "unavailable"
    assert market_news["provider_configured"] is False
    assert "No recent market/news provider is configured" in market_news["reason"]
    assert "Recent Market/News:" in markdown
    assert "No recent market/news provider is configured" in markdown


def test_symbol_chat_structured_web_provider_keeps_payload_layers_separate() -> None:
    class StructuredProvider:
        provider_name = "mock_public_web"

        def enrich(self, symbol: str, *, company_name: str = "", recent_filings=()):
            assert symbol == "AMD"
            assert company_name == "AMD Corp."
            assert list(recent_filings)
            return SymbolWebEnrichment(
                symbol=symbol,
                company_name=company_name,
                provider_name=self.provider_name,
                generated_at_utc="2026-06-11T12:00:00+00:00",
                company_profile={"business": "Public source says AMD designs semiconductors."},
                recent_news=(
                    SymbolWebSource(
                        title="AMD public news item",
                        url="https://example.test/amd-news",
                        publisher="Example News",
                        published_at="2026-06-10",
                        source_type="public_news",
                        snippet="Snippet only; verify source.",
                    ),
                ),
                sources=(
                    SymbolWebSource(
                        title="AMD public news item",
                        url="https://example.test/amd-news",
                        publisher="Example News",
                        published_at="2026-06-10",
                        source_type="public_news",
                    ),
                ),
                source_debug=("provider=mock_public_web", "sources=1"),
            )

    filing = SimpleNamespace(
        form="10-Q",
        filing_date="2026-05-01",
        report_date="2026-03-31",
        description="Quarterly report",
        accession_number="0000000000-26-000001",
        filing_url="https://example.test/amd-10q.htm",
    )
    portfolio = Portfolio(cash=1_000, positions={"AMD": Position(symbol="AMD", quantity=5, average_cost=100, last_price=120)})
    app = SimpleNamespace(broker=FakeBroker(portfolio))
    context = build_symbol_chat_context(
        "AMD",
        app_context=app,
        sec_client=FakeSecClient(filings=[filing]),
        trade_memory_store=SimpleNamespace(find_snapshots_for_symbol=lambda _symbol: []),
        use_web_enrichment=True,
        web_enrichment_provider=StructuredProvider(),
    )
    request_payload = symbol_chat_request_payload(context, "Summarize risks.", timeout_seconds=120)
    web = request_payload["symbol_context"]["web_enrichment"]

    assert request_payload["symbol_context"]["schwab_position"]["is_held"] is True
    assert request_payload["symbol_context"]["schwab_position"]["quantity"] == 5
    assert web["provider_name"] == "mock_public_web"
    assert web["status"] == "available"
    assert web["sources"][0]["url"] == "https://example.test/amd-news"
    assert "Do not use web_enrichment to infer account holdings" in " ".join(request_payload["grounding_rules"])

    fake_openai = FakeOpenAiClient(answer="Uses app-local position separately from web source.")
    session = SymbolChatSession(context, chat_client=OpenAiSymbolChatClient(openai_client=fake_openai, model="gpt-test"))
    response = session.ask("Summarize risks.")
    markdown = render_symbol_chat_transcript_markdown(session)

    assert response.source_mode == "symbol_context_bundle_plus_web_enrichment"
    assert "web_provider=mock_public_web" in response.source_debug
    assert any("web_source_1=public_news | Example News | AMD public news item | https://example.test/amd-news" == item for item in response.source_debug)
    assert "Provider: mock public web" in markdown
    assert "AMD public news item" in markdown
    assert "https://example.test/amd-news" in markdown


def test_symbol_chat_recent_market_news_provider_path_is_included_with_web_research() -> None:
    class MarketNewsProvider:
        provider_name = "mock_recent_market_news"

        def enrich_market_news(self, symbol: str, *, company_name: str = ""):
            assert symbol == "AMD"
            assert company_name == "AMD Corp."
            return {
                "symbol": symbol,
                "provider_name": self.provider_name,
                "status": "available",
                "market_snapshot": {
                    "latest_close": 121.5,
                    "latest_candle_timestamp": "2026-06-11T19:55:00+00:00",
                    "quote_timestamp": "2026-06-11T19:56:00+00:00",
                    "price_move_percent": 1.2,
                    "volume_context": "above 20-day average",
                },
                "recent_news": [
                    {
                        "title": "AMD market headline",
                        "url": "https://example.test/amd-headline",
                        "publisher": "Example News",
                        "published_at": "2026-06-11",
                        "source_type": "public_news",
                    }
                ],
                "sources": [
                    {
                        "title": "AMD market headline",
                        "url": "https://example.test/amd-headline",
                        "publisher": "Example News",
                        "published_at": "2026-06-11",
                        "source_type": "public_news",
                    }
                ],
                "source_debug": ["provider=mock_recent_market_news", "sources=1"],
            }

    context = build_symbol_chat_context(
        "AMD",
        sec_client=FakeSecClient(),
        trade_memory_store=SimpleNamespace(find_snapshots_for_symbol=lambda _symbol: []),
        use_web_enrichment=True,
        web_enrichment_provider=lambda symbol: {
            "summary": f"SEC-compatible public web summary for {symbol}.",
            "sources": [{"title": "Example source", "url": "https://example.test/amd"}],
        },
        market_news_provider=MarketNewsProvider(),
    )
    payload = symbol_chat_request_payload(context, "Summarize market context.", timeout_seconds=120)
    market_news = payload["symbol_context"]["web_enrichment"]["recent_market_news"]
    markdown = render_symbol_chat_transcript_markdown(SymbolChatSession(context, chat_client=OpenAiSymbolChatClient(openai_client=FakeOpenAiClient(), model="gpt-test")))

    assert market_news["status"] == "available"
    assert market_news["provider_name"] == "mock_recent_market_news"
    assert market_news["market_snapshot"]["latest_close"] == 121.5
    assert market_news["recent_news"][0]["title"] == "AMD market headline"
    assert "mock recent market news" in markdown
    assert "AMD market headline" in markdown


def test_sec_symbol_web_enrichment_provider_builds_official_filing_packet() -> None:
    company = SecCompany(ticker="AMD", cik="0000002488", title="ADVANCED MICRO DEVICES INC")
    filing = SecFiling(
        company=company,
        accession_number="0000002488-26-000001",
        filing_date="2026-05-01",
        report_date="2026-03-31",
        form="10-Q",
        primary_document="amd-20260331.htm",
        description="Quarterly report",
    )

    class OfficialSecClient:
        def company_for_ticker(self, _ticker: str) -> SecCompany:
            return company

        def recent_filings(self, _ticker: str, *, forms=None, limit: int = 16):
            return [filing]

        def latest_earnings_release(self, _ticker: str):
            return None

        def latest_formal_earnings_report(self, _ticker: str):
            return None

        def document_text_url(self, _url: str, *, cache_name=None, ttl=None) -> str:
            return (
                "Revenue increased 12% to $123 million. Net income was $20 million. "
                "Risk Factors include demand and margin pressure. The company may issue securities under an offering."
            )

    provider = SecEdgarSymbolWebEnrichmentProvider(sec_client=OfficialSecClient(), max_recent_filings=4, max_filing_summaries=1)  # type: ignore[arg-type]
    enrichment = provider.enrich("AMD")
    payload = symbol_web_enrichment_to_payload(enrichment)

    assert payload["provider_name"] == "sec_edgar_public_filings"
    assert payload["status"] == "available"
    assert payload["company_profile"]["cik"] == "0000002488"
    assert payload["recent_filings"][0]["source_type"] == "sec_filing"
    assert payload["filing_summaries"][0]["form"] == "10-Q"
    assert "Revenue increased" in payload["filing_summaries"][0]["summary"]
    assert any("recent_filings=sec:1" in item for item in payload["source_debug"])


def test_symbol_chat_uses_responses_api_with_store_false_and_local_history() -> None:
    context = build_symbol_chat_context(
        "AMD",
        sec_client=FakeSecClient(),
        trade_memory_store=SimpleNamespace(find_snapshots_for_symbol=lambda _symbol: []),
    )
    fake_openai = FakeOpenAiClient(answer="## Overview\nNo buy/sell recommendation.")
    session = SymbolChatSession(
        context,
        chat_client=OpenAiSymbolChatClient(openai_client=fake_openai, model="gpt-test", timeout_seconds=33),
    )

    response = session.ask("Give me a full company overview.")

    assert response.answer.startswith("## Overview")
    assert response.response_id == "sym_resp_1"
    call = fake_openai.responses.calls[0]
    assert call["model"] == "gpt-test"
    assert call["store"] is False
    assert call["timeout"] == 33
    assert call["input"][0]["role"] == "system"
    assert SYMBOL_CHAT_SYSTEM_PROMPT in call["input"][0]["content"]
    payload = json.loads(call["input"][-1]["content"])
    assert payload["question"] == "Give me a full company overview."
    assert payload["symbol_context"]["symbol"] == "AMD"
    assert "buy, sell, or hold" in " ".join(payload["grounding_rules"])


def test_symbol_chat_missing_api_key_raises_user_friendly_error() -> None:
    context = build_symbol_chat_context(
        "AMD",
        sec_client=FakeSecClient(),
        trade_memory_store=SimpleNamespace(find_snapshots_for_symbol=lambda _symbol: []),
    )
    session = SymbolChatSession(context, chat_client=OpenAiSymbolChatClient(api_key="", model="gpt-test"))

    with patch.dict(os.environ, {"OPENAI_API_KEY": ""}):
        try:
            session.ask("Summarize risks.")
        except OpenAiSymbolChatError as exc:
            message = str(exc)
        else:
            raise AssertionError("Expected OpenAiSymbolChatError")

    assert "OPENAI_API_KEY is not configured" in message
    assert "sk-" not in message


def test_symbol_chat_timeout_error_is_friendly_and_redacted() -> None:
    class TimeoutResponses:
        def __init__(self) -> None:
            self.calls = []

        def create(self, **kwargs):
            self.calls.append(kwargs)
            raise TimeoutError("request timed out for sk-test-secret123456789")

    failing_openai = SimpleNamespace(responses=TimeoutResponses())
    context = build_symbol_chat_context(
        "AMD",
        sec_client=FakeSecClient(),
        trade_memory_store=SimpleNamespace(find_snapshots_for_symbol=lambda _symbol: []),
    )
    session = SymbolChatSession(
        context,
        chat_client=OpenAiSymbolChatClient(
            openai_client=failing_openai,
            api_key="sk-test-secret123456789",
            model="gpt-test",
            timeout_seconds=12,
        ),
    )

    try:
        session.ask("Summarize risks.")
    except OpenAiSymbolChatError as exc:
        message = str(exc)
    else:
        raise AssertionError("Expected OpenAiSymbolChatError")

    assert failing_openai.responses.calls[0]["timeout"] == 12
    assert "timed out after 12 seconds" in message
    assert "sk-test-secret" not in message


def test_symbol_chat_quick_prompts_cover_expected_actions() -> None:
    assert list(SYMBOL_CHAT_QUICK_PROMPTS) == [
        "Overview",
        "Bull vs Bear",
        "Risks",
        "Technicals",
        "What Changed?",
        "Diligence Questions",
    ]
    assert "using only the available context" in SYMBOL_CHAT_QUICK_PROMPTS["Bull vs Bear"]


def test_open_symbol_chat_symbol_resolution_precedence() -> None:
    app = SimpleNamespace(
        schwab_workspace_holdings_table=FakeTree(("symbol", "type"), ("NVDA", "EQUITY")),
        symbol_var=SimpleNamespace(get=lambda: "AMD"),
        options_symbol_var=SimpleNamespace(get=lambda: "TSLA"),
    )
    assert schwab_trading_tab._resolve_open_symbol_chat_symbol(app) == "NVDA"

    app.schwab_workspace_holdings_table = FakeTree(("symbol", "type"), ("USD (Cash)", "Cash"))
    assert schwab_trading_tab._resolve_open_symbol_chat_symbol(app) == "AMD"

    app.symbol_var = SimpleNamespace(get=lambda: "")
    assert schwab_trading_tab._resolve_open_symbol_chat_symbol(app) == "TSLA"


def test_symbol_chat_window_reenables_controls_on_prompt_error() -> None:
    window = object.__new__(SymbolChatWindow)
    window._closed = False
    window._request_generation = 1
    window._request_running = True
    window._cancel_requested = False
    window.status_var = FakeStatus()
    enabled_states = []
    system_lines = []
    window._set_controls_enabled = lambda enabled: enabled_states.append(enabled)
    window._append_system_line = lambda content: system_lines.append(content)

    with patch("app.ui.symbol_chat_window.messagebox.showerror") as showerror:
        SymbolChatWindow._finish_prompt_error(window, OpenAiSymbolChatError("timed out"), 1)

    assert enabled_states == [True]
    assert window._request_running is False
    assert window.status_var.value == "OpenAI symbol chat failed."
    assert system_lines == ["OpenAI request failed: timed out"]
    showerror.assert_called_once_with("AI Symbol Chat failed", "timed out")


def test_symbol_chat_transcript_saves_markdown(tmp_path) -> None:
    context = build_symbol_chat_context(
        "AMD",
        sec_client=FakeSecClient(),
        trade_memory_store=SimpleNamespace(find_snapshots_for_symbol=lambda _symbol: []),
    )
    fake_openai = FakeOpenAiClient(answer="Analysis only. Not available in the provided context.")
    session = SymbolChatSession(
        context,
        chat_client=OpenAiSymbolChatClient(openai_client=fake_openai, model="gpt-test"),
    )
    session.ask("What changed recently?")

    markdown = render_symbol_chat_transcript_markdown(session)
    saved = save_symbol_chat_transcript(session, tmp_path / "symbol-chat.md")

    assert "# AMD - AMD Corp. AI Symbol Chat" in markdown
    assert "## Context Availability" in markdown
    assert "## 1. User" in markdown
    assert "## 2. Assistant" in markdown
    assert "### Source Debug" in markdown
    assert saved.read_text(encoding="utf-8") == markdown
