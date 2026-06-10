from __future__ import annotations

import json
import os
from types import SimpleNamespace
from unittest.mock import patch

from app.analytics.ipo_filing_chat import (
    CHAT_FULL_TEXT_CHAR_LIMIT,
    FILING_CHAT_SYSTEM_PROMPT,
    IpoFilingChatSession,
    OpenAiIpoFilingChatClient,
    OpenAiIpoFilingChatError,
    build_ipo_filing_chat_context,
    filing_context_payload_for_prompt,
    render_ipo_filing_chat_transcript_markdown,
    save_ipo_filing_chat_transcript,
)
from app.analytics.openai_ipo_report import build_ipo_filing_source_bundle
from app.analytics.ipo_pipeline import IpoPipelineRecord
from app.data.sec_edgar import SEC_ARCHIVES_BASE_URL
from app.ui import ipo_pipeline_extension


SAMPLE_FILING_TEXT = """
Prospectus Summary
Silentium Ltd. develops active noise control systems for industrial equipment and commercial facilities.
The company has 24 enterprise customers and multi-year contracts with two global equipment manufacturers.

The Offering
We are offering 2,000,000 ordinary shares.
The estimated initial public offering price is between $6.00 and $8.00 per ordinary share.
We have applied to list our ordinary shares on the Nasdaq Capital Market under the symbol SLNT.
The underwriters are Example Securities LLC and Test Capital LLC.

Use of Proceeds
We intend to use the net proceeds for sales expansion, product development, working capital, and repayment of indebtedness.

Selected Consolidated Statements of Operations
($ in thousands)
Revenue 4,200 1,850
Net loss 7,100 3,900

Capitalization
Cash and cash equivalents were $1.2 million. Total debt was $3.5 million.

Dilution
At an assumed initial public offering price of $7.00 per share, immediate dilution to new investors will be $5.75 per share.

Risk Factors
Our recurring losses and negative cash flows raise substantial doubt about our ability to continue as a going concern.
We depend on a limited number of large enterprise customers.
"""


def _record(*, form: str = "F-1") -> IpoPipelineRecord:
    return IpoPipelineRecord(
        cik="1234567",
        company_name="Silentium Ltd.",
        proposed_ticker=None,
        form=form,
        filed_date="2026-06-09",
        ipo_status="Filed",
        sic="3571",
        sector="Technology",
        industry="Industrial technology",
        exchange=None,
        filing_url=f"{SEC_ARCHIVES_BASE_URL}/1234567/000123456726000001/f1.htm",
        accession_number="0001234567-26-000001",
        is_foreign_issuer=True,
    )


class FakeSecClient:
    def __init__(self, text: str) -> None:
        self.text = text
        self.cache_dir = "."

    def filing_index_items_for_accession(self, _cik, _accession_number):
        return [{"name": "f1.htm", "type": "F-1", "description": "Registration Statement Prospectus", "size": "100000"}]

    def document_text_url(self, _url: str, *, cache_name=None, ttl=None):
        return self.text


class FakeOpenAiResponses:
    def __init__(self, *, answer: str = "## Offering terms\nThe filing says Silentium is offering ordinary shares.") -> None:
        self.answer = answer
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        return SimpleNamespace(output_text=self.answer, id=f"resp_{len(self.calls)}")


class FakeOpenAiClient:
    def __init__(self, *, answer: str = "## Analyst answer\nNot disclosed fields are labeled.") -> None:
        self.responses = FakeOpenAiResponses(answer=answer)


def test_filing_chat_context_fetches_selected_filing_and_builds_bundle() -> None:
    context = build_ipo_filing_chat_context(_record(), client=FakeSecClient(SAMPLE_FILING_TEXT * 20))

    assert context.company_name == "Silentium Ltd."
    assert context.source_form == "F-1"
    assert context.source_document.name == "f1.htm"
    assert context.bundle.metadata["source_document"] == "f1.htm"
    assert context.bundle.metadata["source_url"].endswith("/f1.htm")
    assert context.bundle.deterministic_extracts["parsed_fields"]["price_range_low"] == 6.0
    assert "active noise control systems" in context.bundle.full_text


def test_filing_chat_uses_responses_api_with_store_false_and_local_history() -> None:
    context = build_ipo_filing_chat_context(_record(), client=FakeSecClient(SAMPLE_FILING_TEXT * 20))
    fake_openai = FakeOpenAiClient(answer="## Offering terms\nThe filing discloses 2,000,000 ordinary shares.")
    session = IpoFilingChatSession(
        context,
        chat_client=OpenAiIpoFilingChatClient(openai_client=fake_openai, model="gpt-test"),
    )

    response = session.ask("Extract the offering terms and do not invent missing fields.")

    assert response.answer.startswith("## Offering terms")
    assert response.response_id == "resp_1"
    assert len(session.messages) == 2
    call = fake_openai.responses.calls[0]
    assert call["model"] == "gpt-test"
    assert call["store"] is False
    assert "text" not in call
    assert call["input"][0]["role"] == "system"
    assert FILING_CHAT_SYSTEM_PROMPT in call["input"][0]["content"]
    request_payload = json.loads(call["input"][-1]["content"])
    assert request_payload["question"] == "Extract the offering terms and do not invent missing fields."
    assert request_payload["filing_context"]["source_mode"] == "full_prospectus_text"
    assert "2,000,000 ordinary shares" in request_payload["filing_context"]["full_prospectus_text"]
    assert "OPENAI_API_KEY" not in json.dumps(call)


