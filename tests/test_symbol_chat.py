from __future__ import annotations

import json
import os
from types import SimpleNamespace
from unittest.mock import patch

from app.analytics.symbol_chat import (
    SYMBOL_CHAT_QUICK_PROMPTS,
    SYMBOL_CHAT_SYSTEM_PROMPT,
    OpenAiSymbolChatClient,
    OpenAiSymbolChatError,
    SymbolChatSession,
    build_symbol_chat_context,
    render_symbol_chat_transcript_markdown,
    save_symbol_chat_transcript,
)
from app.analytics.technical_analysis import Candle, build_technical_command_center_report
from app.core.portfolio import Portfolio, Position
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


class FakeTree:
    def __init__(self, columns, values, selection=("row1",)) -> None:
        self._columns = columns
        self._values = values
        self._selection = selection

    def selection(self):
        return self._selection

    def item(self, _row_id, option):
        assert option == "values"
        return self._values

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
