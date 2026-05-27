from __future__ import annotations

import calendar
from dataclasses import dataclass
from datetime import date, datetime, timedelta

from app.data.sec_edgar import SecFiling

PERIODIC_FORMS = ("10-Q", "10-K")


@dataclass(frozen=True)
class NextReportWindow:
    next_form: str
    period_end: date
    estimated_filing_date: date
    window_start: date
    window_end: date
    basis: str

    def format_line(self) -> str:
        return (
            "Next report watch: "
            f"estimated {self.next_form} cycle; fiscal period end around {self.period_end.isoformat()}; "
            f"SEC filing/earnings-watch window roughly {self.window_start.isoformat()} to {self.window_end.isoformat()} "
            f"(center {self.estimated_filing_date.isoformat()}). {self.basis}"
        )


def estimate_next_report_window(filings: list[SecFiling]) -> NextReportWindow | None:
    periodic = [filing for filing in filings if filing.form in PERIODIC_FORMS and filing.report_date]
    if not periodic:
        return None

    latest = _latest_by_report_date(periodic)
    latest_period_end = _parse_date(latest.report_date)
    if latest_period_end is None:
        return None

    fiscal_year_end = _latest_fiscal_year_end(periodic)
    next_period_end = _add_months(latest_period_end, 3)
    next_form = _next_form(latest, next_period_end, fiscal_year_end)
    lag_days = _typical_lag_days(periodic, next_form)
    fallback_lag = 42 if next_form == "10-Q" else 70
    if lag_days is None:
        lag_days = fallback_lag
        basis = "Estimated from SEC periodic filing deadlines; no same-form cadence found."
    else:
        basis = "Estimated from this issuer's recent SEC filing cadence; not a company-announced date."

    estimated = next_period_end + timedelta(days=lag_days)
    slack = 7 if next_form == "10-Q" else 14
    return NextReportWindow(
        next_form=next_form,
        period_end=next_period_end,
        estimated_filing_date=estimated,
        window_start=estimated - timedelta(days=slack),
        window_end=estimated + timedelta(days=slack),
        basis=basis,
    )


def format_next_report_watch_line(filings: list[SecFiling]) -> str:
    window = estimate_next_report_window(filings)
    if window is None:
        return "Next report watch: unavailable from recent SEC periodic filings."
    return window.format_line()


def _latest_by_report_date(filings: list[SecFiling]) -> SecFiling:
    return max(filings, key=lambda filing: _parse_date(filing.report_date) or date.min)


def _latest_fiscal_year_end(filings: list[SecFiling]) -> date | None:
    annual_dates = [
        parsed
        for filing in filings
        if filing.form == "10-K"
        for parsed in [_parse_date(filing.report_date)]
        if parsed is not None
    ]
    if not annual_dates:
        return None
    return max(annual_dates)


def _next_form(latest: SecFiling, next_period_end: date, fiscal_year_end: date | None) -> str:
    if latest.form == "10-K":
        return "10-Q"
    if fiscal_year_end and _same_fiscal_month_day(next_period_end, fiscal_year_end):
        return "10-K"
    return "10-Q"


def _same_fiscal_month_day(candidate: date, fiscal_year_end: date) -> bool:
    return candidate.month == fiscal_year_end.month and abs(candidate.day - fiscal_year_end.day) <= 3


def _typical_lag_days(filings: list[SecFiling], form: str) -> int | None:
    lags: list[int] = []
    for filing in filings:
        if filing.form != form:
            continue
        report_date = _parse_date(filing.report_date)
        filing_date = _parse_date(filing.filing_date)
        if report_date is None or filing_date is None:
            continue
        lag = (filing_date - report_date).days
        if 0 <= lag <= 120:
            lags.append(lag)
    if not lags:
        return None
    recent_lags = lags[:4]
    return round(sum(recent_lags) / len(recent_lags))


def _parse_date(value: str) -> date | None:
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except (TypeError, ValueError):
        return None


def _add_months(value: date, months: int) -> date:
    month_index = value.month - 1 + months
    year = value.year + month_index // 12
    month = month_index % 12 + 1
    day = min(value.day, calendar.monthrange(year, month)[1])
    return date(year, month, day)
