from __future__ import annotations

import json
import logging
import os
import re
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Literal, Mapping, Sequence

from dotenv import load_dotenv

from app.analytics.openai_ipo_report import DEFAULT_OPENAI_IPO_REPORT_MODEL, _redact_api_key, _response_output_text


LOGGER = logging.getLogger(__name__)

DEFAULT_HYPERLIQUID_PERP_CHAT_DIR = Path("data/hyperliquid_perp_chat")
HYPERLIQUID_PERP_CHAT_HISTORY_MESSAGE_LIMIT = 8
HYPERLIQUID_PERP_CHAT_OPENAI_TIMEOUT_SECONDS = 120.0
HYPERLIQUID_PERP_CHAT_REQUEST_CHAR_LIMIT = 70_000
HYPERLIQUID_PERP_CHAT_APPROX_CHARS_PER_TOKEN = 4
DEFAULT_PERP_FEE_RATE_PERCENT = 0.045
DEFAULT_SCENARIO_MOVES = (-0.10, -0.05, -0.02, -0.01, 0.0, 0.01, 0.02, 0.05, 0.10)
ZERO_EPSILON = 0.00000001


HYPERLIQUID_PERP_CHAT_SYSTEM_PROMPT = """You are Hyperliquid Perp Strategy Chat inside Portfolio Risk Cockpit.

You are a direct, high-signal trading math partner for Jeremy and Alex's HYPE perpetual strategy.
Use only the provided Hyperliquid perp context, deterministic calculator output, and conversation history.
Do not invent balances, positions, prices, fills, funding, liquidation levels, account values, orders, or wallet data.

Focus on HYPE/HYPE-PERP strategy math: TP/SL ladders, price scenarios, risk/reward, account-by-account P&L,
combined Jeremy/Alex exposure, fees, margin ROI, liquidation distance, and open-order awareness.
Always separate Jeremy, Alex, and combined impacts when both accounts are loaded.
Be precise and willing to challenge weak math. Say plainly when a setup is mathematically unfavorable,
when fees make a stop too tight, when liquidation distance is thin, when a ladder leaves too much downside open,
or when the combined book is less bullish or bearish than it feels because one account offsets the other.

This chat is read-only. You may draft plans and calculations only.
Never submit orders, never claim an order was placed, never imply live execution, and never ask for API keys or secrets.
Any order, TP/SL, or ladder output must be labeled as a draft plan for manual review/submission through the app's existing live buttons.
Keep the answer strategy-focused rather than compliance-focused.
Never include credentials, API keys, private keys, full wallet addresses, cookies, account hashes, or secrets.
"""


HYPERLIQUID_PERP_CHAT_QUICK_PROMPTS: dict[str, str] = {
    "Position Overview": (
        "Give me the current HYPE-PERP strategy read. Separate Jeremy, Alex, and combined exposure. "
        "Include net HYPE delta, gross long/short, margin/fee context, open-order coverage, and the biggest mathematical weakness."
    ),
    "TP Ladder": (
        "Design a fee-aware TP ladder from the current ticket and synced positions. "
        "Show draft close sizes, prices, net P&L, margin ROI, remaining exposure, and whether existing open orders already cover any rung."
    ),
    "SL / Invalidation": (
        "Evaluate stop-loss and invalidation math for the current HYPE-PERP setup. "
        "Compare tight and wider stops after fees, liquidation distance, and Jeremy/Alex/combined P&L."
    ),
    "Bullish Plan": (
        "Build a bullish draft plan for the current HYPE-PERP book. Keep it mathematical: upside ladder, downside invalidation, "
        "account-by-account exposure, combined net, fees, and what would make the plan weak."
    ),
    "Defensive Plan": (
        "Build a defensive draft plan for the current HYPE-PERP book. Prioritize downside containment, stop/cover ladders, "
        "open orders, liquidation distance, and how much upside remains after reducing risk."
    ),
    "What If +10% / -10%": (
        "Show what a +10% and -10% HYPE move does to Jeremy, Alex, and the combined book. "
        "Use the deterministic scenario table and call out net exposure, P&L, fees, and margin ROI."
    ),
    "Open Orders Check": (
        "Audit current HYPE open orders against the synced positions and current ticket. "
        "Tell me what is already covered, what is duplicated, and what is missing for TP/SL planning."
    ),
    "Jeremy vs Alex": (
        "Compare Jeremy and Alex account exposure side by side. Explain how one account offsets or amplifies the other, "
        "and what the combined HYPE-PERP book actually is."
    ),
}

ProgressCallback = Callable[[str], None]


class OpenAiHyperliquidPerpChatError(RuntimeError):
    """Raised for Hyperliquid perp chat failures with credentials redacted."""


@dataclass(frozen=True)
class HyperliquidPerpPosition:
    account_label: str
    coin: str
    signed_size: float
    entry_price: float
    mark_price: float
    unrealized_pnl: float | None = None
    source_symbol: str = ""

    @property
    def direction(self) -> str:
        return "long" if self.signed_size >= 0 else "short"

    @property
    def size(self) -> float:
        return abs(self.signed_size)

    @property
    def notional(self) -> float:
        return self.size * self.mark_price


@dataclass(frozen=True)
class HyperliquidPerpChatContext:
    selected_coin: str = "HYPE"
    selected_account_mode: str = "combined"
    accounts: tuple[dict[str, Any], ...] = ()
    positions: tuple[dict[str, Any], ...] = ()
    open_orders: tuple[dict[str, Any], ...] = ()
    ticket: dict[str, Any] = field(default_factory=dict)
    market_snapshot: dict[str, Any] = field(default_factory=dict)
    deterministic_math: dict[str, Any] = field(default_factory=dict)
    source_metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def display_name(self) -> str:
        return f"Hyperliquid Perp Strategy Chat - {self.selected_coin}"


@dataclass(frozen=True)
class HyperliquidPerpChatMessage:
    role: Literal["user", "assistant"]
    content: str
    source_mode: str = ""
    source_debug: tuple[str, ...] = ()


@dataclass(frozen=True)
class HyperliquidPerpChatResponse:
    answer: str
    response_id: str
    model: str
    source_mode: str
    source_debug: tuple[str, ...]


class HyperliquidPerpChatSession:
    def __init__(
        self,
        context: HyperliquidPerpChatContext,
        *,
        chat_client: "OpenAiHyperliquidPerpChatClient | None" = None,
    ) -> None:
        self.context = context
        self.chat_client = chat_client or OpenAiHyperliquidPerpChatClient()
        self.messages: list[HyperliquidPerpChatMessage] = []
        self.last_response_id = ""

    @property
    def model(self) -> str:
        return self.chat_client.model

    def ask(self, prompt: str, *, progress_callback: ProgressCallback | None = None) -> HyperliquidPerpChatResponse:
        clean_prompt = _clean_prompt(prompt)
        if not clean_prompt:
            raise OpenAiHyperliquidPerpChatError("Enter a Hyperliquid perp strategy question before sending.")

        response = self.chat_client.ask(self.context, self.messages, clean_prompt, progress_callback=progress_callback)
        self.messages.append(HyperliquidPerpChatMessage(role="user", content=clean_prompt))
        self.messages.append(
            HyperliquidPerpChatMessage(
                role="assistant",
                content=response.answer,
                source_mode=response.source_mode,
                source_debug=response.source_debug,
            )
        )
        self.last_response_id = response.response_id
        return response


