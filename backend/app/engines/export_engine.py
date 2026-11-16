import csv
import io
from datetime import datetime, timezone
from typing import Any
from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle


def export_to_csv_bytes(columns: list[str], rows: list[dict[str, Any]]) -> bytes:
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=columns, extrasaction="ignore")
    writer.writeheader()
    for r in rows:
        writer.writerow(r)
    # UTF-8 BOM for seamless Excel Persian/Arabic & Unicode rendering
    return ("\ufeff" + buf.getvalue()).encode("utf-8")


def export_to_excel_bytes(
    title: str, sheets_data: list[dict[str, Any]]
) -> bytes:
    wb = Workbook()
    # Remove default sheet
    wb.remove(wb.active)

    header_font = Font(bold=True, color="FFFFFF")
    header_fill = PatternFill(start_color="1E293B", end_color="1E293B", fill_type="solid")

    for idx, sheet in enumerate(sheets_data):
        sname = str(sheet.get("name", f"Sheet_{idx+1}"))[:30].replace("/", "-")
        ws = wb.create_sheet(title=sname)
        ws.append([f"{title} — {sname}"])
        ws.append([f"Generated: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}"])
        ws.append([])

        cols: list[str] = sheet.get("columns", [])
        rows: list[dict[str, Any]] = sheet.get("rows", [])
        if cols:
            ws.append(cols)
            for c_idx in range(1, len(cols) + 1):
                cell = ws.cell(row=4, column=c_idx)
                cell.font = header_font
                cell.fill = header_fill
            for r in rows:
                ws.append([r.get(c, "") for c in cols])

    out = io.BytesIO()
    wb.save(out)
    return out.getvalue()


def export_to_pdf_bytes(
    title: str, description: str, sections: list[dict[str, Any]]
) -> bytes:
    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=landscape(A4), topMargin=30, bottomMargin=30)
    styles = getSampleStyleSheet()
    story = [
        Paragraph(f"<b>{title}</b>", styles["Title"]),
        Paragraph(description or "Enterprise Intelligence BI Report", styles["Normal"]),
        Paragraph(
            f"Exported: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}",
            styles["Italic"],
        ),
        Spacer(1, 16),
    ]

    for sec in sections:
        story.append(Paragraph(f"<b>{sec.get('name', 'Widget')}</b>", styles["Heading2"]))
        cols = sec.get("columns", [])[:8]
        rows = sec.get("rows", [])[:40]
        if not cols or not rows:
            story.append(Paragraph("No data rows.", styles["Normal"]))
            story.append(Spacer(1, 10))
            continue

        table_data = [cols] + [[str(r.get(c, ""))[:40] for c in cols] for r in rows]
        tbl = Table(table_data, repeatRows=1)
        tbl.setStyle(
            TableStyle(
                [
                    ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#1e293b")),
                    ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                    ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                    ("FONTSIZE", (0, 0), (-1, -1), 8),
                    ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#cbd5e1")),
                    ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f8fafc")]),
                ]
            )
        )
        story.append(tbl)
        story.append(Spacer(1, 14))

    doc.build(story)
    return buf.getvalue()
