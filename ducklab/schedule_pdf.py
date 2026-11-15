"""Direct, browser-independent PDF export for generated weekly schedules."""

from __future__ import annotations

from datetime import date, timedelta

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_LEFT
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.pdfbase.pdfmetrics import stringWidth
from reportlab.platypus import (
    BaseDocTemplate, Frame, KeepTogether, PageTemplate, Paragraph, Spacer,
    Table, TableStyle,
)

MARGIN = 8 * mm
PAGE_SIZE = landscape(A4)


def _colour(value: str):
    try:
        return colors.HexColor(value)
    except (ValueError, AttributeError):
        return colors.white


def _text(value) -> str:
    """Paragraph text escaping without requiring another utility dependency."""
    return (str(value or "").replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;"))


def _paragraph(value, style):
    return Paragraph(_text(value), style)


def _two_line_name(value: str, width: float) -> str:
    """Keep long staff names within two lines, adding an ellipsis if needed."""
    text = str(value or "")
    if stringWidth(text, "Helvetica", 7.5) <= width * 2:
        return _text(text)
    words = text.split()
    first = ""
    while words and stringWidth(f"{first} {words[0]}".strip(),
                                "Helvetica", 7.5) <= width:
        first = f"{first} {words.pop(0)}".strip()
    remaining = " ".join(words)
    suffix = "…"
    while remaining and stringWidth(f"{remaining}{suffix}", "Helvetica", 7.5) > width:
        remaining = remaining[:-1].rstrip()
    return f"{_text(first)}<br/>{_text(remaining)}{suffix}"


def _legend_detail(code: dict) -> str:
    if code.get("start_time") and code.get("end_time"):
        def display_time(value):
            hour, minute = (int(part) for part in value.split(":"))
            suffix = "AM" if hour < 12 else "PM"
            return f"{hour % 12 or 12}:{minute:02d} {suffix}"
        return (f"{display_time(code['start_time'])} - "
                f"{display_time(code['end_time'])}")
    return code["label"]


def write_schedule_pdf(path, *, store_label: str, week_start: date,
                       target: str, staff: list[dict], entries: list[dict],
                       codes: list[dict]) -> None:
    """Write one fitted A4-landscape schedule; no browser or print dialog."""
    doc = BaseDocTemplate(
        str(path), pagesize=PAGE_SIZE, leftMargin=MARGIN, rightMargin=MARGIN,
        topMargin=MARGIN, bottomMargin=MARGIN,
        title="Staff Weekly Planned Schedule", author="Opscore",
    )
    frame = Frame(MARGIN, MARGIN, PAGE_SIZE[0] - 2 * MARGIN,
                  PAGE_SIZE[1] - 2 * MARGIN, leftPadding=0, rightPadding=0,
                  topPadding=0, bottomPadding=0)
    doc.addPageTemplates([PageTemplate(id="schedule", frames=[frame])])

    title_style = ParagraphStyle(
        "ScheduleTitle", fontName="Helvetica-Bold", fontSize=15,
        leading=18, alignment=TA_CENTER,
    )
    meta_style = ParagraphStyle(
        "ScheduleMeta", fontName="Helvetica", fontSize=9, leading=11,
        alignment=TA_LEFT,
    )
    legend_style = ParagraphStyle(
        "ScheduleLegend", fontName="Helvetica", fontSize=7.5, leading=9,
    )
    cell_style = ParagraphStyle(
        "ScheduleCell", fontName="Helvetica-Bold", fontSize=7.5, leading=9,
        alignment=TA_CENTER,
    )
    name_style = ParagraphStyle(
        "ScheduleName", fontName="Helvetica", fontSize=7.5, leading=9,
        alignment=TA_LEFT, maxLines=2, splitLongWords=True,
    )
    header_style = ParagraphStyle(
        "ScheduleHeader", parent=cell_style, fontSize=7, leading=8,
    )

    by_code = {row["code"]: row for row in codes}
    assignments = {(row["staff_id"], row["work_date"]): row["code"]
                   for row in entries}
    week_end = week_start + timedelta(days=6)
    week_label = (f"{week_start.day} {week_start.strftime('%b')} - "
                  f"{week_end.day} {week_end.strftime('%b %Y')}")
    header_content = [Paragraph("Staff Weekly Planned Schedule", title_style),
                      Spacer(1, 3 * mm)]
    header_content.append(Table(
        [[_paragraph(f"Store: {store_label}", meta_style),
          _paragraph(f"Target Manpower: {target or '—'}", meta_style),
          _paragraph(f"Week: {week_label}", meta_style)]],
        colWidths=[(PAGE_SIZE[0] - 2 * MARGIN) / 3] * 3,
        style=TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"),
                          ("LEFTPADDING", (0, 0), (-1, -1), 0),
                          ("RIGHTPADDING", (0, 0), (-1, -1), 0),
                          ("TOPPADDING", (0, 0), (-1, -1), 0),
                          ("BOTTOMPADDING", (0, 0), (-1, -1), 0)]),
    ))
    header_content.append(Spacer(1, 3 * mm))

    legend_codes = [row for row in codes
                    if row.get("show_in_legend", 1)
                    and row["code"] not in {"EL", "MC", "OPH"}]
    legend_rows = []
    for start in range(0, len(legend_codes), 6):
        cells = []
        for row in legend_codes[start:start + 6]:
            swatch = Table([[""]], colWidths=[3 * mm], rowHeights=[3 * mm],
                           style=TableStyle([
                               ("BACKGROUND", (0, 0), (-1, -1), _colour(row["color"])),
                               ("BOX", (0, 0), (-1, -1), 0.5, colors.HexColor("#999999")),
                           ]))
            cells.append(Table(
                [[swatch, _paragraph(
                    f"{row['code']}: {_legend_detail(row)}", legend_style)]],
                style=TableStyle([("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                                  ("LEFTPADDING", (0, 0), (-1, -1), 0),
                                  ("RIGHTPADDING", (0, 0), (-1, -1), 4),
                                  ("TOPPADDING", (0, 0), (-1, -1), 0),
                                  ("BOTTOMPADDING", (0, 0), (-1, -1), 0)]),
            ))
        legend_rows.append(cells)
    if legend_rows:
        header_content.append(Table(legend_rows,
                           colWidths=[(PAGE_SIZE[0] - 2 * MARGIN) / 6] * 6,
                           style=TableStyle([
                               ("VALIGN", (0, 0), (-1, -1), "TOP"),
                               ("LEFTPADDING", (0, 0), (-1, -1), 0),
                               ("RIGHTPADDING", (0, 0), (-1, -1), 4),
                               ("TOPPADDING", (0, 0), (-1, -1), 0),
                               ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
                           ])))
        header_content.append(Spacer(1, 2 * mm))
    story = [KeepTogether(header_content)]

    header = [_paragraph("No.", header_style), _paragraph("Name", header_style),
              _paragraph("Staff ID", header_style), _paragraph("Position", header_style)]
    for offset in range(7):
        day = week_start + timedelta(days=offset)
        header.append(Paragraph(f"{day:%a}<br/>{day.day}/{day:%b}", header_style))
    rows = [header]
    name_text_width = 48 * mm - 6
    for index, person in enumerate(staff, 1):
        cells = [_paragraph(str(index), cell_style),
                 Paragraph(_two_line_name(person["name"], name_text_width),
                           name_style),
                 _paragraph(person["staff_code"], cell_style),
                 _paragraph(person["position"], cell_style)]
        for offset in range(7):
            work_date = (week_start + timedelta(days=offset)).isoformat()
            cells.append(_paragraph(
                assignments.get((person["id"], work_date), ""), cell_style))
        rows.append(cells)

    available_width = PAGE_SIZE[0] - 2 * MARGIN
    number_width, staff_width, position_width = 9 * mm, 23 * mm, 15 * mm
    name_width = 48 * mm
    day_width = ((available_width - number_width - name_width - staff_width
                  - position_width) / 7)
    _, header_height = header_content[0].wrap(available_width, 0)
    for item in header_content[1:]:
        header_height += item.wrap(available_width, 0)[1]
    available_height = (PAGE_SIZE[1] - 2 * MARGIN - header_height - 2 * mm)
    row_height = ((available_height - 10 * mm) / max(len(staff), 1)
                  if staff else 10 * mm)
    table = Table(rows, colWidths=[number_width, name_width, staff_width,
                                   position_width, *([day_width] * 7)],
                  rowHeights=[10 * mm, *([row_height] * len(staff))],
                  repeatRows=1, hAlign="LEFT")
    table_style = [
        ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#d1d5db")),
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#e5e7eb")),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("LEFTPADDING", (0, 0), (-1, -1), 3),
        ("RIGHTPADDING", (0, 0), (-1, -1), 3),
        ("TOPPADDING", (0, 0), (-1, -1), 6),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
    ]
    for row_index, person in enumerate(staff, 1):
        for offset in range(7):
            work_date = (week_start + timedelta(days=offset)).isoformat()
            code = assignments.get((person["id"], work_date), "")
            definition = by_code.get(code)
            if definition:
                table_style.append(("BACKGROUND", (4 + offset, row_index),
                                    (4 + offset, row_index),
                                    _colour(definition["color"])))
    table.setStyle(TableStyle(table_style))
    story.append(table)
    doc.build(story)