class OpenAiHyperliquidPerpChatClient:
    def __init__(
        self,
        *,
        openai_client: Any | None = None,
        api_key: str | None = None,
        model: str | None = None,
        timeout_seconds: float | None = None,
    ) -> None:
        load_dotenv()
        self._openai_client = openai_client
        self._api_key = api_key
        self.model = (
            model
            or os.getenv("OPENAI_HYPERLIQUID_PERP_CHAT_MODEL")
            or os.getenv("OPENAI_SYMBOL_CHAT_MODEL")
            or os.getenv("OPENAI_IPO_REPORT_MODEL")
            or DEFAULT_OPENAI_IPO_REPORT_MODEL
        ).strip()
        self.timeout_seconds = _positive_timeout_seconds(timeout_seconds, os.getenv("OPENAI_HYPERLIQUID_PERP_CHAT_TIMEOUT_SECONDS"))

    def ask(
        self,
        context: HyperliquidPerpChatContext,
        history: Iterable[HyperliquidPerpChatMessage],
        prompt: str,
        *,
        progress_callback: ProgressCallback | None = None,
    ) -> HyperliquidPerpChatResponse:
        started_at = time.perf_counter()
        history_messages = list(history)
        _notify_progress(progress_callback, "Preparing Hyperliquid perp context...")
        request_payload = hyperliquid_perp_chat_request_payload(context, prompt, timeout_seconds=self.timeout_seconds)
        payload_text = _serialize_request_payload(request_payload)
        payload_chars = len(payload_text)
        diagnostics = {
            "request_payload_chars": payload_chars,
            "request_payload_approx_tokens": _approx_token_count(payload_chars),
            "request_payload_char_limit": HYPERLIQUID_PERP_CHAT_REQUEST_CHAR_LIMIT,
            "openai_timeout_seconds": self.timeout_seconds,
        }
        LOGGER.debug(
            "Hyperliquid perp chat payload ready coin=%s payload_chars=%s approx_tokens=%s history_messages=%s",
            context.selected_coin,
            payload_chars,
            diagnostics["request_payload_approx_tokens"],
            len(history_messages),
        )

        input_messages = [{"role": "system", "content": HYPERLIQUID_PERP_CHAT_SYSTEM_PROMPT}]
        for message in history_messages[-HYPERLIQUID_PERP_CHAT_HISTORY_MESSAGE_LIMIT:]:
            input_messages.append({"role": message.role, "content": message.content})
        input_messages.append({"role": "user", "content": payload_text})

        try:
            _notify_progress(progress_callback, f"Calling OpenAI (timeout {self.timeout_seconds:g}s)...")
            openai_started = time.perf_counter()
            response = self._client().responses.create(
                model=self.model,
                input=input_messages,
                store=False,
                timeout=self.timeout_seconds,
            )
        except Exception as exc:
            if _is_timeout_exception(exc):
                message = (
                    f"OpenAI Hyperliquid perp chat timed out after {self.timeout_seconds:g} seconds. "
                    "Try a narrower question or retry later."
                )
            else:
                message = f"OpenAI Hyperliquid perp chat failed: {exc}"
            message = _redact_api_key(message, self._current_api_key())
            message = redact_hyperliquid_perp_chat_secrets(message)
            LOGGER.warning(
                "Hyperliquid perp chat OpenAI request failed coin=%s elapsed=%.3fs error=%s",
                context.selected_coin,
                time.perf_counter() - started_at,
                message,
            )
            raise OpenAiHyperliquidPerpChatError(message) from None

        diagnostics["openai_seconds"] = round(time.perf_counter() - openai_started, 3)
        diagnostics["total_seconds"] = round(time.perf_counter() - started_at, 3)
        _notify_progress(progress_callback, "OpenAI response received.")
        answer = _clean_answer(_response_output_text(response))
        if not answer:
            raise OpenAiHyperliquidPerpChatError("OpenAI Hyperliquid perp chat returned an empty response.")

        return HyperliquidPerpChatResponse(
            answer=answer,
            response_id=str(getattr(response, "id", "") or ""),
            model=self.model,
            source_mode=_source_mode(context),
            source_debug=tuple(_source_debug_lines(request_payload, diagnostics)),
        )

    def _client(self) -> Any:
        if self._openai_client is not None:
            return self._openai_client

        api_key = self._current_api_key()
        if not api_key:
            raise OpenAiHyperliquidPerpChatError("OPENAI_API_KEY is not configured. Add it to .env or the environment.")

        try:
            from openai import OpenAI
        except ImportError as exc:
            raise OpenAiHyperliquidPerpChatError("The openai package is not installed. Run pip install -r requirements.txt.") from exc

        self._openai_client = OpenAI(api_key=api_key, timeout=self.timeout_seconds)
        return self._openai_client

    def _current_api_key(self) -> str:
        return (self._api_key if self._api_key is not None else os.getenv("OPENAI_API_KEY", "")).strip()


def create_hyperliquid_perp_chat_session(
    coin: str = "HYPE",
    *,
    app_context: Any | None = None,
    openai_client: Any | None = None,
    model: str | None = None,
) -> HyperliquidPerpChatSession:
    context = build_hyperliquid_perp_chat_context(coin=coin, app_context=app_context)
    return HyperliquidPerpChatSession(
        context,
        chat_client=OpenAiHyperliquidPerpChatClient(openai_client=openai_client, model=model),
    )


def build_hyperliquid_perp_chat_context(
    coin: str = "HYPE",
    *,
    app_context: Any | None = None,
    selected_account_mode: str = "combined",
) -> HyperliquidPerpChatContext:
    source_metadata: dict[str, Any] = {
        "loaded_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "available": [],
        "unavailable": [],
        "warnings": [],
        "read_only": True,
    }

    ticket = _ticket_context(app_context, fallback_coin=coin)
    selected_coin = normalize_hyperliquid_perp_coin(ticket.get("coin") or coin or "HYPE")
    ticket["coin"] = selected_coin
    _mark_available(source_metadata, "ticket_fields", "Loaded current Hyperliquid perp ticket fields from the app context.")

    portfolio = _portfolio_from_app(app_context)
    if portfolio is None:
        _mark_unavailable(source_metadata, "portfolio", "No current cockpit portfolio was available.")
        positions: tuple[dict[str, Any], ...] = ()
        position_models: tuple[HyperliquidPerpPosition, ...] = ()
        accounts: tuple[dict[str, Any], ...] = ()
    else:
        position_models = tuple(_extract_perp_positions(portfolio, selected_coin))
        positions = tuple(_position_to_payload(position) for position in position_models)
        accounts = tuple(_account_summaries(portfolio, position_models, app_context, selected_coin))
        if positions:
            _mark_available(source_metadata, "synced_perp_positions", f"Loaded {len(positions)} synced {selected_coin}-PERP position(s).")
        else:
            _mark_unavailable(source_metadata, "synced_perp_positions", f"No synced {selected_coin}-PERP positions found in the current cockpit portfolio.")
        if accounts:
            _mark_available(source_metadata, "account_summaries", f"Built {len(accounts)} Hyperliquid account summary row(s).")
        else:
            _mark_unavailable(source_metadata, "account_summaries", "No account-labeled Hyperliquid summaries were available.")

    open_orders = tuple(_matching_open_orders(app_context, selected_coin))
    if open_orders:
        _mark_available(source_metadata, "open_orders", f"Loaded {len(open_orders)} current {selected_coin} open order(s).")
    else:
        _mark_unavailable(source_metadata, "open_orders", f"No current {selected_coin} open orders were found in app caches.")

    market_snapshot = _market_snapshot(selected_coin, ticket, position_models)
    if market_snapshot.get("reference_price"):
        _mark_available(source_metadata, "market_reference", f"Using {market_snapshot.get('reference_source')} for scenario reference price.")
    else:
        _mark_unavailable(source_metadata, "market_reference", "No synced mark or ticket entry price was available for scenario math.")

    deterministic_math = _deterministic_math(
        selected_coin=selected_coin,
        ticket=ticket,
        positions=position_models,
        open_orders=open_orders,
        market_snapshot=market_snapshot,
    )
    if deterministic_math.get("scenario_table"):
        _mark_available(source_metadata, "scenario_table", "Built deterministic account-by-account and combined HYPE scenario table.")
    else:
        _mark_unavailable(source_metadata, "scenario_table", "Scenario table unavailable because no reference price was available.")
    if deterministic_math.get("proposed_ticket"):
        _mark_available(source_metadata, "proposed_ticket_math", "Built deterministic proposed-ticket TP/SL and fee math.")
    else:
        _mark_warning(source_metadata, "Proposed-ticket math is limited until size and entry are filled in.")

    return HyperliquidPerpChatContext(
        selected_coin=selected_coin,
        selected_account_mode=selected_account_mode,
        accounts=accounts,
        positions=positions,
        open_orders=open_orders,
        ticket=ticket,
        market_snapshot=market_snapshot,
        deterministic_math=deterministic_math,
        source_metadata=source_metadata,
    )