def test_filing_chat_missing_api_key_raises_user_friendly_error() -> None:
    context = build_ipo_filing_chat_context(_record(), client=FakeSecClient(SAMPLE_FILING_TEXT * 20))
    session = IpoFilingChatSession(
        context,
        chat_client=OpenAiIpoFilingChatClient(api_key="", model="gpt-test"),
    )

    with patch.dict(os.environ, {"OPENAI_API_KEY": ""}):
        try:
            session.ask("Summarize risks.")
        except OpenAiIpoFilingChatError as exc:
            message = str(exc)
        else:
            raise AssertionError("Expected OpenAiIpoFilingChatError")

    assert "OPENAI_API_KEY is not configured" in message
    assert "sk-" not in message


def test_filing_chat_redacts_api_key_from_openai_errors() -> None:
    class FailingResponses:
        def create(self, **_kwargs):
            raise RuntimeError("request failed for sk-test-secret123456789")

    context = build_ipo_filing_chat_context(_record(), client=FakeSecClient(SAMPLE_FILING_TEXT * 20))
    failing_openai = SimpleNamespace(responses=FailingResponses())
    session = IpoFilingChatSession(
        context,
        chat_client=OpenAiIpoFilingChatClient(openai_client=failing_openai, api_key="sk-test-secret123456789"),
    )

    try:
        session.ask("Summarize risks.")
    except OpenAiIpoFilingChatError as exc:
        message = str(exc)
    else:
        raise AssertionError("Expected OpenAiIpoFilingChatError")

    assert "sk-test-secret" not in message
    assert "[REDACTED]" in message


def test_large_filing_chat_context_uses_relevant_retrieved_chunks() -> None:
    filler = "Generic prospectus boilerplate about the company and securities.\n" * ((CHAT_FULL_TEXT_CHAR_LIMIT // 62) + 100)
    large_text = (
        "Prospectus Summary\nSilentium sells acoustic control systems.\n"
        + filler
        + "\nRisk Factors\nOur liquidity is limited and recurring losses raise substantial doubt about our ability to continue as a going concern.\n"
        + "Customer concentration could materially affect revenue.\n"
        + "\nDilution\nImmediate dilution to new investors will be material because the IPO price exceeds net tangible book value.\n"
    )
    bundle = build_ipo_filing_source_bundle(_record(), large_text, source_url=_record().filing_url)

    payload = filing_context_payload_for_prompt(bundle, "Summarize risk factors and dilution.")
    joined = "\n".join(section["text"] for section in payload["sections"])

    assert payload["source_mode"] == "retrieved_filing_chunks"
    assert "full_prospectus_text" not in payload
    assert len(joined) < len(large_text)
    assert "substantial doubt" in joined
    assert "Immediate dilution" in joined


def test_filing_chat_transcript_saves_markdown(tmp_path) -> None:
    context = build_ipo_filing_chat_context(_record(), client=FakeSecClient(SAMPLE_FILING_TEXT * 20))
    fake_openai = FakeOpenAiClient(answer="Not disclosed fields should stay labeled.")
    session = IpoFilingChatSession(
        context,
        chat_client=OpenAiIpoFilingChatClient(openai_client=fake_openai, model="gpt-test"),
    )
    session.ask("What is the market cap?")

    markdown = render_ipo_filing_chat_transcript_markdown(session)
    saved = save_ipo_filing_chat_transcript(session, tmp_path / "chat.md")

    assert "# Silentium Ltd. F-1 AI Filing Chat" in markdown
    assert "## 1. User" in markdown
    assert "## 2. Assistant" in markdown
    assert saved.read_text(encoding="utf-8") == markdown


class FakeTree:
    def __init__(self, selection):
        self._selection = selection

    def selection(self):
        return self._selection


class FakeStatus:
    def __init__(self) -> None:
        self.value = ""

    def set(self, value: str) -> None:
        self.value = value


def test_open_ai_filing_chat_handler_rejects_missing_selection() -> None:
    app = SimpleNamespace(ipo_pipeline_table=FakeTree(()), ipo_pipeline_row_map={}, ipo_pipeline_status_var=FakeStatus())

    with patch.object(ipo_pipeline_extension.messagebox, "showinfo") as showinfo:
        ipo_pipeline_extension._open_selected_ai_filing_chat(app)  # type: ignore[arg-type]

    showinfo.assert_called_once_with("Open AI Filing Chat", "Select an IPO pipeline row first.")


def test_open_ai_filing_chat_handler_rejects_non_reportable_form() -> None:
    app = SimpleNamespace(
        ipo_pipeline_table=FakeTree(("row1",)),
        ipo_pipeline_row_map={"row1": _record(form="8-K")},
        ipo_pipeline_status_var=FakeStatus(),
    )

    with patch.object(ipo_pipeline_extension.messagebox, "showinfo") as showinfo:
        ipo_pipeline_extension._open_selected_ai_filing_chat(app)  # type: ignore[arg-type]

    showinfo.assert_called_once_with("Open AI Filing Chat", "AI filing chat is available for S-1/F-1, 424B4, and EFFECT rows.")


def test_open_ai_filing_chat_handler_opens_window_for_valid_row() -> None:
    app = SimpleNamespace(
        ipo_pipeline_table=FakeTree(("row1",)),
        ipo_pipeline_row_map={"row1": _record()},
        ipo_pipeline_status_var=FakeStatus(),
    )

    with patch.object(ipo_pipeline_extension, "open_ipo_filing_chat_window") as open_window:
        ipo_pipeline_extension._open_selected_ai_filing_chat(app)  # type: ignore[arg-type]

    open_window.assert_called_once()
    assert "Opened AI filing chat for Silentium Ltd." == app.ipo_pipeline_status_var.value
