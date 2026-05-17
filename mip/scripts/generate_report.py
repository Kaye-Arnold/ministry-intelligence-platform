#!/usr/bin/env python3
"""
Ministry Intelligence Platform (MIP) – Report Generator
Generates weekly, monthly, and semester PDF reports by querying
PostgreSQL directly and composing a ReportLab PDF with charts and tables.

Design: queries DB directly (not Superset screenshot API) for reliability;
Superset headless-screenshot approach is used as an optional enhancement.

Usage:
    python3 generate_report.py --type weekly  --output /app/reports/weekly_2026-01-01.pdf
    python3 generate_report.py --type monthly --output /app/reports/monthly_2026-01.pdf
    python3 generate_report.py --type semester --output /app/reports/semester_2026-S1.pdf
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from datetime import date, datetime, timedelta
from io import BytesIO
from typing import Any

import psycopg2
import psycopg2.extras
from dotenv import load_dotenv
from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_LEFT, TA_RIGHT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import cm, mm
from reportlab.platypus import (
    HRFlowable,
    Image,
    PageBreak,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)
from reportlab.graphics.shapes import Drawing
from reportlab.graphics.charts.barcharts import VerticalBarChart
from reportlab.graphics.charts.piecharts import Pie
from reportlab.graphics.charts.lineplots import LinePlot
from reportlab.graphics import renderPDF

load_dotenv("/app/.env", override=False)
load_dotenv(os.path.join(os.path.dirname(__file__), "../.env"), override=False)

# ── Logging ──────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s – %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
log = logging.getLogger("generate_report")

# ── DB Config ─────────────────────────────────────────────────────
PG_HOST = os.environ.get("POSTGRES_HOST", "db")
PG_USER = os.environ.get("POSTGRES_USER", "ministry")
PG_PASS = os.environ.get("POSTGRES_PASSWORD", "ministry")
PG_DB   = os.environ.get("POSTGRES_DB", "ministry_db")
DOMAIN  = os.environ.get("DOMAIN", "ministry.example.com")
SEMESTER = os.environ.get("CURRENT_SEMESTER", "2026-S1")

# ── Colour palette ────────────────────────────────────────────────
BRAND_BLUE   = colors.HexColor("#003366")
BRAND_GOLD   = colors.HexColor("#FFB300")
LIGHT_GRAY   = colors.HexColor("#F5F5F5")
MED_GRAY     = colors.HexColor("#CCCCCC")
DARK_GRAY    = colors.HexColor("#555555")
SUCCESS_GREEN = colors.HexColor("#2E7D32")
WARN_ORANGE  = colors.HexColor("#E65100")
DANGER_RED   = colors.HexColor("#C62828")


def get_db() -> psycopg2.extensions.connection:
    return psycopg2.connect(
        host=PG_HOST, user=PG_USER,
        password=PG_PASS, dbname=PG_DB,
        connect_timeout=10,
        cursor_factory=psycopg2.extras.RealDictCursor,
    )


def query(sql: str, params: tuple = ()) -> list[dict]:
    conn = get_db()
    try:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            return [dict(r) for r in cur.fetchall()]
    finally:
        conn.close()


def scalar(sql: str, params: tuple = (), default: Any = 0) -> Any:
    rows = query(sql, params)
    if rows:
        v = list(rows[0].values())[0]
        return v if v is not None else default
    return default


# ── Style helpers ─────────────────────────────────────────────────

def make_styles() -> dict:
    base = getSampleStyleSheet()
    return {
        "title": ParagraphStyle(
            "MIPTitle", parent=base["Title"],
            fontSize=22, textColor=BRAND_BLUE,
            spaceAfter=4, alignment=TA_CENTER, fontName="Helvetica-Bold",
        ),
        "subtitle": ParagraphStyle(
            "MIPSubtitle", parent=base["Normal"],
            fontSize=11, textColor=DARK_GRAY,
            spaceAfter=8, alignment=TA_CENTER,
        ),
        "section": ParagraphStyle(
            "MIPSection", parent=base["Heading2"],
            fontSize=13, textColor=BRAND_BLUE,
            spaceBefore=12, spaceAfter=4, fontName="Helvetica-Bold",
            borderPad=4,
        ),
        "body": ParagraphStyle(
            "MIPBody", parent=base["Normal"],
            fontSize=9, textColor=DARK_GRAY, spaceAfter=4,
        ),
        "kpi_label": ParagraphStyle(
            "KPILabel", parent=base["Normal"],
            fontSize=8, textColor=DARK_GRAY, alignment=TA_CENTER,
        ),
        "kpi_value": ParagraphStyle(
            "KPIValue", parent=base["Normal"],
            fontSize=22, textColor=BRAND_BLUE, alignment=TA_CENTER,
            fontName="Helvetica-Bold",
        ),
        "footer": ParagraphStyle(
            "Footer", parent=base["Normal"],
            fontSize=7, textColor=MED_GRAY, alignment=TA_CENTER,
        ),
        "th": ParagraphStyle(
            "TH", parent=base["Normal"],
            fontSize=8, textColor=colors.white, fontName="Helvetica-Bold",
            alignment=TA_LEFT,
        ),
        "td": ParagraphStyle(
            "TD", parent=base["Normal"],
            fontSize=8, textColor=DARK_GRAY, alignment=TA_LEFT,
        ),
    }


def kpi_table(kpis: list[tuple[str, str, str]]) -> Table:
    """
    Render a row of KPI boxes.
    kpis: [(label, value, sub), ...]
    """
    S = make_styles()
    cells = []
    for label, value, sub in kpis:
        cell_content = [
            Paragraph(value, S["kpi_value"]),
            Paragraph(label, S["kpi_label"]),
        ]
        if sub:
            sub_style = ParagraphStyle(
                "KPISub", parent=S["kpi_label"],
                textColor=SUCCESS_GREEN if "▲" in sub else WARN_ORANGE,
                fontSize=8,
            )
            cell_content.append(Paragraph(sub, sub_style))
        cells.append(cell_content)

    t = Table([cells], colWidths=[A4[0] / len(kpis) - 24 * mm / len(kpis)] * len(kpis))
    t.setStyle(TableStyle([
        ("BACKGROUND",   (0, 0), (-1, -1), LIGHT_GRAY),
        ("BOX",          (0, 0), (-1, -1), 0.5, MED_GRAY),
        ("INNERGRID",    (0, 0), (-1, -1), 0.5, MED_GRAY),
        ("VALIGN",       (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING",   (0, 0), (-1, -1), 8),
        ("BOTTOMPADDING",(0, 0), (-1, -1), 8),
        ("LEFTPADDING",  (0, 0), (-1, -1), 6),
        ("RIGHTPADDING", (0, 0), (-1, -1), 6),
    ]))
    return t


def data_table(headers: list[str], rows: list[list], col_widths: list[float] | None = None) -> Table:
    S = make_styles()
    header_row = [Paragraph(h, S["th"]) for h in headers]
    data_rows = []
    for i, row in enumerate(rows):
        bg = LIGHT_GRAY if i % 2 == 0 else colors.white
        data_rows.append([Paragraph(str(c) if c is not None else "–", S["td"]) for c in row])

    all_rows = [header_row] + data_rows
    page_width = A4[0] - 2 * cm
    if col_widths is None:
        col_widths = [page_width / len(headers)] * len(headers)

    t = Table(all_rows, colWidths=col_widths, repeatRows=1)
    style = [
        ("BACKGROUND",    (0, 0), (-1, 0),  BRAND_BLUE),
        ("TEXTCOLOR",     (0, 0), (-1, 0),  colors.white),
        ("FONTNAME",      (0, 0), (-1, 0),  "Helvetica-Bold"),
        ("FONTSIZE",      (0, 0), (-1, -1), 8),
        ("GRID",          (0, 0), (-1, -1), 0.3, MED_GRAY),
        ("VALIGN",        (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING",    (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ("LEFTPADDING",   (0, 0), (-1, -1), 5),
        ("RIGHTPADDING",  (0, 0), (-1, -1), 5),
    ]
    for i in range(1, len(data_rows) + 1):
        if i % 2 == 0:
            style.append(("BACKGROUND", (0, i), (-1, i), LIGHT_GRAY))
    t.setStyle(TableStyle(style))
    return t


def bar_chart(labels: list[str], values: list[float], width: float = 400, height: float = 160,
              color: colors.Color = BRAND_BLUE) -> Drawing:
    d = Drawing(width, height)
    if not values or all(v == 0 for v in values):
        return d
    bc = VerticalBarChart()
    bc.x = 40
    bc.y = 20
    bc.width = width - 60
    bc.height = height - 40
    bc.data = [values]
    bc.bars[0].fillColor = color
    bc.categoryAxis.categoryNames = [str(l)[:12] for l in labels]
    bc.categoryAxis.labels.angle = 30 if len(labels) > 5 else 0
    bc.categoryAxis.labels.fontSize = 7
    bc.valueAxis.labels.fontSize = 7
    bc.valueAxis.valueMin = 0
    bc.valueAxis.valueMax = max(values) * 1.2 if max(values) > 0 else 10
    d.add(bc)
    return d


def pie_chart(labels: list[str], values: list[float], width: float = 200, height: float = 160) -> Drawing:
    d = Drawing(width, height)
    if not values or sum(values) == 0:
        return d
    pie = Pie()
    pie.x = 20
    pie.y = 20
    pie.width = min(width - 80, height - 40)
    pie.height = pie.width
    pie.data = values
    pie.labels = [f"{l[:10]}\n{v:.0f}" for l, v in zip(labels, values)]
    pie.sideLabels = True
    pie_colors = [BRAND_BLUE, BRAND_GOLD, SUCCESS_GREEN, WARN_ORANGE, DANGER_RED,
                  colors.HexColor("#7B1FA2"), colors.HexColor("#0097A7"),
                  colors.HexColor("#558B2F"), colors.HexColor("#F57F17")]
    for i, slice_ in enumerate(pie.slices):
        slice_.fillColor = pie_colors[i % len(pie_colors)]
        slice_.strokeColor = colors.white
        slice_.strokeWidth = 1
    d.add(pie)
    return d


# ── Data fetchers ─────────────────────────────────────────────────

def get_date_range(report_type: str) -> tuple[date, date]:
    today = date.today()
    if report_type == "weekly":
        # Last full week (Mon–Sun)
        last_monday = today - timedelta(days=today.weekday() + 7)
        return last_monday, last_monday + timedelta(days=6)
    elif report_type == "monthly":
        first = today.replace(day=1)
        last_month_end = first - timedelta(days=1)
        return last_month_end.replace(day=1), last_month_end
    else:  # semester
        return date(2026, 1, 1), today


def fetch_attendance_summary(start: date, end: date) -> dict:
    rows = query(
        """
        SELECT
            a.activity_type,
            COUNT(DISTINCT a.activity_code) AS sessions,
            COALESCE(SUM(att.present::int), 0) AS total_present,
            COALESCE(AVG(att.present::int) * 100, 0) AS avg_rate
        FROM activities a
        LEFT JOIN attendance att ON a.activity_code = att.activity_code
        WHERE a.date BETWEEN %s AND %s
        GROUP BY a.activity_type
        ORDER BY total_present DESC
        """,
        (start, end),
    )
    total_present = scalar(
        "SELECT COALESCE(SUM(present::int),0) FROM attendance att "
        "JOIN activities a ON a.activity_code=att.activity_code "
        "WHERE a.date BETWEEN %s AND %s",
        (start, end),
    )
    active_members = scalar(
        "SELECT COUNT(*) FROM members WHERE status='Active'"
    )
    return {"rows": rows, "total_present": total_present, "active_members": active_members}


def fetch_weekly_trend(weeks: int = 8) -> list[dict]:
    return query(
        """
        SELECT
            date_trunc('week', a.date)::date AS week_start,
            COUNT(att.id) FILTER (WHERE att.present) AS present_count
        FROM activities a
        LEFT JOIN attendance att ON a.activity_code = att.activity_code
        WHERE a.date >= CURRENT_DATE - (%s * 7) * INTERVAL '1 day'
          AND a.activity_type = 'Service'
        GROUP BY 1
        ORDER BY 1
        """,
        (weeks,),
    )


def fetch_outreach_summary(start: date, end: date) -> dict:
    rows = query(
        """
        SELECT
            l.location_name, l.area_type,
            SUM(o.people_reached) AS people_reached,
            SUM(o.salvations) AS salvations,
            SUM(o.follow_up_contacts) AS follow_ups,
            COUNT(o.id) AS events
        FROM outreaches o
        JOIN activities a ON o.activity_code = a.activity_code
        LEFT JOIN locations l ON o.location = l.location_name
        WHERE a.date BETWEEN %s AND %s
        GROUP BY l.location_name, l.area_type
        ORDER BY people_reached DESC
        """,
        (start, end),
    )
    totals = query(
        """
        SELECT
            COALESCE(SUM(o.people_reached),0) AS total_reached,
            COALESCE(SUM(o.salvations),0) AS total_salvations,
            COALESCE(SUM(o.follow_up_contacts),0) AS total_follow_ups,
            COUNT(o.id) AS total_events
        FROM outreaches o
        JOIN activities a ON o.activity_code = a.activity_code
        WHERE a.date BETWEEN %s AND %s
        """,
        (start, end),
    )
    return {"rows": rows, "totals": totals[0] if totals else {}}


def fetch_follow_up_summary(start: date, end: date) -> dict:
    rows = query(
        """
        SELECT
            m.full_name AS leader,
            COUNT(*) FILTER (WHERE f.status = 'Pending')   AS pending,
            COUNT(*) FILTER (WHERE f.status = 'Completed') AS completed,
            COUNT(*) AS total,
            ROUND(COUNT(*) FILTER (WHERE f.status='Completed')::numeric /
                  NULLIF(COUNT(*),0)*100, 0) AS pct
        FROM follow_ups f
        LEFT JOIN members m ON f.assigned_to = m.member_id
        WHERE f.created_at BETWEEN %s AND %s
        GROUP BY m.full_name
        ORDER BY completed DESC
        """,
        (start, end),
    )
    overdue = scalar(
        """
        SELECT COUNT(*) FROM follow_ups
        WHERE status = 'Pending'
          AND contact_date < CURRENT_DATE - INTERVAL '7 days'
        """
    )
    return {"rows": rows, "overdue": overdue}


def fetch_finance_summary(start: date, end: date) -> dict:
    rows = query(
        """
        SELECT category, type,
               COALESCE(SUM(amount),0) AS total
        FROM finance
        WHERE date BETWEEN %s AND %s
        GROUP BY category, type
        ORDER BY type, total DESC
        """,
        (start, end),
    )
    totals = query(
        """
        SELECT
            COALESCE(SUM(amount) FILTER (WHERE type='Income'),0)  AS income,
            COALESCE(SUM(amount) FILTER (WHERE type='Expense'),0) AS expense
        FROM finance
        WHERE date BETWEEN %s AND %s
        """,
        (start, end),
    )
    return {"rows": rows, "totals": totals[0] if totals else {}}


def fetch_donation_summary(start: date, end: date) -> dict:
    rows = query(
        """
        SELECT gc.category_name, d.payment_method,
               COUNT(*) AS count,
               COALESCE(SUM(d.amount) FILTER (WHERE d.status='completed'),0) AS confirmed
        FROM donations d
        JOIN giving_categories gc ON d.giving_category = gc.category_code
        WHERE d.date::date BETWEEN %s AND %s
        GROUP BY gc.category_name, d.payment_method
        ORDER BY confirmed DESC
        """,
        (start, end),
    )
    total_confirmed = scalar(
        "SELECT COALESCE(SUM(amount),0) FROM donations WHERE status='completed' AND date::date BETWEEN %s AND %s",
        (start, end),
    )
    return {"rows": rows, "total_confirmed": total_confirmed}


def fetch_goals(semester: str) -> list[dict]:
    return query(
        """
        SELECT description, target_value, actual_value, percentage, unit, category
        FROM goals WHERE semester = %s
        ORDER BY category, description
        """,
        (semester,),
    )


# ── Report builders ───────────────────────────────────────────────

def build_header_footer(canvas, doc, report_type: str, period_label: str) -> None:
    canvas.saveState()
    # Header bar
    canvas.setFillColor(BRAND_BLUE)
    canvas.rect(0, A4[1] - 2 * cm, A4[0], 2 * cm, fill=1, stroke=0)
    canvas.setFillColor(colors.white)
    canvas.setFont("Helvetica-Bold", 13)
    canvas.drawString(1.5 * cm, A4[1] - 1.35 * cm, "Ministry Intelligence Platform")
    canvas.setFont("Helvetica", 9)
    canvas.drawRightString(A4[0] - 1.5 * cm, A4[1] - 1.35 * cm, f"{report_type.upper()} REPORT | {period_label}")

    # Footer
    canvas.setFillColor(MED_GRAY)
    canvas.rect(0, 0, A4[0], 1 * cm, fill=1, stroke=0)
    canvas.setFillColor(DARK_GRAY)
    canvas.setFont("Helvetica", 7)
    canvas.drawString(1.5 * cm, 0.35 * cm,
                      f"MIP v1.0 | Generated {datetime.now().strftime('%d %b %Y %H:%M EAT')} | CONFIDENTIAL")
    canvas.drawRightString(A4[0] - 1.5 * cm, 0.35 * cm, f"Page {doc.page}")
    canvas.restoreState()


def generate_report(report_type: str, output_path: str) -> str:
    """
    Generate a PDF report and write it to output_path.
    Returns the output_path on success.
    """
    start, end = get_date_range(report_type)
    period_label = (
        f"{start.strftime('%d %b')} – {end.strftime('%d %b %Y')}" if report_type == "weekly"
        else f"{start.strftime('%b %Y')}" if report_type == "monthly"
        else f"Semester {SEMESTER}"
    )

    log.info("Generating %s report: %s → %s", report_type, start, end)

    # Fetch all data
    att = fetch_attendance_summary(start, end)
    trend = fetch_weekly_trend(8 if report_type == "weekly" else 16)
    out = fetch_outreach_summary(start, end)
    follow = fetch_follow_up_summary(start, end)
    fin = fetch_finance_summary(start, end)
    don = fetch_donation_summary(start, end)
    goals = fetch_goals(SEMESTER) if report_type == "semester" else []

    S = make_styles()
    story = []

    # ── Cover / Title ─────────────────────────────────────────────
    story.append(Spacer(1, 2 * cm))
    story.append(Paragraph(
        f"{'Weekly' if report_type=='weekly' else 'Monthly' if report_type=='monthly' else 'Semester'} "
        "Ministry Report",
        S["title"]
    ))
    story.append(Paragraph(period_label, S["subtitle"]))
    story.append(HRFlowable(width="100%", thickness=2, color=BRAND_GOLD, spaceAfter=8))

    # ── KPI Row ───────────────────────────────────────────────────
    fin_totals = fin["totals"] if fin["totals"] else {}
    out_totals = out["totals"] if out["totals"] else {}

    story.append(kpi_table([
        ("Active Members",    str(att["active_members"]),   ""),
        ("Total Present",     str(att["total_present"]),    ""),
        ("People Reached",    str(out_totals.get("total_reached", 0)), ""),
        ("Salvations",        str(out_totals.get("total_salvations", 0)), ""),
        ("Net Balance (UGX)", f"{float(fin_totals.get('income', 0)) - float(fin_totals.get('expense', 0)):,.0f}", ""),
    ]))
    story.append(Spacer(1, 0.4 * cm))

    # ── Attendance Section ────────────────────────────────────────
    story.append(Paragraph("📅 Attendance", S["section"]))

    if trend:
        weeks = [str(r["week_start"]) for r in trend]
        counts = [int(r["present_count"] or 0) for r in trend]
        story.append(bar_chart(weeks, counts, width=460, height=160, color=BRAND_BLUE))
        story.append(Spacer(1, 0.2 * cm))

    if att["rows"]:
        story.append(data_table(
            ["Activity Type", "Sessions", "Total Present", "Avg Rate (%)"],
            [[r["activity_type"], r["sessions"], r["total_present"],
              f"{float(r['avg_rate'] or 0):.1f}%"] for r in att["rows"]],
            col_widths=[6 * cm, 3 * cm, 4 * cm, 4 * cm],
        ))
    else:
        story.append(Paragraph("No attendance records for this period.", S["body"]))

    story.append(Spacer(1, 0.4 * cm))

    # ── Outreach Section ──────────────────────────────────────────
    story.append(Paragraph("🌍 Outreach & Evangelism", S["section"]))

    if out["rows"]:
        out_labels = [r["location_name"] or "Unknown" for r in out["rows"]]
        out_values = [int(r["people_reached"] or 0) for r in out["rows"]]
        story.append(bar_chart(out_labels, out_values, width=460, height=140, color=SUCCESS_GREEN))
        story.append(data_table(
            ["Location", "Area Type", "Events", "People Reached", "Salvations", "Follow-Ups"],
            [[r["location_name"], r["area_type"], r["events"],
              r["people_reached"], r["salvations"], r["follow_ups"]]
             for r in out["rows"]],
            col_widths=[4.5*cm, 3*cm, 2*cm, 3.5*cm, 2.5*cm, 3*cm],
        ))
    else:
        story.append(Paragraph("No outreach records for this period.", S["body"]))

    story.append(Spacer(1, 0.4 * cm))

    # ── Follow-Ups Section ────────────────────────────────────────
    story.append(Paragraph("🤝 Follow-Up Management", S["section"]))
    story.append(Paragraph(
        f"⚠️ Overdue follow-ups (>7 days): <b>{follow['overdue']}</b>", S["body"]
    ))
    if follow["rows"]:
        story.append(data_table(
            ["Leader", "Pending", "Completed", "Total", "Completion %"],
            [[r["leader"] or "Unassigned", r["pending"], r["completed"],
              r["total"], f"{r['pct'] or 0}%"]
             for r in follow["rows"]],
            col_widths=[5*cm, 3*cm, 3*cm, 2.5*cm, 4*cm],
        ))

    story.append(Spacer(1, 0.4 * cm))

    # ── Finance Section ───────────────────────────────────────────
    story.append(Paragraph("💰 Finance", S["section"]))
    income  = float(fin_totals.get("income", 0))
    expense = float(fin_totals.get("expense", 0))
    balance = income - expense

    story.append(kpi_table([
        ("Total Income (UGX)",  f"{income:,.0f}",  ""),
        ("Total Expense (UGX)", f"{expense:,.0f}", ""),
        ("Net Balance (UGX)",   f"{balance:,.0f}",
         "▲ Surplus" if balance >= 0 else "▼ Deficit"),
    ]))
    story.append(Spacer(1, 0.2 * cm))

    if fin["rows"]:
        # Pie chart for expense categories
        exp_rows = [r for r in fin["rows"] if r["type"] == "Expense"]
        if exp_rows:
            story.append(pie_chart(
                [r["category"] for r in exp_rows],
                [float(r["total"]) for r in exp_rows],
                width=300, height=180,
            ))
        story.append(data_table(
            ["Category", "Type", "Amount (UGX)"],
            [[r["category"], r["type"], f"{float(r['total']):,.0f}"] for r in fin["rows"]],
            col_widths=[7*cm, 3*cm, 7.5*cm],
        ))

    story.append(Spacer(1, 0.4 * cm))

    # ── Donations Section ─────────────────────────────────────────
    story.append(Paragraph("🙏 Donations & Giving", S["section"]))
    story.append(Paragraph(
        f"Total confirmed donations: <b>UGX {float(don['total_confirmed']):,.0f}</b>", S["body"]
    ))
    if don["rows"]:
        story.append(data_table(
            ["Category", "Payment Method", "Count", "Confirmed (UGX)"],
            [[r["category_name"], r["payment_method"], r["count"],
              f"{float(r['confirmed']):,.0f}"] for r in don["rows"]],
            col_widths=[5.5*cm, 4*cm, 2.5*cm, 5.5*cm],
        ))

    # ── Goals Section (semester only) ────────────────────────────
    if report_type == "semester" and goals:
        story.append(PageBreak())
        story.append(Paragraph(f"🎯 Semester Goals – {SEMESTER}", S["section"]))

        for g in goals:
            pct = float(g["percentage"] or 0)
            bar_color = SUCCESS_GREEN if pct >= 100 else (BRAND_GOLD if pct >= 60 else WARN_ORANGE)
            label = (
                f"<b>{g['description']}</b> | "
                f"Target: {float(g['target_value']):,.0f} {g['unit']} | "
                f"Actual: {float(g['actual_value']):,.0f} | "
                f"<font color='{'green' if pct>=100 else 'orange'}'>{pct:.1f}%</font>"
            )
            story.append(Paragraph(label, S["body"]))

        story.append(Spacer(1, 0.4 * cm))
        story.append(data_table(
            ["Goal", "Category", "Target", "Actual", "Unit", "%"],
            [[g["description"], g["category"],
              f"{float(g['target_value']):,.0f}",
              f"{float(g['actual_value']):,.0f}",
              g["unit"],
              f"{float(g['percentage'] or 0):.1f}%"]
             for g in goals],
            col_widths=[6*cm, 3*cm, 2.5*cm, 2.5*cm, 2.5*cm, 1.5*cm],
        ))

    # ── Build PDF ─────────────────────────────────────────────────
    os.makedirs(os.path.dirname(output_path), exist_ok=True)

    doc = SimpleDocTemplate(
        output_path,
        pagesize=A4,
        topMargin=2.5 * cm,
        bottomMargin=1.5 * cm,
        leftMargin=1.5 * cm,
        rightMargin=1.5 * cm,
        title=f"MIP {report_type.title()} Report – {period_label}",
        author="Ministry Intelligence Platform",
        subject=f"Ministry Report {period_label}",
    )

    doc.build(
        story,
        onFirstPage=lambda c, d: build_header_footer(c, d, report_type, period_label),
        onLaterPages=lambda c, d: build_header_footer(c, d, report_type, period_label),
    )

    size_kb = os.path.getsize(output_path) / 1024
    log.info("Report saved: %s (%.1f KB)", output_path, size_kb)
    return output_path


# ── CLI ───────────────────────────────────────────────────────────

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="MIP PDF Report Generator")
    parser.add_argument(
        "--type", choices=["weekly", "monthly", "semester"],
        default="weekly", help="Report type"
    )
    parser.add_argument(
        "--output", default=None,
        help="Output PDF path (default: auto-generated)"
    )
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()
    if not args.output:
        ts = datetime.now().strftime("%Y%m%d")
        args.output = f"/app/reports/{args.type}_report_{ts}.pdf"
    generate_report(args.type, args.output)
    print(f"Report generated: {args.output}")
