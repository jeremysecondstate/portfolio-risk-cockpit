from __future__ import annotations

import re
from dataclasses import dataclass

from app.data.sec_edgar import SecEarningsRelease

MAX_SNIPPET_CHARS = 260


@dataclass(frozen=True)
class EarningsReleaseDigest:
    title: str
    filing_date: str
    filing_items: str
    exhibit_type: str
    source_url: str
    headline_snippets: list[str]
    guidance_snippets: list[str]
    margin_cashflow_snippets: list[str]


def analyze_earnings_release(release: SecEarningsRelease | None) -> EarningsReleaseDigest | None:
    if release is None:
        return None

    text = normalize_release_text(release.text)
    title = _guess_title(text) or f"Latest 8-K earnings exhibit for {release.company.ticker}"
    headline_snippets = _find_snippets(
        text,
        (
            "revenue",
            "net income",
            "diluted earnings per share",
            "diluted eps",
            "earnings per share",
            "gross margin",
            "operating income",
        ),
        limit=5,
    )
    guidance_snippets = _find_snippets(
        text,
        (
            "guidance",
            "outlook",
            "expects",
            "expect",
            "forecast",
            "next quarter",
            "fiscal year",
        ),
        limit=4,
    )
    margin_cashflow_snippets = _find_snippets(
        text,
        (
            "cash flow",
            "free cash flow",
            "operating cash flow",
            "gross margin",
            "operating margin",
            "capital expenditures",
            "capex",
        ),
        limit=4,
    )

    return EarningsReleaseDigest(
        title=title,
        filing_date=release.filing.filing_date,
        filing_items=release.filing.items or "--",
        exhibit_type=release.document.type or release.document.description or "exhibit",
        source_url=release.source_url,
        headline_snippets=headline_snippets,
        guidance_snippets=guidance_snippets,
        margin_cashflow_snippets=margin_cashflow_snippets,
    )


def format_earnings_release_digest(digest: EarningsReleaseDigest | None) -> str:
    lines = [
        "FAST EARNINGS RELEASE LAYER",
        "===========================",
    ]
    if digest is None:
        lines.extend(
            [
                "No recent 8-K earnings-release exhibit was found in the latest SEC filings scan.",
                "The report below falls back to standardized XBRL/companyfacts from 10-Q/10-K style filings.",
            ]
        )
        return "\n".join(lines)

    lines.extend(
        [
            digest.title,
            f"Filed: {digest.filing_date} | 8-K items: {digest.filing_items} | Exhibit: {digest.exhibit_type}",
            f"Source: {digest.source_url}",
            "",
            "Headline / Result Snippets:",
        ]
    )
    _append_snippet_bucket(lines, digest.headline_snippets)

    lines.append("")
    lines.append("Guidance / Outlook Snippets:")
    _append_snippet_bucket(lines, digest.guidance_snippets)

    lines.append("")
    lines.append("Margin / Cash Flow Snippets:")
    _append_snippet_bucket(lines, digest.margin_cashflow_snippets)

    lines.extend(
        [
            "",
            "Use this as the fast official earnings-release layer. Reconcile against the formal 10-Q/10-K and XBRL facts below when those are available.",
        ]
    )
    return "\n".join(lines)


def normalize_release_text(text: str) -> str:
    text = re.sub(r"\s+", " ", text or " ")
    text = text.replace("•", "-")
    return text.strip()


def _append_snippet_bucket(lines: list[str], snippets: list[str]) -> None:
    if not snippets:
        lines.extend(
            [
                "Snippet availability:",
                "No clean snippet found; open the exhibit source for full detail.",
            ]
        )
        return
    for index, snippet in enumerate(snippets, start=1):
        if index > 1:
            lines.append("")
        lines.extend(
            [
                f"Snippet {index}:",
                snippet,
            ]
        )


def _find_snippets(text: str, keywords: tuple[str, ...], *, limit: int) -> list[str]:
    snippets: list[str] = []
    seen_normalized: set[str] = set()
    for keyword in keywords:
        pattern = re.compile(rf"(.{{0,95}}\b{re.escape(keyword)}\b.{{0,165}})", re.IGNORECASE)
        for match in pattern.finditer(text):
            snippet = _clean_snippet(match.group(1))
            normalized = snippet.lower()
            if normalized in seen_normalized or len(snippet) < 30:
                continue
            seen_normalized.add(normalized)
            snippets.append(snippet)
            if len(snippets) >= limit:
                return snippets
    return snippets


def _clean_snippet(value: str) -> str:
    value = re.sub(r"\s+", " ", value).strip(" -–—|•\t\n")
    if len(value) > MAX_SNIPPET_CHARS:
        value = value[: MAX_SNIPPET_CHARS - 1].rstrip() + "..."
    return value


def _guess_title(text: str) -> str | None:
    candidates = re.split(r"(?<=[.!?])\s+|\s{2,}", text[:2000])
    for candidate in candidates[:20]:
        candidate = _clean_snippet(candidate)
        if not candidate:
            continue
        lower = candidate.lower()
        if "results" in lower or "earnings" in lower or "reports" in lower or "announces" in lower:
            return candidate[:120]
    return None