def hyperliquid_perp_chat_request_payload(
    context: HyperliquidPerpChatContext,
    prompt: str,
    *,
    timeout_seconds: float,
) -> dict[str, Any]:
    payload = {
        "question": prompt,
        "hyperliquid_perp_context": {
            "selected_coin": context.selected_coin,
            "selected_account_mode": context.selected_account_mode,
            "accounts": list(context.accounts) or _not_available(),
            "positions": list(context.positions) or _not_available(),
            "open_orders": list(context.open_orders) or _not_available(),
            "ticket": context.ticket or _not_available(),
            "market_snapshot": context.market_snapshot or _not_available(),
            "deterministic_math": context.deterministic_math or _not_available(),
            "source_metadata": context.source_metadata,
        },
        "grounding_rules": [
            "Use only hyperliquid_perp_context and conversation history for factual claims.",
            "Treat deterministic_math as authoritative for calculations. Do not recalculate inconsistently.",
            "Separate Jeremy, Alex, and combined impacts when those accounts are present.",
            "Use HYPE/HYPE-PERP terminology and explain long/short direction exactly.",
            "Long P&L is (exit - entry) * size. Short P&L is (entry - exit) * size.",
            "Fees are estimated from entry notional plus exit notional times the configured fee rate unless the context says exit-only.",
            "Leverage changes margin, margin ROI, and liquidation distance; it does not change dollar P&L for a fixed contract size.",
            "Call out stale or missing context directly. Say 'Not available in the provided context.' when needed.",
            "Do not infer balances, funding, fills, order state, or liquidation levels that are absent.",
            "This is read-only strategy chat. Draft plans are text only and require manual review/submission outside the chat.",
            "Never submit orders, trigger app actions, or claim live execution happened.",
            "Never include API keys, private keys, full wallet addresses, account hashes, cookies, credentials, or secrets.",
        ],
        "request_budget": {
            "request_payload_char_limit": HYPERLIQUID_PERP_CHAT_REQUEST_CHAR_LIMIT,
            "openai_timeout_seconds": timeout_seconds,
        },
    }
    return _enforce_request_payload_budget(payload)


def perp_position_pnl(entry_price: float, exit_price: float, size: float, is_long: bool) -> float:
    _validate_positive(entry_price, "entry_price")
    _validate_positive(exit_price, "exit_price")
    _validate_non_negative(size, "size")
    return (exit_price - entry_price) * size if is_long else (entry_price - exit_price) * size


def perp_fee_amount(
    entry_price: float,
    exit_price: float,
    size: float,
    fee_rate_percent: float,
    *,
    include_entry_fee: bool = True,
    include_exit_fee: bool = True,
) -> float:
    _validate_positive(entry_price, "entry_price")
    _validate_positive(exit_price, "exit_price")
    _validate_non_negative(size, "size")
    if fee_rate_percent < 0:
        raise ValueError("fee_rate_percent cannot be negative.")
    notional = 0.0
    if include_entry_fee:
        notional += entry_price * size
    if include_exit_fee:
        notional += exit_price * size
    return notional * (fee_rate_percent / 100.0)


def perp_scenario(
    entry_price: float,
    exit_price: float,
    size: float,
    is_long: bool,
    *,
    leverage: float = 1.0,
    fee_rate_percent: float = DEFAULT_PERP_FEE_RATE_PERCENT,
    include_entry_fee: bool = True,
    include_exit_fee: bool = True,
) -> dict[str, float]:
    gross_pnl = perp_position_pnl(entry_price, exit_price, size, is_long)
    fees = perp_fee_amount(
        entry_price,
        exit_price,
        size,
        fee_rate_percent,
        include_entry_fee=include_entry_fee,
        include_exit_fee=include_exit_fee,
    )
    net_pnl = gross_pnl - fees
    margin = estimated_margin(entry_price, size, leverage)
    return {
        "entry_price": entry_price,
        "exit_price": exit_price,
        "size": size,
        "direction": "long" if is_long else "short",
        "price_move_percent": ((exit_price - entry_price) / entry_price) * 100.0,
        "directional_move_percent": (((exit_price - entry_price) if is_long else (entry_price - exit_price)) / entry_price) * 100.0,
        "gross_pnl": gross_pnl,
        "fees": fees,
        "net_pnl": net_pnl,
        "estimated_margin": margin,
        "margin_roi_percent": (net_pnl / margin * 100.0) if margin > 0 else 0.0,
    }


def estimated_margin(entry_price: float, size: float, leverage: float) -> float:
    _validate_positive(entry_price, "entry_price")
    _validate_non_negative(size, "size")
    if leverage <= 0:
        raise ValueError("leverage must be positive.")
    return (entry_price * size) / leverage


def breakeven_price(entry_price: float, is_long: bool, fee_rate_percent: float = DEFAULT_PERP_FEE_RATE_PERCENT) -> float:
    _validate_positive(entry_price, "entry_price")
    if fee_rate_percent < 0:
        raise ValueError("fee_rate_percent cannot be negative.")
    fee = fee_rate_percent / 100.0
    if is_long:
        return entry_price * (1.0 + fee) / max(1.0 - fee, 0.000001)
    return entry_price * (1.0 - fee) / (1.0 + fee)


def isolated_liquidation_estimate(entry_price: float, is_long: bool, leverage: float) -> float | None:
    if entry_price <= 0 or leverage <= 0:
        return None
    if leverage <= 1:
        return 0.0 if is_long else entry_price * 2.0
    return max(entry_price * (1.0 - 1.0 / leverage), 0.0) if is_long else entry_price * (1.0 + 1.0 / leverage)


def liquidation_distance_percent(reference_price: float, liquidation_price: float | None) -> float | None:
    if reference_price <= 0 or liquidation_price is None:
        return None
    return abs(reference_price - liquidation_price) / reference_price * 100.0


def risk_reward_ratio(reward_net_pnl: float, stop_net_pnl: float) -> float | None:
    risk = abs(min(stop_net_pnl, 0.0))
    reward = max(reward_net_pnl, 0.0)
    if risk <= ZERO_EPSILON or reward <= ZERO_EPSILON:
        return None
    return reward / risk


def risk_reward_label(reward_net_pnl: float, stop_net_pnl: float) -> str:
    ratio = risk_reward_ratio(reward_net_pnl, stop_net_pnl)
    if ratio is None:
        return "n/a - TP must be profitable and SL must be a loss after fees"
    return f"{ratio:.2f} : 1"


def validate_tpsl_direction(
    entry_price: float,
    *,
    tp_price: float | None,
    sl_price: float | None,
    is_long: bool,
) -> dict[str, Any]:
    _validate_positive(entry_price, "entry_price")
    direction = "LONG" if is_long else "SHORT"
    warnings: list[str] = []
    tp_valid: bool | None = None
    sl_valid: bool | None = None

    if tp_price is not None:
        tp_valid = tp_price > entry_price if is_long else tp_price < entry_price
        if not tp_valid:
            relation = _price_relation(tp_price, entry_price)
            warnings.append(f"TP is {relation} entry for a {direction}; that is not a take-profit scenario.")
    if sl_price is not None:
        sl_valid = sl_price < entry_price if is_long else sl_price > entry_price
        if not sl_valid:
            relation = _price_relation(sl_price, entry_price)
            warnings.append(f"SL is {relation} entry for a {direction}; that is not a stop-loss scenario.")

    return {
        "direction": direction,
        "tp_valid": tp_valid,
        "sl_valid": sl_valid,
        "warnings": warnings,
    }


def build_ladder_plan(
    *,
    entry_price: float,
    size: float,
    is_long: bool,
    levels: Sequence[Mapping[str, Any]],
    fee_rate_percent: float = DEFAULT_PERP_FEE_RATE_PERCENT,
    leverage: float = 1.0,
    total_size: float | None = None,
) -> list[dict[str, Any]]:
    _validate_positive(entry_price, "entry_price")
    _validate_non_negative(size, "size")
    if not levels or size <= ZERO_EPSILON:
        return []

    intended_total = min(size, max(float(total_size), 0.0)) if total_size is not None else size
    close_sizes = _ladder_close_sizes(size=size, intended_total=intended_total, levels=levels)
    rows: list[dict[str, Any]] = []
    allocated = 0.0
    cumulative_net = 0.0
    for index, (level, close_size) in enumerate(zip(levels, close_sizes), start=1):
        if close_size <= ZERO_EPSILON:
            continue
        exit_price = _ladder_level_price(entry_price, is_long, level)
        case = perp_scenario(
            entry_price,
            exit_price,
            close_size,
            is_long,
            leverage=leverage,
            fee_rate_percent=fee_rate_percent,
        )
        allocated += close_size
        cumulative_net += case["net_pnl"]
        rows.append(
            {
                "rung": index,
                "label": str(level.get("label") or f"Rung {index}"),
                "exit_price": exit_price,
                "close_size": close_size,
                "close_fraction_of_position": close_size / size if size > 0 else 0.0,
                "remaining_size_after": max(size - allocated, 0.0),
                "gross_pnl": case["gross_pnl"],
                "fees": case["fees"],
                "net_pnl": case["net_pnl"],
                "cumulative_net_pnl": cumulative_net,
                "margin_roi_percent": case["margin_roi_percent"],
            }
        )
    return rows


