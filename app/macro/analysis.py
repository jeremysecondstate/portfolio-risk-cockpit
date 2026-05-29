from __future__ import annotations

from collections import defaultdict

from app.macro.models import MacroRelease, MacroSnapshot


def format_macro_report(snapshot: MacroSnapshot) -> str:
    lines = [
        "Official Macro Snapshot",
        f"Fetched: {snapshot.fetched_at}",
        "",
        "Macro release snapshot:",
    ]
    for release in _display_releases(snapshot.releases):
        lines.append(f"- {release.metric} ({release.source}, {release.period}, {release.freshness_status}): {_value_text(release)}")
        if release.notes:
            lines.append(f"  {release.notes}")

    lines.extend(["", "Market impact:", *market_impact_lines(snapshot.releases)])
    lines.extend(["", "Upcoming / recent official releases:", *release_calendar_lines(snapshot)])
    lines.extend(["", "Source status:"])
    for status in snapshot.source_statuses:
        suffix = " using cached fallback" if status.cached_fallback else ""
        message = f" - {status.message}" if status.message else ""
        lines.append(f"- {status.source}: {status.status}{suffix}; checked {status.fetched_at}{message}")
    return "\n".join(lines)


def market_impact_lines(releases: list[MacroRelease]) -> list[str]:
    signals = _category_signals(releases)
    inflation = signals["inflation"]
    labor = signals["labor"]
    growth = signals["growth"] + signals["consumer"]
    rates = signals["treasury"] + signals["rates"]
    energy = signals["energy"]
    housing = signals["housing"]

    lines = [
        f"- Inflation: {_signal_text(inflation, hotter='hotter / more hawkish', cooler='cooler / more dovish')}.",
        f"- Labor: {_signal_text(labor, hotter='stronger labor', cooler='weaker labor')}.",
        f"- Growth/consumer: {_signal_text(growth, hotter='stronger demand', cooler='weaker demand')}.",
        f"- Rates/Treasury: {_signal_text(rates, hotter='yields/rate pressure up', cooler='yields/rate pressure down')}.",
        f"- Energy: {_signal_text(energy, hotter='energy inflation pressure up', cooler='energy pressure down')}.",
        f"- Housing: {_signal_text(housing, hotter='housing stronger', cooler='housing weaker')}.",
        "",
        "Asset-class readout:",
    ]
    risk_pressure = inflation + rates
    demand = labor + growth
    if risk_pressure > 0:
        lines.append("- Broad equities: higher inflation/rate pressure is a headwind for multiples; prefer tighter entry discipline.")
        lines.append("- Growth/tech: more sensitive to higher yields; bullish setups need stronger confirmation.")
        lines.append("- Bonds/rate-sensitive ETFs: higher yield pressure is bearish for duration-heavy exposure.")
    elif risk_pressure < 0:
        lines.append("- Broad equities: cooler inflation/rate pressure is supportive if growth is not deteriorating sharply.")
        lines.append("- Growth/tech: lower yield pressure is usually supportive for long-duration earnings.")
        lines.append("- Bonds/rate-sensitive ETFs: lower yield pressure is supportive for duration.")
    else:
        lines.append("- Broad equities: macro impulse is mixed/neutral; symbol-level trend and earnings matter more.")
        lines.append("- Growth/tech: no clear macro rate impulse from the loaded official data.")

    if demand > 0:
        lines.append("- Financials/consumer discretionary: stronger labor/growth can support credit demand and spending, unless it revives rate fears.")
    elif demand < 0:
        lines.append("- Financials/consumer discretionary: weaker growth/labor can pressure cyclicals and risk appetite.")
    else:
        lines.append("- Financials/consumer discretionary: demand read is neutral from the loaded official data.")

    if energy > 0:
        lines.append("- Energy: inventory/price pressure would usually favor energy producers but can hurt inflation-sensitive sectors.")
    elif energy < 0:
        lines.append("- Energy: softer energy pressure can help inflation-sensitive sectors but may weigh on producers.")
    return lines


def release_calendar_lines(snapshot: MacroSnapshot) -> list[str]:
    categories: dict[str, list[MacroRelease]] = defaultdict(list)
    for release in snapshot.releases:
        categories[release.category].append(release)
    lines: list[str] = []
    for category in ("inflation", "labor", "growth", "consumer", "treasury", "rates", "energy", "housing"):
        releases = [release for release in categories.get(category, []) if release.period and release.period != "--"]
        if not releases:
            continue
        freshest = releases[0]
        lines.append(f"- {category.title()}: latest loaded {freshest.metric} for {freshest.period} from {freshest.source}; release timestamp {freshest.release_timestamp or '--'}.")
    planned = [release for release in snapshot.releases if release.freshness_status == "planned"]
    if planned:
        lines.append("- Planned source hooks: " + ", ".join(f"{release.source} {release.metric}" for release in planned[:6]) + ".")
    return lines or ["- No official release rows were available; check source status below."]


def _display_releases(releases: list[MacroRelease]) -> list[MacroRelease]:
    priority = {"inflation": 0, "labor": 1, "growth": 2, "consumer": 3, "treasury": 4, "rates": 5, "energy": 6}
    concrete = [release for release in releases if release.actual is not None]
    planned = [release for release in releases if release.actual is None and release.freshness_status in {"planned", "cached", "error"}]
    return sorted(concrete, key=lambda release: (priority.get(release.category, 99), release.metric))[:14] + planned[:8]


def _value_text(release: MacroRelease) -> str:
    actual = "--" if release.actual is None else f"{release.actual:g}"
    prior = "--" if release.prior is None else f"{release.prior:g}"
    unit = f" {release.unit}" if release.unit else ""
    delta = ""
    if release.actual is not None and release.prior is not None:
        delta = f", change {release.actual - release.prior:+.2f}"
    return f"actual {actual}{unit}, prior {prior}{unit}{delta}"


def _category_signals(releases: list[MacroRelease]) -> dict[str, int]:
    signals: dict[str, int] = defaultdict(int)
    for release in releases:
        if release.actual is None or release.prior is None:
            continue
        delta = release.actual - release.prior
        if abs(delta) < 0.0001:
            continue
        metric = release.metric.lower()
        direction = 1 if delta > 0 else -1
        if "unemployment" in metric:
            direction *= -1
        if "treasury yield" in metric:
            signals["treasury"] += direction
        elif "energy" in release.category or "crude" in metric or "gas" in metric:
            signals["energy"] += direction
        else:
            signals[release.category] += direction
    return signals


def _signal_text(score: int, *, hotter: str, cooler: str) -> str:
    if score > 0:
        return hotter
    if score < 0:
        return cooler
    return "neutral/mixed"
