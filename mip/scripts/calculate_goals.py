#!/usr/bin/env python3
"""
Ministry Intelligence Platform (MIP) – Goal Calculator
Computes actual values for all semester goals by querying aggregated data
from the ministry_db, then updates the goals table.
The `percentage` column is a GENERATED column, so it updates automatically.

Usage:
    python3 calculate_goals.py [--semester 2026-S1] [--dry-run]

Scheduling: run weekly via GitHub Actions or cron.
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from datetime import date, datetime
from typing import Any

import psycopg2
import psycopg2.extras
from dotenv import load_dotenv

load_dotenv("/app/.env", override=False)
load_dotenv(os.path.join(os.path.dirname(__file__), "../.env"), override=False)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s – %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
log = logging.getLogger("calculate_goals")

PG_HOST  = os.environ.get("POSTGRES_HOST", "db")
PG_USER  = os.environ.get("POSTGRES_USER", "ministry")
PG_PASS  = os.environ.get("POSTGRES_PASSWORD", "ministry")
PG_DB    = os.environ.get("POSTGRES_DB", "ministry_db")
SEMESTER = os.environ.get("CURRENT_SEMESTER", "2026-S1")


def get_db() -> psycopg2.extensions.connection:
    return psycopg2.connect(
        host=PG_HOST, user=PG_USER, password=PG_PASS, dbname=PG_DB,
        connect_timeout=10,
        cursor_factory=psycopg2.extras.RealDictCursor,
    )


def scalar(conn: psycopg2.extensions.connection, sql: str,
           params: tuple = (), default: float = 0.0) -> float:
    with conn.cursor() as cur:
        cur.execute(sql, params)
        row = cur.fetchone()
    if row:
        v = list(dict(row).values())[0]
        return float(v) if v is not None else default
    return default


# ── Metric calculators ────────────────────────────────────────────
# Each function takes a db connection and returns a float actual_value.
# The description string must match exactly the description in the goals table.

METRIC_CALCULATORS: dict[str, Any] = {}


def metric(description_pattern: str):
    """Decorator to register a metric calculator by goal description substring."""
    def decorator(func):
        METRIC_CALCULATORS[description_pattern.lower()] = func
        return func
    return decorator


@metric("average weekly service attendance")
def avg_weekly_service_attendance(conn, semester: str) -> float:
    return scalar(conn, """
        SELECT AVG(weekly_count) FROM (
            SELECT date_trunc('week', a.date) AS wk,
                   COUNT(att.id) FILTER (WHERE att.present) AS weekly_count
            FROM activities a
            LEFT JOIN attendance att ON a.activity_code = att.activity_code
            WHERE a.activity_type = 'Service'
            GROUP BY wk
            HAVING COUNT(a.activity_code) > 0
        ) sub
    """)


@metric("total outreach events")
def total_outreach_events(conn, semester: str) -> float:
    return scalar(conn, """
        SELECT COUNT(DISTINCT o.id)
        FROM outreaches o
        JOIN activities a ON a.activity_code = o.activity_code
        WHERE EXTRACT(year FROM a.date) = EXTRACT(year FROM CURRENT_DATE)
    """)


@metric("total people reached (outreach)")
def total_people_reached(conn, semester: str) -> float:
    return scalar(conn, """
        SELECT COALESCE(SUM(o.people_reached), 0)
        FROM outreaches o
        JOIN activities a ON a.activity_code = o.activity_code
        WHERE EXTRACT(year FROM a.date) = EXTRACT(year FROM CURRENT_DATE)
    """)


@metric("total salvations recorded")
def total_salvations(conn, semester: str) -> float:
    return scalar(conn, """
        SELECT COALESCE(SUM(o.salvations), 0)
        FROM outreaches o
        JOIN activities a ON a.activity_code = o.activity_code
        WHERE EXTRACT(year FROM a.date) = EXTRACT(year FROM CURRENT_DATE)
    """)


@metric("follow-up completion rate")
def followup_completion_rate(conn, semester: str) -> float:
    return scalar(conn, """
        SELECT ROUND(
            COUNT(*) FILTER (WHERE status = 'Completed')::numeric /
            NULLIF(COUNT(*), 0) * 100, 2
        )
        FROM follow_ups
        WHERE created_at >= DATE_TRUNC('year', CURRENT_DATE)
    """)


@metric("total general offering")
def total_general_offering(conn, semester: str) -> float:
    return scalar(conn, """
        SELECT COALESCE(SUM(amount), 0)
        FROM finance
        WHERE type = 'Income'
          AND category ILIKE 'Offering%'
          AND EXTRACT(year FROM date) = EXTRACT(year FROM CURRENT_DATE)
    """)


@metric("hospital outreach visits")
def hospital_visits(conn, semester: str) -> float:
    return scalar(conn, """
        SELECT COUNT(DISTINCT a.activity_code)
        FROM activities a
        WHERE a.activity_type = 'Hospital_Outreach'
          AND EXTRACT(year FROM a.date) = EXTRACT(year FROM CURRENT_DATE)
    """)


@metric("prison ministry visits")
def prison_visits(conn, semester: str) -> float:
    return scalar(conn, """
        SELECT COUNT(DISTINCT a.activity_code)
        FROM activities a
        JOIN outreaches o ON o.activity_code = a.activity_code
        JOIN locations l ON l.location_name = o.location
        WHERE l.area_type = 'prison'
          AND EXTRACT(year FROM a.date) = EXTRACT(year FROM CURRENT_DATE)
    """)


@metric("new members joined")
def new_members(conn, semester: str) -> float:
    return scalar(conn, """
        SELECT COUNT(*)
        FROM members
        WHERE EXTRACT(year FROM join_date) = EXTRACT(year FROM CURRENT_DATE)
          AND status IN ('Active', 'Inactive')
    """)


@metric("ctf conference attendance target")
def ctf_attendance(conn, semester: str) -> float:
    return scalar(conn, """
        SELECT COALESCE(SUM(att.present::int), 0)
        FROM attendance att
        JOIN activities a ON a.activity_code = att.activity_code
        WHERE a.activity_type = 'Conference'
          AND EXTRACT(year FROM a.date) = EXTRACT(year FROM CURRENT_DATE)
        LIMIT 1
    """)


def find_calculator(description: str):
    """Find the best matching calculator for a goal description."""
    desc_lower = description.lower()
    for pattern, func in METRIC_CALCULATORS.items():
        if pattern in desc_lower:
            return func
    return None


def update_goals(conn: psycopg2.extensions.connection,
                 semester: str, dry_run: bool = False) -> list[dict]:
    """
    Fetch all goals for the semester, compute actual values, and update DB.
    Returns a list of update summaries.
    """
    with conn.cursor() as cur:
        cur.execute(
            "SELECT id, description, target_value, actual_value FROM goals WHERE semester = %s",
            (semester,),
        )
        goals = [dict(r) for r in cur.fetchall()]

    if not goals:
        log.warning("No goals found for semester: %s", semester)
        return []

    log.info("Processing %d goals for semester %s", len(goals), semester)
    updates = []

    for goal in goals:
        gid = goal["id"]
        desc = goal["description"]
        old_val = float(goal["actual_value"] or 0)

        calc = find_calculator(desc)
        if not calc:
            log.warning("No calculator found for goal: '%s' (id=%d)", desc, gid)
            updates.append({
                "id": gid, "description": desc,
                "old_actual": old_val, "new_actual": old_val,
                "status": "NO_CALCULATOR",
            })
            continue

        try:
            new_val = calc(conn, semester)
        except Exception as exc:
            log.error("Calculator error for goal '%s': %s", desc, exc)
            updates.append({
                "id": gid, "description": desc,
                "old_actual": old_val, "new_actual": old_val,
                "status": f"ERROR: {exc}",
            })
            continue

        new_val = round(float(new_val), 2)
        diff = new_val - old_val

        if not dry_run:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE goals SET actual_value = %s, updated_at = NOW() WHERE id = %s",
                    (new_val, gid),
                )

        updates.append({
            "id": gid,
            "description": desc,
            "old_actual": old_val,
            "new_actual": new_val,
            "diff": diff,
            "status": "DRY_RUN" if dry_run else "UPDATED",
        })

        log.info(
            "Goal [%d] '%s': %.2f → %.2f (Δ%.2f) [%s]",
            gid, desc[:50], old_val, new_val, diff,
            "DRY_RUN" if dry_run else "UPDATED",
        )

    if not dry_run:
        conn.commit()

    return updates


def print_summary(updates: list[dict], semester: str) -> None:
    print("\n" + "=" * 70)
    print(f"GOAL CALCULATION SUMMARY – Semester: {semester}")
    print(f"Run time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 70)
    for u in updates:
        icon = "✅" if u["status"] == "UPDATED" else ("🔄" if u["status"] == "DRY_RUN" else "⚠️")
        print(f"  {icon} [{u['id']}] {u['description'][:50]}")
        print(f"      Old: {u['old_actual']:.2f}  New: {u['new_actual']:.2f}  "
              f"Δ: {u.get('diff', 0):+.2f}  [{u['status']}]")
    updated = sum(1 for u in updates if u["status"] in ("UPDATED", "DRY_RUN"))
    skipped = len(updates) - updated
    print("=" * 70)
    print(f"Updated: {updated}  |  Skipped/Error: {skipped}")
    print("=" * 70)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="MIP Goal Calculator")
    parser.add_argument("--semester", default=SEMESTER,
                        help=f"Target semester (default: {SEMESTER})")
    parser.add_argument("--dry-run", action="store_true",
                        help="Compute values without writing to database")
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()
    conn = get_db()
    try:
        updates = update_goals(conn, args.semester, dry_run=args.dry_run)
        print_summary(updates, args.semester)
    finally:
        conn.close()
