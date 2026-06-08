from __future__ import annotations

from app.ui.research_widgets import parse_markdown_pipe_table, parse_visual_readout, truncate_with_detail


def test_markdown_pipe_table_parser_handles_alignment_and_escaped_pipes() -> None:
    lines = [
        "| Component | Vote | Reason |",
        "| --- | ---: | --- |",
        "| Chart setup | +42 | Breakout held above VWAP \\| volume confirmed |",
    ]

    table, consumed = parse_markdown_pipe_table(lines)

    assert table is not None
    assert consumed == 3
    assert table.headers == ("Component", "Vote", "Reason")
    assert table.rows == (("Chart setup", "+42", "Breakout held above VWAP | volume confirmed"),)


def test_visual_readout_parser_splits_sections_tables_and_secondary_detail() -> None:
    readout = """
Recommendation Engine Explanation - TEST
========================================

Recommendation: Constructive / defined-risk only
Confidence: High (84/100)

Evidence Components:
| Component | Vote | Confidence | Status | Reason |
| --- | --- | --- | --- | --- |
| Chart setup | +48 | 80% | Supportive | Trend and volume confirm. |

Why:
- Chart setup is supportive.

Original / raw generated readout:
Raw pipe tables and source prose stay available here.
""".strip()

    parsed = parse_visual_readout(readout)
    table_blocks = [block for block in parsed.blocks if block.kind == "table"]
    secondary_blocks = [block for block in parsed.blocks if block.secondary]

    assert parsed.title == "Recommendation Engine Explanation - TEST"
    assert ("Recommendation", "Constructive / defined-risk only") in parsed.key_values
    assert table_blocks[0].title == "Evidence Components"
    assert table_blocks[0].table_rows[0][0] == "Chart setup"
    assert secondary_blocks
    assert "Raw pipe tables" in secondary_blocks[0].rows[0]


def test_truncate_with_detail_preserves_original_text() -> None:
    original = "Important detail " * 40

    result = truncate_with_detail(original, 80)

    assert result.truncated
    assert result.display.endswith("...")
    assert len(result.display) <= 80
    assert result.detail == " ".join(original.split())