def standard_ladder_plans(
    *,
    entry_price: float,
    size: float,
    is_long: bool,
    fee_rate_percent: float = DEFAULT_PERP_FEE_RATE_PERCENT,
    leverage: float = 1.0,
) -> dict[str, list[dict[str, Any]]]:
    if entry_price <= 0 or size <= ZERO_EPSILON:
        return {}
    equal_tp = [{"move_percent": move, "weight": 1.0, "label": f"TP {move:.0%}"} for move in (0.02, 0.04, 0.06, 0.08, 0.10)]
    bullish_tp = [
        {"move_percent": move, "weight": weight, "label": f"TP {move:.0%}"}
        for move, weight in ((0.02, 1.0), (0.04, 1.0), (0.08, 1.25), (0.12, 1.5), (0.18, 2.0))
    ]
    defensive_tp = [
        {"move_percent": move, "weight": weight, "label": f"TP {move:.0%}"}
        for move, weight in ((0.01, 2.0), (0.02, 2.0), (0.03, 1.5), (0.05, 1.0))
    ]
    defensive_sl = [
        {"move_percent": move, "weight": weight, "label": f"SL {move:.0%}"}
        for move, weight in ((-0.02, 2.0), (-0.04, 2.0), (-0.06, 1.0))
    ]
    return {
        "equal_weight_tp": build_ladder_plan(
            entry_price=entry_price,
            size=size,
            is_long=is_long,
            levels=equal_tp,
            fee_rate_percent=fee_rate_percent,
            leverage=leverage,
        ),
        "weighted_bullish_tp": build_ladder_plan(
            entry_price=entry_price,
            size=size,
            is_long=is_long,
            levels=bullish_tp,
            fee_rate_percent=fee_rate_percent,
            leverage=leverage,
        ),
        "defensive_tp": build_ladder_plan(
            entry_price=entry_price,
            size=size,
            is_long=is_long,
            levels=defensive_tp,
            fee_rate_percent=fee_rate_percent,
            leverage=leverage,
        ),
        "defensive_sl": build_ladder_plan(
            entry_price=entry_price,
            size=size,
            is_long=is_long,
            levels=defensive_sl,
            fee_rate_percent=fee_rate_percent,
            leverage=leverage,
        ),
    }


def combined_exposure(
    positions: Sequence[HyperliquidPerpPosition],
    *,
    reference_price: float,
) -> dict[str, Any]:
    long_size = sum(position.size for position in positions if position.signed_size > 0)
    short_size = sum(position.size for position in positions if position.signed_size < 0)
    net_signed_size = sum(position.signed_size for position in positions)
    account_rows = []
    for account in sorted({position.account_label for position in positions}):
        account_positions = [position for position in positions if position.account_label == account]
        account_signed = sum(position.signed_size for position in account_positions)
        account_rows.append(
            {
                "account_label": account,
                "signed_size": account_signed,
                "direction": _signed_direction(account_signed),
                "gross_long_size": sum(position.size for position in account_positions if position.signed_size > 0),
                "gross_short_size": sum(position.size for position in account_positions if position.signed_size < 0),
                "net_notional_at_reference": account_signed * reference_price,
            }
        )
    return {
        "reference_price": reference_price,
        "gross_long_size": long_size,
        "gross_short_size": short_size,
        "net_signed_size": net_signed_size,
        "net_direction": _signed_direction(net_signed_size),
        "gross_notional_at_reference": (long_size + short_size) * reference_price,
        "net_notional_at_reference": net_signed_size * reference_price,
        "accounts": account_rows,
    }


