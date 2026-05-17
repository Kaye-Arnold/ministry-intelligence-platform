#!/usr/bin/env python3
"""
Ministry Intelligence Platform (MIP) – Integrity Check Script
Runs data-quality validations against the ministry_db PostgreSQL database.
Reports anomalies to stdout and exits with code 1 if issues found.
Used by GitHub Actions weekly integrity-check workflow.

Usage:
    python3 integrity_check.py [--verbose] [--fix-safe]

Exit codes:
    0 – all checks passed
    1 – anomalies detected (CI will create a GitHub Issue)
    2 – database connection or runtime error
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

import psycopg2
import psycopg2.extras
from dotenv import load_dotenv

load_dotenv("/app/.env", override=False)
load_dotenv(os.path.join(os.path.dirname(__file__), "../.env"), override=False)

# ── Logging ──────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
log = logging.getLogger("integrity_check")

# ── Config ────────────────────────────────────────────────────────
PG_HOST = os.environ.get("POSTGRES_HOST", "db")
PG_USER = os.environ.get("POSTGRES_USER", "ministry")
PG_PASS = os.environ.get("POSTGRES_PASSWORD", "ministry")
PG_DB   = os.environ.get("POSTGRES_DB", "ministry_db")


@dataclass
class CheckResult:
    name: str
    passed: bool
    severity: str          # "ERROR", "WARNING", "INFO"
    message: str
    row_count: int = 0
    details: list[dict] = field(default_factory=list)


def get_db() -> psycopg2.extensions.connection:
    return psycopg2.connect(
        host=PG_HOST, user=PG_USER,
        password=PG_PASS, dbname=PG_DB,
        connect_timeout=10,
        cursor_factory=psycopg2.extras.RealDictCursor,
    )


def query(conn: psycopg2.extensions.connection, sql: str, params: tuple = ()) -> list[dict]:
    with conn.cursor() as cur:
        cur.execute(sql, params)
        return [dict(r) for r in cur.fetchall()]


def scalar(conn: psycopg2.extensions.connection, sql: str,
           params: tuple = (), default: Any = 0) -> Any:
    rows = query(conn, sql, params)
    if rows:
        v = list(rows[0].values())[0]
        return v if v is not None else default
    return default


def run_all_checks(conn: psycopg2.extensions.connection, verbose: bool = False) -> list[CheckResult]:
    results: list[CheckResult] = []

    # ── CHECK 1: Orphan attendance records ────────────────────────
    orphan_att = query(conn, """
        SELECT att.id, att.activity_code, att.member_id
        FROM attendance att
        WHERE NOT EXISTS (SELECT 1 FROM activities a WHERE a.activity_code = att.activity_code)
           OR NOT EXISTS (SELECT 1 FROM members m WHERE m.member_id = att.member_id)
        LIMIT 50
    """)
    results.append(CheckResult(
        name="Orphan Attendance Records",
        passed=len(orphan_att) == 0,
        severity="ERROR",
        message=(
            "All attendance records have valid foreign keys."
            if not orphan_att
            else f"{len(orphan_att)} attendance record(s) reference non-existent activities or members."
        ),
        row_count=len(orphan_att),
        details=orphan_att[:10] if verbose else [],
    ))

    # ── CHECK 2: Duplicate attendance entries ─────────────────────
    dupes = query(conn, """
        SELECT activity_code, member_id, COUNT(*) AS cnt
        FROM attendance
        GROUP BY activity_code, member_id
        HAVING COUNT(*) > 1
        LIMIT 20
    """)
    results.append(CheckResult(
        name="Duplicate Attendance Entries",
        passed=len(dupes) == 0,
        severity="ERROR",
        message=(
            "No duplicate attendance entries found."
            if not dupes
            else f"{len(dupes)} duplicate attendance combination(s) detected."
        ),
        row_count=len(dupes),
        details=dupes[:10] if verbose else [],
    ))

    # ── CHECK 3: Outreaches with NULL GPS coordinates ─────────────
    null_gps = query(conn, """
        SELECT o.id, o.activity_code, o.location
        FROM outreaches o
        WHERE o.latitude IS NULL OR o.longitude IS NULL
        LIMIT 20
    """)
    results.append(CheckResult(
        name="Outreach Records Missing GPS",
        passed=len(null_gps) == 0,
        severity="WARNING",
        message=(
            "All outreach records have GPS coordinates."
            if not null_gps
            else f"{len(null_gps)} outreach record(s) missing latitude/longitude."
        ),
        row_count=len(null_gps),
        details=null_gps[:10] if verbose else [],
    ))

    # ── CHECK 4: Negative or zero donation amounts ────────────────
    bad_amounts = query(conn, """
        SELECT donation_id, amount, status FROM donations WHERE amount <= 0 LIMIT 20
    """)
    results.append(CheckResult(
        name="Invalid Donation Amounts",
        passed=len(bad_amounts) == 0,
        severity="ERROR",
        message=(
            "All donation amounts are positive."
            if not bad_amounts
            else f"{len(bad_amounts)} donation(s) have zero or negative amounts."
        ),
        row_count=len(bad_amounts),
        details=bad_amounts[:10] if verbose else [],
    ))

    # ── CHECK 5: Stale pending donations (>24 hours) ──────────────
    stale = query(conn, """
        SELECT donation_id, amount, payment_method, initiated_at
        FROM donations
        WHERE status = 'pending'
          AND initiated_at < NOW() - INTERVAL '24 hours'
          AND payment_method IN ('MTN_MoMo', 'Airtel_Money')
        LIMIT 20
    """)
    results.append(CheckResult(
        name="Stale Pending Donations (>24h)",
        passed=len(stale) == 0,
        severity="WARNING",
        message=(
            "No stale pending donations."
            if not stale
            else f"{len(stale)} donation(s) have been pending for >24 hours without confirmation."
        ),
        row_count=len(stale),
        details=stale[:10] if verbose else [],
    ))

    # ── CHECK 6: Orphan donations (member_id not in members) ──────
    orphan_don = query(conn, """
        SELECT donation_id, member_id
        FROM donations
        WHERE member_id IS NOT NULL
          AND NOT EXISTS (SELECT 1 FROM members m WHERE m.member_id = donations.member_id)
        LIMIT 20
    """)
    results.append(CheckResult(
        name="Orphan Donation Records",
        passed=len(orphan_don) == 0,
        severity="ERROR",
        message=(
            "All donations reference valid members."
            if not orphan_don
            else f"{len(orphan_don)} donation(s) reference non-existent member_ids."
        ),
        row_count=len(orphan_don),
        details=orphan_don[:10] if verbose else [],
    ))

    # ── CHECK 7: Orphan outreaches (activity not in activities) ───
    orphan_out = query(conn, """
        SELECT o.id, o.activity_code
        FROM outreaches o
        WHERE o.activity_code IS NOT NULL
          AND NOT EXISTS (SELECT 1 FROM activities a WHERE a.activity_code = o.activity_code)
        LIMIT 20
    """)
    results.append(CheckResult(
        name="Orphan Outreach Records",
        passed=len(orphan_out) == 0,
        severity="ERROR",
        message=(
            "All outreach records reference valid activities."
            if not orphan_out
            else f"{len(orphan_out)} outreach record(s) reference non-existent activities."
        ),
        row_count=len(orphan_out),
        details=orphan_out[:10] if verbose else [],
    ))

    # ── CHECK 8: Follow-ups overdue (>7 days without contact) ─────
    overdue = scalar(conn, """
        SELECT COUNT(*) FROM follow_ups
        WHERE status = 'Pending'
          AND contact_date < CURRENT_DATE - INTERVAL '7 days'
    """)
    results.append(CheckResult(
        name="Overdue Follow-Ups",
        passed=int(overdue) == 0,
        severity="WARNING",
        message=(
            "No overdue follow-ups."
            if int(overdue) == 0
            else f"{overdue} follow-up(s) are overdue (pending >7 days since last contact)."
        ),
        row_count=int(overdue),
    ))

    # ── CHECK 9: Members with invalid member_id format ────────────
    bad_ids = query(conn, """
        SELECT member_id, full_name FROM members
        WHERE member_id !~ '^MEM-[0-9]{4}$'
        LIMIT 20
    """)
    results.append(CheckResult(
        name="Invalid Member ID Format",
        passed=len(bad_ids) == 0,
        severity="ERROR",
        message=(
            "All member IDs conform to MEM-XXXX format."
            if not bad_ids
            else f"{len(bad_ids)} member(s) have non-conforming IDs."
        ),
        row_count=len(bad_ids),
        details=bad_ids[:10] if verbose else [],
    ))

    # ── CHECK 10: Activities with invalid code format ──────────────
    bad_codes = query(conn, """
        SELECT activity_code FROM activities
        WHERE activity_code !~ '^[A-Z]{2,4}-[A-Z]{2,5}-[0-9]{3}$'
        LIMIT 20
    """)
    results.append(CheckResult(
        name="Invalid Activity Code Format",
        passed=len(bad_codes) == 0,
        severity="ERROR",
        message=(
            "All activity codes conform to format."
            if not bad_codes
            else f"{len(bad_codes)} activity code(s) don't match expected format."
        ),
        row_count=len(bad_codes),
        details=bad_codes[:10] if verbose else [],
    ))

    # ── CHECK 11: Goals without actual_value updates (if semester started > 1 week ago) ──
    stale_goals = query(conn, """
        SELECT id, description, semester
        FROM goals
        WHERE actual_value = 0
          AND updated_at < NOW() - INTERVAL '7 days'
        LIMIT 10
    """)
    results.append(CheckResult(
        name="Goals With No Progress Updates",
        passed=len(stale_goals) == 0,
        severity="WARNING",
        message=(
            "All goals have been updated."
            if not stale_goals
            else f"{len(stale_goals)} goal(s) have not been updated in >7 days."
        ),
        row_count=len(stale_goals),
        details=stale_goals[:5] if verbose else [],
    ))

    # ── CHECK 12: Attendance recorded after activity date ─────────
    future_att = query(conn, """
        SELECT att.id, att.activity_code, att.recorded_at, a.date
        FROM attendance att
        JOIN activities a ON a.activity_code = att.activity_code
        WHERE att.recorded_at::date < a.date
        LIMIT 10
    """)
    results.append(CheckResult(
        name="Attendance Recorded Before Activity Date",
        passed=len(future_att) == 0,
        severity="WARNING",
        message=(
            "No attendance timestamps precede activity dates."
            if not future_att
            else f"{len(future_att)} attendance record(s) were recorded before the activity date."
        ),
        row_count=len(future_att),
        details=future_att[:5] if verbose else [],
    ))

    # ── CHECK 13: Finance records with no category ────────────────
    no_cat_finance = scalar(conn, """
        SELECT COUNT(*) FROM finance WHERE category IS NULL OR category = ''
    """)
    results.append(CheckResult(
        name="Finance Records Without Category",
        passed=int(no_cat_finance) == 0,
        severity="ERROR",
        message=(
            "All finance records have categories."
            if int(no_cat_finance) == 0
            else f"{no_cat_finance} finance record(s) missing category."
        ),
        row_count=int(no_cat_finance),
    ))

    # ── CHECK 14: Table row counts (basic sanity) ─────────────────
    table_counts = query(conn, """
        SELECT relname AS table_name, n_live_tup AS row_count
        FROM pg_stat_user_tables
        WHERE schemaname = 'public'
        ORDER BY relname
    """)
    total_tables = len(table_counts)
    results.append(CheckResult(
        name="Database Table Availability",
        passed=total_tables >= 14,
        severity="ERROR",
        message=(
            f"All {total_tables} tables present and accessible."
            if total_tables >= 14
            else f"Only {total_tables} tables found; expected at least 14."
        ),
        row_count=total_tables,
        details=table_counts if verbose else [],
    ))

    # ── CHECK 15: Donation giving_category referential integrity ──
    orphan_cat = query(conn, """
        SELECT donation_id, giving_category
        FROM donations
        WHERE giving_category IS NOT NULL
          AND NOT EXISTS (
              SELECT 1 FROM giving_categories gc
              WHERE gc.category_code = donations.giving_category
          )
        LIMIT 20
    """)
    results.append(CheckResult(
        name="Donations Referencing Unknown Categories",
        passed=len(orphan_cat) == 0,
        severity="ERROR",
        message=(
            "All donation categories are valid."
            if not orphan_cat
            else f"{len(orphan_cat)} donation(s) reference non-existent giving categories."
        ),
        row_count=len(orphan_cat),
        details=orphan_cat[:10] if verbose else [],
    ))

    return results


def format_report(results: list[CheckResult], verbose: bool = False) -> str:
    lines = [
        "=" * 70,
        "MINISTRY INTELLIGENCE PLATFORM – DATA INTEGRITY REPORT",
        f"Run time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        "=" * 70,
        "",
    ]

    errors   = [r for r in results if not r.passed and r.severity == "ERROR"]
    warnings = [r for r in results if not r.passed and r.severity == "WARNING"]
    passed   = [r for r in results if r.passed]

    lines += [f"✅ PASSED:   {len(passed)}/{len(results)}"]
    lines += [f"❌ ERRORS:   {len(errors)}"]
    lines += [f"⚠️  WARNINGS: {len(warnings)}"]
    lines += [""]

    if errors:
        lines += ["── ERRORS (must be fixed) " + "─" * 44]
        for r in errors:
            lines += [f"  ❌ [{r.name}]", f"     {r.message}"]
            if verbose and r.details:
                for d in r.details[:3]:
                    lines += [f"     Sample: {d}"]
        lines += [""]

    if warnings:
        lines += ["── WARNINGS (should be reviewed) " + "─" * 37]
        for r in warnings:
            lines += [f"  ⚠️  [{r.name}]", f"     {r.message}"]
            if verbose and r.details:
                for d in r.details[:3]:
                    lines += [f"     Sample: {d}"]
        lines += [""]

    if passed:
        lines += ["── PASSED " + "─" * 60]
        for r in passed:
            lines += [f"  ✅ {r.name}: {r.message}"]
        lines += [""]

    lines += ["=" * 70]
    if errors or warnings:
        lines += [f"ACTION REQUIRED: {len(errors)} error(s) and {len(warnings)} warning(s) detected."]
        lines += ["Please review and resolve before the next service."]
    else:
        lines += ["ALL CHECKS PASSED. Data integrity is healthy."]
    lines += ["=" * 70]

    return "\n".join(lines)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="MIP Data Integrity Check")
    parser.add_argument("--verbose", action="store_true",
                        help="Show sample rows for failed checks")
    parser.add_argument("--fix-safe", action="store_true",
                        help="Automatically fix safe issues (e.g., mark stale donations as failed)")
    return parser.parse_args()


def apply_safe_fixes(conn: psycopg2.extensions.connection) -> int:
    """Apply automatically safe fixes; returns count of fixes applied."""
    fixes = 0
    # Mark donations pending >48h as failed (telecom timeout)
    with conn.cursor() as cur:
        cur.execute("""
            UPDATE donations
            SET status = 'failed',
                transaction_ref = COALESCE(transaction_ref, 'TIMEOUT-AUTO')
            WHERE status = 'pending'
              AND initiated_at < NOW() - INTERVAL '48 hours'
              AND payment_method IN ('MTN_MoMo', 'Airtel_Money')
        """)
        fixes += cur.rowcount
        log.info("Auto-fixed %d stale donations → 'failed'", cur.rowcount)
    conn.commit()
    return fixes


if __name__ == "__main__":
    args = parse_args()

    try:
        conn = get_db()
    except psycopg2.OperationalError as exc:
        log.critical("Cannot connect to database: %s", exc)
        sys.exit(2)

    try:
        if args.fix_safe:
            fixes = apply_safe_fixes(conn)
            log.info("Applied %d safe auto-fixes.", fixes)

        results = run_all_checks(conn, verbose=args.verbose)
        report = format_report(results, verbose=args.verbose)
        print(report)

        errors = [r for r in results if not r.passed and r.severity == "ERROR"]
        warnings = [r for r in results if not r.passed and r.severity == "WARNING"]

        sys.exit(1 if (errors or warnings) else 0)

    except Exception as exc:
        log.exception("Unexpected error during integrity check: %s", exc)
        sys.exit(2)
    finally:
        conn.close()
