from __future__ import annotations

import csv
import re
from dataclasses import dataclass
from pathlib import Path

from app.core.portfolio_io import SNAPSHOT_PATH

MONEY_RE = re.compile(r"\$?\(?-?[\d,]+(?:\.\d+)?\)?")
TRAILING_SYMBOL_RE = re.compile(r"([A-Z][A-Z0-9.]{0,5})$")


@dataclass(frozen=True)
class ParsedPdfSnapshot:
    cash: float
    positions_count: int
    output_path: Path
    source_path: Path


def _parse_money(value: str) -> float:
    cleaned = value.strip().replace("$", "").replace(",", "")
    is_negative = cleaned.startswith("(") and cleaned.endswith(")")
    cleaned = cleaned.strip("()")
    amount = float(cleaned)
    return -amount if is_negative else amount


def _extract_text_from_pdf(pdf_path: Path) -> str:
    try:
        from pypdf import PdfReader
    except ImportError as exc:
        raise RuntimeError(
            "PDF import requires pypdf. Install it with: pip install -r requirements.txt"
        ) from exc

    reader = PdfReader(str(pdf_path))
    text_parts: list[str] = []
    for page in reader.pages:
        text_parts.append(page.extract_text() or "")
    text = "\n".join(text_parts)
    if not text.strip():
        raise ValueError(
            "No text could be extracted from this PDF. If it is image-only, export a text-based PDF or CSV from Robinhood."
        )
    return text


def _extract_cash(text: str) -> float:
    patterns = [
        r"Individual cash\s+[\d.]+%\s+\$([\d,]+(?:\.\d+)?)",
        r"Individual Cash\s+\$([\d,]+(?:\.\d+)?)",
        r"Withdrawable Cash\s+\$([\d,]+(?:\.\d+)?)",
    ]
    for pattern in patterns:
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if match:
            return _parse_money(match.group(1))
    raise ValueError("Could not find Individual Cash in the Robinhood PDF text.")


def _symbol_from_token(token: str) -> str | None:
    token = token.strip()
    if re.fullmatch(r"[A-Z][A-Z0-9.]{0,5}", token):
        return token
    match = TRAILING_SYMBOL_RE.search(token)
    return match.group(1) if match else None


def _parse_stock_line(line: str) -> tuple[str, float, float, float] | None:
    """Parse a Robinhood stock row into symbol, quantity, average_cost, last_price.

    Robinhood PDF rows are shaped like:
    AMD AMD 3 $450.45 $323.89 $379.69 $1,351.35

    Some extracted rows have the symbol glued to the company name, e.g.
    Taiwan Semiconductor Manuf…TSM 0.515 $403.69 ...
    so this parser works from the right side of the line.
    """
    line = line.strip()
    if not line or "$" not in line:
        return None

    skip_starts = (
        "stocks & options",
        "crypto",
        "total portfolio",
        "individual cash",
        "withdrawable cash",
        "cash earning",
        "interest accrued",
        "lifetime interest",
        "margin",
        "instant deposits",
        "name symbol",
    )
    if line.lower().startswith(skip_starts):
        return None

    tokens = line.split()
    first_money_index = next((idx for idx, token in enumerate(tokens) if token.startswith("$")), None)
    if first_money_index is None or first_money_index < 2:
        return None

    try:
        quantity = float(tokens[first_money_index - 1].replace(",", ""))
    except ValueError:
        return None

    symbol = _symbol_from_token(tokens[first_money_index - 2])
    if not symbol:
        return None

    money_tokens = [token for token in tokens[first_money_index:] if token.startswith("$")]
    if len(money_tokens) < 3:
        return None

    try:
        last_price = _parse_money(money_tokens[0])
        average_cost = _parse_money(money_tokens[1])
    except ValueError:
        return None

    return symbol, quantity, average_cost, last_price


def _stock_section_lines(text: str) -> list[str]:
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    collected: list[str] = []
    in_stocks = False

    for line in lines:
        lowered = line.lower()
        if lowered == "stocks" or lowered.startswith("stocks\n"):
            in_stocks = True
            continue
        if in_stocks and (lowered == "crypto" or lowered.startswith("crypto") or lowered.startswith("margin investing")):
            break
        if in_stocks:
            collected.append(line)

    return collected or lines


def import_robinhood_pdf_to_snapshot(
    pdf_path: str | Path,
    output_path: str | Path = SNAPSHOT_PATH,
) -> ParsedPdfSnapshot:
    pdf_path = Path(pdf_path)
    output_path = Path(output_path)

    if not pdf_path.exists():
        raise FileNotFoundError(f"PDF not found: {pdf_path}")

    text = _extract_text_from_pdf(pdf_path)
    cash = _extract_cash(text)

    positions: dict[str, tuple[float, float, float]] = {}
    for line in _stock_section_lines(text):
        parsed = _parse_stock_line(line)
        if parsed is None:
            continue
        symbol, quantity, average_cost, last_price = parsed
        positions[symbol] = (quantity, average_cost, last_price)

    if not positions:
        raise ValueError(
            "No stock positions could be parsed from the PDF. Try a text-based Robinhood PDF/export or use portfolio_snapshot.csv."
        )

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["type", "symbol", "quantity", "average_cost", "last_price", "notes"])
        writer.writerow(["cash", "CASH", "", "", f"{cash:.2f}", f"imported from {pdf_path.name}"])
        for symbol in sorted(positions):
            quantity, average_cost, last_price = positions[symbol]
            writer.writerow(
                [
                    "position",
                    symbol,
                    f"{quantity:g}",
                    f"{average_cost:.4f}",
                    f"{last_price:.4f}",
                    f"imported from {pdf_path.name}",
                ]
            )

    return ParsedPdfSnapshot(
        cash=cash,
        positions_count=len(positions),
        output_path=output_path,
        source_path=pdf_path,
    )