def account_scenario_table(
    positions: Sequence[HyperliquidPerpPosition],
    *,
    scenario_prices: Sequence[float],
    fee_rate_percent: float = DEFAULT_PERP_FEE_RATE_PERCENT,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    accounts = sorted({position.account_label for position in positions})
    for price in scenario_prices:
        if price <= 0:
            continue
        account_payload: dict[str, Any] = {}
        combined = _empty_scenario_bucket()
        for account in accounts:
            bucket = _empty_scenario_bucket()
            for position in positions:
                if position.account_label != account:
                    continue
                _add_position_scenario(bucket, position, price, fee_rate_percent)
            account_payload[account] = _finalize_scenario_bucket(bucket)
            _merge_scenario_bucket(combined, bucket)
        rows.append(
            {
                "price": price,
                "accounts": account_payload,
                "combined": _finalize_scenario_bucket(combined),
            }
        )
    return rows


def render_hyperliquid_perp_chat_transcript_markdown(session: HyperliquidPerpChatSession) -> str:
    context = session.context
    metadata = context.source_metadata
    lines = [
        f"# {context.display_name}",
        "",
        f"Coin: {context.selected_coin}",
        f"Account mode: {context.selected_account_mode}",
        f"AI model: {session.model}",
        f"Loaded: {metadata.get('loaded_at_utc') or '--'}",
        "Read-only: yes",
        "",
        "## Context Availability",
        "",
    ]
    available = [str(item) for item in metadata.get("available", []) if str(item).strip()]
    unavailable = [str(item) for item in metadata.get("unavailable", []) if str(item).strip()]
    warnings = [str(item) for item in metadata.get("warnings", []) if str(item).strip()]
    lines.append("Available:")
    lines.extend(f"- {redact_hyperliquid_perp_chat_secrets(item)}" for item in (available or ["None"]))
    lines.extend(["", "Unavailable / limited:"])
    lines.extend(f"- {redact_hyperliquid_perp_chat_secrets(item)}" for item in (unavailable or ["None"]))
    lines.extend(["", "Warnings:"])
    lines.extend(f"- {redact_hyperliquid_perp_chat_secrets(item)}" for item in (warnings or ["None"]))
    lines.append("")

    if not session.messages:
        lines.append("_No chat messages yet._")
    else:
        for index, message in enumerate(session.messages, start=1):
            speaker = "User" if message.role == "user" else "Assistant"
            lines.extend([f"## {index}. {speaker}", "", redact_hyperliquid_perp_chat_secrets(message.content), ""])
            if message.role == "assistant" and (message.source_mode or message.source_debug):
                lines.extend(["### Source Debug", ""])
                if message.source_mode:
                    lines.append(f"- Source mode: {message.source_mode}")
                for entry in message.source_debug:
                    lines.append(f"- {redact_hyperliquid_perp_chat_secrets(entry)}")
                lines.append("")
    return "\n".join(lines).strip() + "\n"


def save_hyperliquid_perp_chat_transcript(
    session: HyperliquidPerpChatSession,
    output_path: str | Path | None = None,
    *,
    output_root: str | Path = DEFAULT_HYPERLIQUID_PERP_CHAT_DIR,
) -> Path:
    path = Path(output_path) if output_path is not None else hyperliquid_perp_chat_transcript_path(session.context.selected_coin, output_root=output_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(render_hyperliquid_perp_chat_transcript_markdown(session), encoding="utf-8")
    temporary.replace(path)
    return path


def hyperliquid_perp_chat_transcript_path(
    coin: str,
    *,
    output_root: str | Path = DEFAULT_HYPERLIQUID_PERP_CHAT_DIR,
) -> Path:
    clean = normalize_hyperliquid_perp_coin(coin or "HYPE")
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d")
    return Path(output_root) / clean / f"{clean}_Hyperliquid_Perp_Strategy_Chat_{timestamp}.md"


def redact_hyperliquid_perp_chat_secrets(value: str) -> str:
    text = str(value or "")
    text = re.sub(r"sk-[A-Za-z0-9_-]{8,}", "sk-[REDACTED]", text)
    text = re.sub(r"Bearer\s+[A-Za-z0-9._~+/=-]+", "Bearer [REDACTED]", text, flags=re.IGNORECASE)
    text = re.sub(r"0x[a-fA-F0-9]{64}", "0x[REDACTED_PRIVATE_KEY]", text)
    text = re.sub(r"0x[a-fA-F0-9]{40}", lambda match: _short_address(match.group(0)), text)
    text = re.sub(
        r"(?i)(access_token|refresh_token|client_secret|api_key|api_secret|private_key|authorization|account_hash|hashValue|cookie)\s*[:=]\s*[^,\s)}]+",
        r"\1=[REDACTED]",
        text,
    )
    return text


def normalize_hyperliquid_perp_coin(value: Any) -> str:
    text = str(value or "").strip().upper()
    if text.startswith("HL:"):
        text = text[3:]
    if "(" in text:
        text = text.split("(", 1)[0].strip()
    for suffix in ("-PERP-SHORT", "-PERP", "-SPOT"):
        if text.endswith(suffix):
            text = text[: -len(suffix)]
    if "/" in text:
        text = text.split("/", 1)[0].strip()
    text = re.sub(r"[^A-Z0-9._-]", "", text)
    if text == "UBTC":
        text = "BTC"
    return text or "HYPE"


def _ticket_context(app_context: Any | None, *, fallback_coin: str) -> dict[str, Any]:
    raw_coin = _first_var_value(app_context, ("hyperliquid_perp_coin_var", "hyperliquid_coin_var", "symbol_var"), fallback_coin)
    side = _first_var_value(app_context, ("hyperliquid_perp_side_var", "side_var"), "buy").strip().lower()
    is_long = side not in {"sell", "short", "a", "ask"}
    size = _safe_number(_first_var_value(app_context, ("hyperliquid_perp_quantity_var", "quantity_var"), ""))
    entry_price = _safe_number(_first_var_value(app_context, ("hyperliquid_perp_limit_price_var", "limit_price_var"), ""))
    tp_price = _safe_number(_first_var_value(app_context, ("hyperliquid_perp_target_price_var", "hyperliquid_target_price_var"), ""))
    sl_price = _safe_number(
        _first_var_value(app_context, ("hyperliquid_perp_stop_price_var", "hyperliquid_bad_price_var", "stop_price_var"), "")
    )
    leverage = _safe_number(_first_var_value(app_context, ("hyperliquid_perp_leverage_var", "hyperliquid_leverage_var"), "1")) or 1.0
    fee_rate = _safe_number(_first_var_value(app_context, ("hyperliquid_perp_fee_rate_var", "hyperliquid_fee_rate_var"), str(DEFAULT_PERP_FEE_RATE_PERCENT)))
    if fee_rate is None:
        fee_rate = DEFAULT_PERP_FEE_RATE_PERCENT
    return {
        "coin": normalize_hyperliquid_perp_coin(raw_coin),
        "side": "buy" if is_long else "sell",
        "direction": "long" if is_long else "short",
        "size": size,
        "entry_price": entry_price,
        "tp_price": tp_price,
        "sl_price": sl_price,
        "leverage": max(leverage, ZERO_EPSILON),
        "margin_mode": _first_var_value(app_context, ("hyperliquid_perp_margin_mode_var", "hyperliquid_margin_mode_var"), "Cross") or "Cross",
        "time_in_force": _first_var_value(app_context, ("hyperliquid_perp_tif_var", "hyperliquid_tif_var"), "Gtc") or "Gtc",
        "reduce_only": _bool_var_value(app_context, ("hyperliquid_perp_reduce_only_var", "hyperliquid_reduce_only_var")),
        "attach_tpsl": _bool_var_value(app_context, ("hyperliquid_perp_attach_tpsl_var", "hyperliquid_attach_tpsl_var")),
        "fee_rate_percent_per_side": max(fee_rate, 0.0),
    }


def _portfolio_from_app(app_context: Any | None) -> Any | None:
    broker = getattr(app_context, "broker", None)
    if broker is not None:
        try:
            return broker.get_portfolio()
        except Exception:
            return None
    return getattr(app_context, "current_portfolio", None)


def _extract_perp_positions(portfolio: Any, selected_coin: str) -> list[HyperliquidPerpPosition]:
    results: list[HyperliquidPerpPosition] = []
    for raw_symbol, position in getattr(portfolio, "positions", {}).items():
        parsed = _parse_perp_symbol(str(raw_symbol), position)
        if parsed is None:
            continue
        coin, account_label, direction_sign = parsed
        if normalize_hyperliquid_perp_coin(coin) != selected_coin:
            continue
        quantity = abs(float(getattr(position, "quantity", 0.0) or 0.0))
        if quantity <= ZERO_EPSILON:
            continue
        entry = float(getattr(position, "average_cost", 0.0) or 0.0)
        mark = float(getattr(position, "last_price", 0.0) or 0.0)
        if entry <= 0 and mark > 0:
            entry = mark
        if mark <= 0 and entry > 0:
            mark = entry
        if entry <= 0 or mark <= 0:
            continue
        signed_size = quantity * direction_sign
        raw_pnl = getattr(position, "open_profit_loss", None)
        if raw_pnl is None:
            raw_pnl = (mark - entry) * signed_size
        results.append(
            HyperliquidPerpPosition(
                account_label=account_label,
                coin=selected_coin,
                signed_size=signed_size,
                entry_price=entry,
                mark_price=mark,
                unrealized_pnl=_safe_number(raw_pnl),
                source_symbol=str(getattr(position, "symbol", raw_symbol) or raw_symbol),
            )
        )
    return results


def _parse_perp_symbol(symbol: str, position: Any) -> tuple[str, str, int] | None:
    clean = str(symbol or "").strip()
    upper = clean.upper()
    if upper.endswith("-PERP-SHORT"):
        direction_sign = -1
        base = clean[: -len("-PERP-SHORT")]
    elif upper.endswith("-PERP"):
        direction_sign = 1
        base = clean[: -len("-PERP")]
    else:
        return None

    account_label = str(getattr(position, "hyperliquid_account_label", "") or "").strip()
    match = re.search(r"\(([^)]+)\)", base)
    if not account_label and match:
        account_label = match.group(1).strip()
    if not account_label:
        account_label = "Unlabeled"
    coin = re.sub(r"\s*\([^)]+\)", "", base).strip()
    return normalize_hyperliquid_perp_coin(coin), account_label, direction_sign


def _position_to_payload(position: HyperliquidPerpPosition) -> dict[str, Any]:
    return {
        "account_label": position.account_label,
        "coin": position.coin,
        "direction": position.direction,
        "signed_size": position.signed_size,
        "size": position.size,
        "entry_price": position.entry_price,
        "mark_price": position.mark_price,
        "notional": position.notional,
        "unrealized_pnl": position.unrealized_pnl,
        "source_symbol": position.source_symbol,
    }


def _account_summaries(
    portfolio: Any,
    positions: Sequence[HyperliquidPerpPosition],
    app_context: Any | None,
    selected_coin: str,
) -> list[dict[str, Any]]:
    open_orders = _raw_open_orders(app_context)
    labels = {position.account_label for position in positions}
    labels.update(str(order.get("accountLabel") or "").strip() for order in open_orders if isinstance(order, Mapping))
    cash_by_account: dict[str, float] = {}
    for cash in getattr(portfolio, "cash_positions", {}).values():
        source = str(getattr(cash, "source", "") or "")
        if "HYPERLIQUID" not in source.upper():
            continue
        account_label = _account_label_from_text(source) or "Unlabeled"
        labels.add(account_label)
        cash_by_account[account_label] = cash_by_account.get(account_label, 0.0) + float(getattr(cash, "amount", 0.0) or 0.0)

    rows: list[dict[str, Any]] = []
    for label in sorted(item for item in labels if item):
        account_positions = [position for position in positions if position.account_label == label]
        account_orders = [
            order
            for order in open_orders
            if str(order.get("accountLabel") or "").strip() == label
            and normalize_hyperliquid_perp_coin(order.get("coin") or selected_coin) == selected_coin
        ]
        signed_size = sum(position.signed_size for position in account_positions)
        gross_notional = sum(position.notional for position in account_positions)
        cash = cash_by_account.get(label, 0.0)
        rows.append(
            {
                "account_label": label,
                "available_usdc_or_cash": cash,
                "cockpit_value_estimate": cash + gross_notional,
                "margin_used": None,
                "maintenance_margin": None,
                "current_hype_signed_size": signed_size,
                "current_hype_direction": _signed_direction(signed_size),
                "current_hype_gross_notional": gross_notional,
                "position_count": len(account_positions),
                "open_order_count": len(account_orders),
            }
        )
    return rows


def _raw_open_orders(app_context: Any | None) -> list[dict[str, Any]]:
    orders: dict[str, dict[str, Any]] = {}
    for source in (
        getattr(app_context, "hyperliquid_open_order_by_oid", None),
        getattr(getattr(app_context, "hyperliquid_workspace_open_orders_table", None), "_hyperliquid_open_order_by_oid", None),
    ):
        if not isinstance(source, Mapping):
            continue
        for key, value in source.items():
            if isinstance(value, dict):
                orders[str(key)] = value
    return list(orders.values())


def _matching_open_orders(app_context: Any | None, selected_coin: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for order in _raw_open_orders(app_context):
        coin = normalize_hyperliquid_perp_coin(order.get("coin") or selected_coin)
        if coin != selected_coin:
            continue
        rows.append(_sanitize_open_order(order))
    return rows


def _sanitize_open_order(order: Mapping[str, Any]) -> dict[str, Any]:
    oid = str(order.get("oid") or order.get("orderId") or "").strip()
    side = _order_side_label(order.get("side") or order.get("direction"))
    size = _safe_number(order.get("sz") or order.get("size") or order.get("origSz"))
    price = _safe_number(order.get("limitPx") or order.get("price") or order.get("px"))
    trigger_price = _safe_number(order.get("triggerPx") or order.get("trigger_price"))
    reduce_only = _truthy(order.get("reduceOnly") or order.get("reduce_only") or order.get("isPositionTpsl"))
    account_address = str(order.get("accountAddress") or "").strip()
    return {
        "account_label": str(order.get("accountLabel") or order.get("account") or "--").strip() or "--",
        "account_key": str(order.get("accountKey") or "").strip(),
        "account_address_short": _short_address(account_address) if account_address else "",
        "coin": normalize_hyperliquid_perp_coin(order.get("coin") or ""),
        "side": side,
        "direction": _order_direction_label(side, reduce_only),
        "size": size,
        "limit_price": price,
        "trigger_price": trigger_price,
        "order_type": str(order.get("orderType") or order.get("order_type") or "").strip(),
        "reduce_only": reduce_only,
        "tpsl": str(order.get("tpsl") or "").strip().lower(),
        "oid": oid,
    }


def _market_snapshot(
    selected_coin: str,
    ticket: Mapping[str, Any],
    positions: Sequence[HyperliquidPerpPosition],
) -> dict[str, Any]:
    position_marks = [position.mark_price for position in positions if position.mark_price > 0]
    position_mark = position_marks[0] if position_marks else None
    ticket_entry = _safe_number(ticket.get("entry_price"))
    reference_price = position_mark or ticket_entry
    source = "synced_position_mark" if position_mark else "ticket_entry" if ticket_entry else ""
    return {
        "coin": selected_coin,
        "reference_price": reference_price,
        "reference_source": source or "not_available",
        "synced_position_mark": position_mark,
        "ticket_entry_price": ticket_entry,
        "mark_price": position_mark,
        "oracle_price": None,
        "mid_price": None,
        "funding_rate": None,
        "funding_countdown": None,
        "order_book_top": None,
    }


def _deterministic_math(
    *,
    selected_coin: str,
    ticket: Mapping[str, Any],
    positions: Sequence[HyperliquidPerpPosition],
    open_orders: Sequence[Mapping[str, Any]],
    market_snapshot: Mapping[str, Any],
) -> dict[str, Any]:
    reference_price = _safe_number(market_snapshot.get("reference_price"))
    fee_rate = _safe_number(ticket.get("fee_rate_percent_per_side")) or DEFAULT_PERP_FEE_RATE_PERCENT
    scenario_prices = _scenario_prices(reference_price, ticket)
    result: dict[str, Any] = {
        "combined_exposure": combined_exposure(positions, reference_price=reference_price) if positions and reference_price else {},
        "scenario_prices": scenario_prices,
        "scenario_table": account_scenario_table(positions, scenario_prices=scenario_prices, fee_rate_percent=fee_rate) if positions and scenario_prices else [],
        "open_order_awareness": _open_order_awareness(open_orders, ticket),
        "proposed_ticket": _proposed_ticket_math(ticket),
    }
    proposed = result.get("proposed_ticket")
    if isinstance(proposed, Mapping) and proposed.get("entry_price") and proposed.get("size"):
        result["standard_ladders"] = standard_ladder_plans(
            entry_price=float(proposed["entry_price"]),
            size=float(proposed["size"]),
            is_long=str(proposed.get("direction")) == "long",
            fee_rate_percent=fee_rate,
            leverage=float(proposed.get("leverage") or 1.0),
        )
    else:
        result["standard_ladders"] = {}
    return result


def _scenario_prices(reference_price: float | None, ticket: Mapping[str, Any]) -> list[float]:
    if reference_price is None or reference_price <= 0:
        return []
    prices = {round(reference_price * (1.0 + move), 6) for move in DEFAULT_SCENARIO_MOVES}
    for key in ("entry_price", "tp_price", "sl_price"):
        value = _safe_number(ticket.get(key))
        if value is not None and value > 0:
            prices.add(round(value, 6))
    return sorted(prices)


def _proposed_ticket_math(ticket: Mapping[str, Any]) -> dict[str, Any]:
    entry = _safe_number(ticket.get("entry_price"))
    size = _safe_number(ticket.get("size"))
    if entry is None or entry <= 0 or size is None or size <= ZERO_EPSILON:
        return {}
    is_long = str(ticket.get("direction") or "long").lower() == "long"
    leverage = _safe_number(ticket.get("leverage")) or 1.0
    fee_rate = _safe_number(ticket.get("fee_rate_percent_per_side")) or DEFAULT_PERP_FEE_RATE_PERCENT
    tp_price = _safe_number(ticket.get("tp_price"))
    sl_price = _safe_number(ticket.get("sl_price"))
    tp_case = perp_scenario(entry, tp_price, size, is_long, leverage=leverage, fee_rate_percent=fee_rate) if tp_price else None
    sl_case = perp_scenario(entry, sl_price, size, is_long, leverage=leverage, fee_rate_percent=fee_rate) if sl_price else None
    liq = isolated_liquidation_estimate(entry, is_long, leverage)
    return {
        "coin": ticket.get("coin") or "HYPE",
        "direction": "long" if is_long else "short",
        "size": size,
        "entry_price": entry,
        "notional": entry * size,
        "leverage": leverage,
        "estimated_margin": estimated_margin(entry, size, leverage),
        "fee_rate_percent_per_side": fee_rate,
        "breakeven_exit_price": breakeven_price(entry, is_long, fee_rate),
        "isolated_liquidation_estimate": liq,
        "liquidation_distance_from_entry_percent": liquidation_distance_percent(entry, liq),
        "tp_case": tp_case or {},
        "sl_case": sl_case or {},
        "risk_reward": risk_reward_label(tp_case["net_pnl"], sl_case["net_pnl"]) if tp_case and sl_case else "n/a - TP and SL are both required",
        "tpsl_validation": validate_tpsl_direction(entry, tp_price=tp_price, sl_price=sl_price, is_long=is_long),
    }


def _open_order_awareness(open_orders: Sequence[Mapping[str, Any]], ticket: Mapping[str, Any]) -> dict[str, Any]:
    ticket_direction = str(ticket.get("direction") or "").lower()
    reduce_only_orders = [order for order in open_orders if bool(order.get("reduce_only"))]
    tp_orders = [order for order in open_orders if str(order.get("tpsl") or "").lower() == "tp"]
    sl_orders = [order for order in open_orders if str(order.get("tpsl") or "").lower() == "sl"]
    duplicate_direction = [
        order
        for order in open_orders
        if ticket_direction and ticket_direction in str(order.get("direction") or "").lower()
    ]
    return {
        "matching_open_order_count": len(open_orders),
        "reduce_only_order_count": len(reduce_only_orders),
        "tp_order_count": len(tp_orders),
        "sl_order_count": len(sl_orders),
        "same_direction_as_ticket_count": len(duplicate_direction),
        "readout": _open_order_awareness_readout(open_orders, reduce_only_orders, tp_orders, sl_orders, duplicate_direction),
    }


def _open_order_awareness_readout(
    open_orders: Sequence[Mapping[str, Any]],
    reduce_only_orders: Sequence[Mapping[str, Any]],
    tp_orders: Sequence[Mapping[str, Any]],
    sl_orders: Sequence[Mapping[str, Any]],
    duplicate_direction: Sequence[Mapping[str, Any]],
) -> str:
    if not open_orders:
        return "No matching HYPE open orders are loaded; ladder coverage cannot be confirmed from app state."
    parts = [f"{len(open_orders)} matching open order(s) loaded"]
    if reduce_only_orders:
        parts.append(f"{len(reduce_only_orders)} reduce-only")
    if tp_orders:
        parts.append(f"{len(tp_orders)} TP")
    if sl_orders:
        parts.append(f"{len(sl_orders)} SL")
    if duplicate_direction:
        parts.append(f"{len(duplicate_direction)} same-direction as current ticket")
    return "; ".join(parts) + "."


def _empty_scenario_bucket() -> dict[str, float]:
    return {
        "signed_size": 0.0,
        "gross_open_pnl": 0.0,
        "move_pnl_from_mark": 0.0,
        "estimated_entry_exit_fees": 0.0,
        "estimated_exit_fees": 0.0,
        "net_open_pnl_after_estimated_entry_exit_fees": 0.0,
    }


def _add_position_scenario(
    bucket: dict[str, float],
    position: HyperliquidPerpPosition,
    scenario_price: float,
    fee_rate_percent: float,
) -> None:
    gross_open = (scenario_price - position.entry_price) * position.signed_size
    move_from_mark = (scenario_price - position.mark_price) * position.signed_size
    entry_exit_fees = perp_fee_amount(position.entry_price, scenario_price, position.size, fee_rate_percent)
    exit_fees = perp_fee_amount(
        position.entry_price,
        scenario_price,
        position.size,
        fee_rate_percent,
        include_entry_fee=False,
        include_exit_fee=True,
    )
    bucket["signed_size"] += position.signed_size
    bucket["gross_open_pnl"] += gross_open
    bucket["move_pnl_from_mark"] += move_from_mark
    bucket["estimated_entry_exit_fees"] += entry_exit_fees
    bucket["estimated_exit_fees"] += exit_fees
    bucket["net_open_pnl_after_estimated_entry_exit_fees"] += gross_open - entry_exit_fees


def _merge_scenario_bucket(target: dict[str, float], source: Mapping[str, float]) -> None:
    for key in target:
        target[key] += float(source.get(key, 0.0) or 0.0)


def _finalize_scenario_bucket(bucket: Mapping[str, float]) -> dict[str, Any]:
    signed_size = float(bucket.get("signed_size", 0.0) or 0.0)
    return {
        "signed_size": signed_size,
        "direction": _signed_direction(signed_size),
        "gross_open_pnl": bucket.get("gross_open_pnl", 0.0),
        "move_pnl_from_mark": bucket.get("move_pnl_from_mark", 0.0),
        "estimated_entry_exit_fees": bucket.get("estimated_entry_exit_fees", 0.0),
        "estimated_exit_fees": bucket.get("estimated_exit_fees", 0.0),
        "net_open_pnl_after_estimated_entry_exit_fees": bucket.get("net_open_pnl_after_estimated_entry_exit_fees", 0.0),
    }


def _ladder_close_sizes(*, size: float, intended_total: float, levels: Sequence[Mapping[str, Any]]) -> list[float]:
    explicit_sizes = [_safe_number(level.get("size") or level.get("close_size")) for level in levels]
    if any(value is not None for value in explicit_sizes):
        return [min(max(value or 0.0, 0.0), size) for value in explicit_sizes]

    fractions = [_safe_number(level.get("fraction") or level.get("close_fraction")) for level in levels]
    if all(value is not None for value in fractions):
        sizes = [max(float(value or 0.0), 0.0) * size for value in fractions]
        return _cap_ladder_sizes(sizes, size)

    weights = [_safe_number(level.get("weight")) for level in levels]
    if not any(value is not None and value > 0 for value in weights):
        weights = [1.0 for _level in levels]
    clean_weights = [max(float(value or 0.0), 0.0) for value in weights]
    total_weight = sum(clean_weights)
    if total_weight <= ZERO_EPSILON:
        return [0.0 for _level in levels]

    sizes = []
    allocated = 0.0
    for index, weight in enumerate(clean_weights):
        if index == len(clean_weights) - 1:
            close_size = max(intended_total - allocated, 0.0)
        else:
            close_size = intended_total * weight / total_weight
        sizes.append(close_size)
        allocated += close_size
    return _cap_ladder_sizes(sizes, size)


def _cap_ladder_sizes(sizes: Sequence[float], size: float) -> list[float]:
    result: list[float] = []
    allocated = 0.0
    for value in sizes:
        close_size = min(max(float(value or 0.0), 0.0), max(size - allocated, 0.0))
        result.append(close_size)
        allocated += close_size
    return result


def _ladder_level_price(entry_price: float, is_long: bool, level: Mapping[str, Any]) -> float:
    price = _safe_number(level.get("price") or level.get("exit_price"))
    if price is not None and price > 0:
        return price
    move = _safe_number(level.get("move_percent") or level.get("move"))
    if move is None:
        raise ValueError("Each ladder level needs a price or move_percent.")
    if abs(move) > 1:
        move = move / 100.0
    signed_move = move if is_long else -move
    return entry_price * (1.0 + signed_move)


def _first_var_value(app_context: Any | None, names: Sequence[str], default: str = "") -> str:
    for name in names:
        var = getattr(app_context, name, None)
        try:
            value = str(var.get()).strip()
        except Exception:
            continue
        if value:
            return value
    return default


def _bool_var_value(app_context: Any | None, names: Sequence[str]) -> bool:
    for name in names:
        var = getattr(app_context, name, None)
        try:
            return _truthy(var.get())
        except Exception:
            continue
    return False


def _truthy(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value or "").strip().lower() in {"1", "true", "yes", "y", "on"}


def _safe_number(value: Any) -> float | None:
    if value is None or value == "":
        return None
    if isinstance(value, (int, float)):
        return float(value)
    try:
        text = str(value).replace("$", "").replace(",", "").replace("%", "").strip()
        if not text:
            return None
        return float(text)
    except (TypeError, ValueError):
        return None


def _account_label_from_text(value: str) -> str:
    match = re.search(r"\(([^)]+)\)", str(value or ""))
    return match.group(1).strip() if match else ""


def _order_side_label(value: Any) -> str:
    side = str(value or "").strip().upper()
    if side in {"B", "BUY", "BID"}:
        return "BUY"
    if side in {"A", "S", "SELL", "ASK"}:
        return "SELL"
    return side or "UNKNOWN"


def _order_direction_label(side: str, reduce_only: bool) -> str:
    if side == "BUY":
        return "buy / cover" if reduce_only else "buy / long"
    if side == "SELL":
        return "sell / close" if reduce_only else "sell / short"
    return side.lower() or "unknown"


def _signed_direction(signed_size: float) -> str:
    if signed_size > ZERO_EPSILON:
        return "net long"
    if signed_size < -ZERO_EPSILON:
        return "net short"
    return "flat"


def _price_relation(price: float, entry: float) -> str:
    if price > entry:
        return "above"
    if price < entry:
        return "below"
    return "at"


def _validate_positive(value: float, label: str) -> None:
    if value <= 0:
        raise ValueError(f"{label} must be positive.")


def _validate_non_negative(value: float, label: str) -> None:
    if value < 0:
        raise ValueError(f"{label} cannot be negative.")


def _clean_prompt(prompt: str) -> str:
    return re.sub(r"\s+", " ", str(prompt or "").strip())


def _clean_answer(answer: str) -> str:
    return redact_hyperliquid_perp_chat_secrets(str(answer or "").strip())


def _positive_timeout_seconds(value: float | None, env_value: str | None) -> float:
    for candidate in (value, env_value, HYPERLIQUID_PERP_CHAT_OPENAI_TIMEOUT_SECONDS):
        try:
            parsed = float(candidate)  # type: ignore[arg-type]
        except (TypeError, ValueError):
            continue
        if parsed > 0:
            return parsed
    return HYPERLIQUID_PERP_CHAT_OPENAI_TIMEOUT_SECONDS


def _is_timeout_exception(exc: Exception) -> bool:
    if isinstance(exc, TimeoutError):
        return True
    name = type(exc).__name__.lower()
    message = str(exc).lower()
    return "timeout" in name or "timed out" in message or "read timed out" in message


def _notify_progress(callback: ProgressCallback | None, message: str) -> None:
    if callback is None:
        return
    try:
        callback(message)
    except Exception:
        LOGGER.debug("Hyperliquid perp chat progress callback failed", exc_info=True)


def _source_mode(context: HyperliquidPerpChatContext) -> str:
    if context.positions and context.deterministic_math.get("scenario_table"):
        return "hyperliquid_perp_context_with_deterministic_math"
    if context.ticket:
        return "hyperliquid_perp_ticket_context_only"
    return "hyperliquid_perp_minimal_context"


def _source_debug_lines(request_payload: Mapping[str, Any], diagnostics: Mapping[str, Any]) -> list[str]:
    context = request_payload.get("hyperliquid_perp_context") if isinstance(request_payload, Mapping) else {}
    metadata = context.get("source_metadata") if isinstance(context, Mapping) else {}
    deterministic = context.get("deterministic_math") if isinstance(context, Mapping) else {}
    available = metadata.get("available", []) if isinstance(metadata, Mapping) else []
    unavailable = metadata.get("unavailable", []) if isinstance(metadata, Mapping) else []
    warnings = metadata.get("warnings", []) if isinstance(metadata, Mapping) else []
    scenario_table = deterministic.get("scenario_table") if isinstance(deterministic, Mapping) else []
    lines = [
        f"available_context={len(available)}",
        f"unavailable_context={len(unavailable)}",
        f"warnings={len(warnings)}",
        f"account_count={len(context.get('accounts', [])) if isinstance(context.get('accounts'), list) else 0}",
        f"position_count={len(context.get('positions', [])) if isinstance(context.get('positions'), list) else 0}",
        f"open_order_count={len(context.get('open_orders', [])) if isinstance(context.get('open_orders'), list) else 0}",
        f"scenario_row_count={len(scenario_table) if isinstance(scenario_table, list) else 0}",
    ]
    request_budget = request_payload.get("request_budget") if isinstance(request_payload, Mapping) else {}
    if isinstance(request_budget, Mapping):
        for key in ("pre_trim_payload_chars", "final_payload_chars", "budget_trimmed", "request_payload_char_limit"):
            if key in request_budget:
                lines.append(f"{key}={request_budget.get(key)}")
    lines.extend(f"{key}={value}" for key, value in diagnostics.items())
    return [redact_hyperliquid_perp_chat_secrets(line) for line in lines]


def _enforce_request_payload_budget(payload: dict[str, Any]) -> dict[str, Any]:
    pre_trim_chars = len(_serialize_request_payload(payload))
    trimmed = pre_trim_chars > HYPERLIQUID_PERP_CHAT_REQUEST_CHAR_LIMIT
    if not trimmed:
        _finalize_request_budget(payload, pre_trim_chars, trimmed=False)
        return payload

    context = payload.get("hyperliquid_perp_context")
    if isinstance(context, dict):
        context["open_orders"] = _short_list(context.get("open_orders"), 12)
        deterministic = context.get("deterministic_math")
        if isinstance(deterministic, dict):
            deterministic["scenario_table"] = _short_list(deterministic.get("scenario_table"), 15)
            ladders = deterministic.get("standard_ladders")
            if isinstance(ladders, dict):
                for key, rows in list(ladders.items()):
                    ladders[key] = _short_list(rows, 8)
        metadata = context.get("source_metadata")
        if isinstance(metadata, dict):
            for key in ("available", "unavailable", "warnings"):
                metadata[key] = [_shorten(item, 280) for item in metadata.get(key, [])[:12] if str(item).strip()]

    if len(_serialize_request_payload(payload)) > HYPERLIQUID_PERP_CHAT_REQUEST_CHAR_LIMIT:
        _trim_strings_for_budget(payload, HYPERLIQUID_PERP_CHAT_REQUEST_CHAR_LIMIT)
    _finalize_request_budget(payload, pre_trim_chars, trimmed=True)
    return payload


def _trim_strings_for_budget(value: Any, limit: int) -> None:
    if len(_serialize_request_payload(value)) <= limit:
        return
    if isinstance(value, dict):
        for key, item in list(value.items()):
            if isinstance(item, str):
                value[key] = _shorten(item, 1_000)
            else:
                _trim_strings_for_budget(item, limit)
            if len(_serialize_request_payload(value)) <= limit:
                return
    elif isinstance(value, list):
        while len(value) > 5 and len(_serialize_request_payload(value)) > limit:
            value.pop()
        for item in value:
            _trim_strings_for_budget(item, limit)
            if len(_serialize_request_payload(value)) <= limit:
                return


def _finalize_request_budget(payload: dict[str, Any], pre_trim_chars: int, *, trimmed: bool) -> None:
    request_budget = payload.setdefault("request_budget", {})
    if not isinstance(request_budget, dict):
        payload["request_budget"] = request_budget = {}
    request_budget["request_payload_char_limit"] = HYPERLIQUID_PERP_CHAT_REQUEST_CHAR_LIMIT
    request_budget["pre_trim_payload_chars"] = pre_trim_chars
    request_budget["budget_trimmed"] = bool(trimmed)
    request_budget["final_payload_chars"] = len(_serialize_request_payload(payload))


def _serialize_request_payload(payload: Mapping[str, Any]) -> str:
    return json.dumps(_json_safe(payload), ensure_ascii=True, sort_keys=True, indent=2)


def _json_safe(value: Any) -> Any:
    if isinstance(value, Mapping):
        result: dict[str, Any] = {}
        for key, item in value.items():
            key_text = str(key)
            if _is_secret_key(key_text):
                continue
            result[key_text] = _json_safe(item)
        return result
    if isinstance(value, (list, tuple, set)):
        return [_json_safe(item) for item in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        if isinstance(value, str):
            return redact_hyperliquid_perp_chat_secrets(value)
        return value
    return redact_hyperliquid_perp_chat_secrets(str(value))


def _is_secret_key(key: str) -> bool:
    compact = re.sub(r"[^a-z0-9]", "", key.lower())
    return any(
        part in compact
        for part in (
            "token",
            "secret",
            "privatekey",
            "authorization",
            "apikey",
            "password",
            "credential",
            "cookie",
            "hashvalue",
            "accounthash",
        )
    )


def _short_list(value: Any, limit: int) -> Any:
    if isinstance(value, list):
        return value[:limit]
    return value


def _shorten(value: Any, limit: int) -> str:
    text = redact_hyperliquid_perp_chat_secrets(str(value or "").strip())
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 70)].rstrip() + f"\n\n[Truncated to {limit} characters for Hyperliquid Perp Chat context.]"


def _mark_available(metadata: dict[str, Any], key: str, detail: str) -> None:
    metadata.setdefault("available", []).append(f"{key}: {detail}")


def _mark_unavailable(metadata: dict[str, Any], key: str, detail: str) -> None:
    metadata.setdefault("unavailable", []).append(f"{key}: {detail}")


def _mark_warning(metadata: dict[str, Any], detail: str) -> None:
    metadata.setdefault("warnings", []).append(detail)


def _not_available() -> dict[str, str]:
    return {"status": "Not available in the provided context."}


def _approx_token_count(chars: int) -> int:
    return max(1, int(chars / HYPERLIQUID_PERP_CHAT_APPROX_CHARS_PER_TOKEN))


def _short_address(address: str) -> str:
    clean = str(address or "").strip()
    if len(clean) < 12:
        return clean
    return f"{clean[:6]}...{clean[-4:]}"
