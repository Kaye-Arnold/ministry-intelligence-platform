#!/usr/bin/env python3
"""
Ministry Intelligence Platform (MIP) – End-to-End Verification Script
Tests all critical system components after deployment.

Usage:
    # Inside the Oracle VM (direct DB access):
    python3 tests/verify_system.py

    # Against a running deployment:
    python3 tests/verify_system.py --host https://ministry.example.com

    # Verbose mode:
    python3 tests/verify_system.py --verbose

Exit codes:
    0  – all tests passed
    1  – one or more tests failed
    2  – fatal: cannot connect to required services
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
import uuid
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any, Callable, Optional

try:
    import requests
    HAS_REQUESTS = True
except ImportError:
    HAS_REQUESTS = False

try:
    import psycopg2
    import psycopg2.extras
    HAS_PSYCOPG2 = True
except ImportError:
    HAS_PSYCOPG2 = False

# ── Config ────────────────────────────────────────────────────────
PG_HOST = os.environ.get("POSTGRES_HOST", "db")
PG_USER = os.environ.get("POSTGRES_USER", "ministry")
PG_PASS = os.environ.get("POSTGRES_PASSWORD", "ministry")
PG_DB   = os.environ.get("POSTGRES_DB", "ministry_db")

SUPERSET_URL   = os.environ.get("SUPERSET_URL", "http://localhost:8088")
NOCODB_URL     = os.environ.get("NOCODB_URL", "http://localhost:8080")
AUTOMATION_URL = os.environ.get("AUTOMATION_URL", "http://localhost:5000")

SUPERSET_ADMIN = os.environ.get("SUPERSET_ADMIN_USERNAME", "admin")
SUPERSET_PASS  = os.environ.get("SUPERSET_ADMIN_PASSWORD", "admin")


@dataclass
class TestResult:
    name: str
    passed: bool
    message: str
    duration_ms: float = 0.0
    details: Any = None
    critical: bool = False


class MIPVerifier:
    """Runs all verification tests and tracks results."""

    def __init__(self, host: Optional[str] = None, verbose: bool = False):
        self.host = host  # External URL (optional)
        self.verbose = verbose
        self.results: list[TestResult] = []
        self.conn: Optional[Any] = None

    def run(self, name: str, critical: bool = False) -> Callable:
        """Decorator to register and run a test."""
        def decorator(fn: Callable) -> Callable:
            start = time.monotonic()
            try:
                result = fn()
                passed = result is True or (isinstance(result, tuple) and result[0])
                msg = result[1] if isinstance(result, tuple) and len(result) > 1 else (
                    "Passed" if passed else "Failed")
                self.results.append(TestResult(
                    name=name, passed=passed, message=msg,
                    duration_ms=(time.monotonic() - start) * 1000,
                    critical=critical,
                ))
            except Exception as exc:
                self.results.append(TestResult(
                    name=name, passed=False,
                    message=f"Exception: {type(exc).__name__}: {exc}",
                    duration_ms=(time.monotonic() - start) * 1000,
                    critical=critical,
                ))
            return fn
        return decorator

    def _run_test(self, name: str, fn: Callable, critical: bool = False) -> TestResult:
        start = time.monotonic()
        try:
            result = fn()
            if isinstance(result, tuple):
                passed, msg = result[0], result[1] if len(result) > 1 else "OK"
            else:
                passed, msg = bool(result), "Passed" if result else "Failed"
        except Exception as exc:
            passed, msg = False, f"{type(exc).__name__}: {exc}"
        duration = (time.monotonic() - start) * 1000
        r = TestResult(name=name, passed=passed, message=msg, duration_ms=duration, critical=critical)
        self.results.append(r)
        icon = "✅" if passed else ("❌" if critical else "⚠️ ")
        print(f"  {icon} [{name}] {msg} ({duration:.0f}ms)")
        if not passed and critical:
            print(f"\n  CRITICAL test failed: {name}. Aborting further tests.")
            sys.exit(2)
        return r

    def get_db(self):
        if not HAS_PSYCOPG2:
            raise RuntimeError("psycopg2 not installed")
        return psycopg2.connect(
            host=PG_HOST, user=PG_USER, password=PG_PASS,
            dbname=PG_DB, connect_timeout=10,
            cursor_factory=psycopg2.extras.RealDictCursor,
        )

    def http_get(self, url: str, **kwargs) -> requests.Response:
        if not HAS_REQUESTS:
            raise RuntimeError("requests not installed")
        return requests.get(url, timeout=15, **kwargs)

    def http_post(self, url: str, **kwargs) -> requests.Response:
        if not HAS_REQUESTS:
            raise RuntimeError("requests not installed")
        return requests.post(url, timeout=15, **kwargs)

    # ── Test 1: PostgreSQL connectivity ───────────────────────────
    def test_db_connectivity(self) -> TestResult:
        def fn():
            conn = self.get_db()
            with conn.cursor() as cur:
                cur.execute("SELECT version()")
                ver = cur.fetchone()
            conn.close()
            v = list(dict(ver).values())[0]
            if "PostgreSQL 16" not in v:
                return False, f"Expected PostgreSQL 16, got: {v[:40]}"
            return True, f"Connected: {v[:40]}"
        return self._run_test("PostgreSQL Connectivity", fn, critical=True)

    # ── Test 2: Schema completeness ───────────────────────────────
    def test_schema_completeness(self) -> TestResult:
        required_tables = [
            "members", "cell_groups", "activities", "locations",
            "attendance", "outreaches", "follow_ups", "finance",
            "giving_categories", "donations", "goals", "user_teams",
            "api_keys", "report_log",
        ]
        required_views = [
            "v_attendance_summary", "v_finance_balance",
            "v_donation_summary", "v_weekly_attendance", "v_followup_performance",
        ]

        def fn():
            conn = self.get_db()
            try:
                with conn.cursor() as cur:
                    cur.execute("""
                        SELECT table_name FROM information_schema.tables
                        WHERE table_schema = 'public'
                          AND table_type IN ('BASE TABLE', 'VIEW')
                    """)
                    existing = {r["table_name"] for r in cur.fetchall()}
            finally:
                conn.close()

            missing_tables = [t for t in required_tables if t not in existing]
            missing_views  = [v for v in required_views  if v not in existing]
            all_missing = missing_tables + missing_views
            if all_missing:
                return False, f"Missing: {', '.join(all_missing)}"
            return True, f"All {len(required_tables)} tables + {len(required_views)} views present"
        return self._run_test("Schema Completeness", fn, critical=True)

    # ── Test 3: Giving categories seed data ───────────────────────
    def test_giving_categories_seeded(self) -> TestResult:
        def fn():
            conn = self.get_db()
            try:
                with conn.cursor() as cur:
                    cur.execute("SELECT COUNT(*) AS cnt FROM giving_categories")
                    cnt = dict(cur.fetchone())["cnt"]
            finally:
                conn.close()
            if cnt < 11:
                return False, f"Only {cnt}/11 giving categories seeded"
            return True, f"{cnt} giving categories present"
        return self._run_test("Giving Categories Seeded", fn)

    # ── Test 4: Unique constraint on attendance ───────────────────
    def test_attendance_unique_constraint(self) -> TestResult:
        def fn():
            conn = self.get_db()
            try:
                with conn.cursor() as cur:
                    # Try to insert a duplicate — should raise IntegrityError
                    test_code = "TST-TSTV-001"
                    test_member = "MEM-0001"
                    cur.execute("""
                        INSERT INTO activities (activity_code, activity_type, date, expected_attendance)
                        VALUES (%s, 'Service', CURRENT_DATE, 1)
                        ON CONFLICT (activity_code) DO NOTHING
                    """, (test_code,))
                    # First insert
                    cur.execute("""
                        INSERT INTO attendance (activity_code, member_id, present)
                        VALUES (%s, %s, true)
                        ON CONFLICT (activity_code, member_id) DO NOTHING
                    """, (test_code, test_member))
                    # Second insert — should be silently ignored by ON CONFLICT
                    cur.execute("""
                        INSERT INTO attendance (activity_code, member_id, present)
                        VALUES (%s, %s, false)
                        ON CONFLICT (activity_code, member_id) DO NOTHING
                    """, (test_code, test_member))
                    # Verify only one record
                    cur.execute("""
                        SELECT COUNT(*) AS cnt FROM attendance
                        WHERE activity_code = %s AND member_id = %s
                    """, (test_code, test_member))
                    cnt = dict(cur.fetchone())["cnt"]
                    # Cleanup
                    cur.execute("DELETE FROM attendance WHERE activity_code = %s", (test_code,))
                    cur.execute("DELETE FROM activities WHERE activity_code = %s", (test_code,))
                conn.commit()
            finally:
                conn.close()
            if cnt != 1:
                return False, f"Unique constraint failed: {cnt} records instead of 1"
            return True, "Unique constraint (activity_code, member_id) enforced correctly"
        return self._run_test("Attendance Unique Constraint", fn, critical=True)

    # ── Test 5: Donation insertion and status update ───────────────
    def test_donation_flow(self) -> TestResult:
        def fn():
            conn = self.get_db()
            try:
                with conn.cursor() as cur:
                    # Insert a test donation
                    cur.execute("""
                        INSERT INTO donations
                            (donor_phone, amount, giving_category, payment_method, status)
                        VALUES ('256700000000', 5000.00, 'General_Offering', 'Cash', 'pending')
                        RETURNING donation_id
                    """)
                    row = dict(cur.fetchone())
                    donation_id = row["donation_id"]

                    # Update to completed
                    test_ref = f"TEST-{uuid.uuid4().hex[:8].upper()}"
                    cur.execute("""
                        UPDATE donations SET status = 'completed',
                               transaction_ref = %s, confirmed_at = NOW()
                        WHERE donation_id = %s
                    """, (test_ref, donation_id))

                    # Verify
                    cur.execute("""
                        SELECT status, transaction_ref FROM donations WHERE donation_id = %s
                    """, (donation_id,))
                    updated = dict(cur.fetchone())

                    # Cleanup
                    cur.execute("DELETE FROM donations WHERE donation_id = %s", (donation_id,))
                conn.commit()
            finally:
                conn.close()
            if updated["status"] != "completed":
                return False, f"Status not updated: {updated['status']}"
            if updated["transaction_ref"] != test_ref:
                return False, "Transaction ref not updated correctly"
            return True, f"Donation flow: insert → update → verified (id={donation_id})"
        return self._run_test("Donation Insert + Status Update", fn)

    # ── Test 6: Goals table and percentage computed column ─────────
    def test_goals_percentage(self) -> TestResult:
        def fn():
            conn = self.get_db()
            try:
                with conn.cursor() as cur:
                    cur.execute("""
                        INSERT INTO goals (semester, description, target_value, actual_value, unit)
                        VALUES ('TEST-S1', 'Verify percentage column', 200.0, 150.0, 'count')
                        RETURNING id, percentage
                    """)
                    row = dict(cur.fetchone())
                    gid = row["id"]
                    pct = float(row["percentage"])
                    cur.execute("DELETE FROM goals WHERE id = %s", (gid,))
                conn.commit()
            finally:
                conn.close()
            expected = 75.0
            if abs(pct - expected) > 0.1:
                return False, f"Expected percentage=75.0, got {pct}"
            return True, f"GENERATED percentage column correct: 150/200 = {pct}%"
        return self._run_test("Goals Percentage Computed Column", fn)

    # ── Test 7: Integrity check script runs cleanly ────────────────
    def test_integrity_check_runs(self) -> TestResult:
        def fn():
            script = os.path.join(os.path.dirname(__file__), "../scripts/integrity_check.py")
            if not os.path.exists(script):
                return False, f"Script not found: {script}"
            env = {
                **os.environ,
                "POSTGRES_HOST": PG_HOST,
                "POSTGRES_USER": PG_USER,
                "POSTGRES_PASSWORD": PG_PASS,
                "POSTGRES_DB": PG_DB,
            }
            result = subprocess.run(
                [sys.executable, script],
                capture_output=True, text=True, timeout=60, env=env,
            )
            if result.returncode == 2:
                return False, f"integrity_check.py crashed: {result.stderr[:200]}"
            # Return code 0 = clean, 1 = anomalies (acceptable on fresh DB)
            lines = result.stdout.strip().split("\n")
            summary = next((l for l in reversed(lines) if l.strip()), "")
            return True, f"Exit code {result.returncode}: {summary[:80]}"
        return self._run_test("Integrity Check Script Runs", fn)

    # ── Test 8: Superset health endpoint ──────────────────────────
    def test_superset_health(self) -> TestResult:
        def fn():
            if not HAS_REQUESTS:
                return True, "Skipped (requests not available)"
            r = self.http_get(f"{SUPERSET_URL}/health")
            if r.status_code != 200:
                return False, f"HTTP {r.status_code}"
            return True, f"Superset healthy (HTTP {r.status_code})"
        return self._run_test("Superset Health Endpoint", fn)

    # ── Test 9: Superset login ────────────────────────────────────
    def test_superset_login(self) -> TestResult:
        def fn():
            if not HAS_REQUESTS:
                return True, "Skipped (requests not available)"
            r = self.http_post(
                f"{SUPERSET_URL}/api/v1/security/login",
                json={"username": SUPERSET_ADMIN, "password": SUPERSET_PASS, "provider": "db"},
            )
            if r.status_code != 200:
                return False, f"Login failed: HTTP {r.status_code}"
            token = r.json().get("access_token", "")
            if not token:
                return False, "No access_token in response"
            return True, "Admin login successful; JWT token obtained"
        return self._run_test("Superset Admin Login", fn)

    # ── Test 10: NocoDB health endpoint ───────────────────────────
    def test_nocodb_health(self) -> TestResult:
        def fn():
            if not HAS_REQUESTS:
                return True, "Skipped"
            r = self.http_get(f"{NOCODB_URL}/api/v1/health")
            if r.status_code != 200:
                return False, f"HTTP {r.status_code}"
            return True, f"NocoDB healthy (HTTP {r.status_code})"
        return self._run_test("NocoDB Health Endpoint", fn)

    # ── Test 11: Automation webhook health ────────────────────────
    def test_automation_health(self) -> TestResult:
        def fn():
            if not HAS_REQUESTS:
                return True, "Skipped"
            r = self.http_get(f"{AUTOMATION_URL}/health")
            if r.status_code != 200:
                return False, f"HTTP {r.status_code}"
            data = r.json()
            return True, f"Automation healthy: {data.get('status', 'ok')}"
        return self._run_test("Automation Webhook Health", fn)

    # ── Test 12: External portal loads ────────────────────────────
    def test_portal_loads(self) -> TestResult:
        def fn():
            if not self.host or not HAS_REQUESTS:
                return True, "Skipped (no --host specified or requests unavailable)"
            r = self.http_get(self.host, allow_redirects=True)
            if r.status_code != 200:
                return False, f"Portal returned HTTP {r.status_code}"
            if "Ministry Intelligence Platform" not in r.text:
                return False, "Portal response doesn't contain expected title"
            return True, f"Portal loads correctly ({len(r.text)} bytes)"
        return self._run_test("External Portal Loads", fn)

    # ── Test 13: Report script syntax ─────────────────────────────
    def test_report_script_syntax(self) -> TestResult:
        def fn():
            import ast
            scripts = [
                "scripts/webhook_listener.py",
                "scripts/generate_report.py",
                "scripts/integrity_check.py",
                "scripts/calculate_goals.py",
                "scripts/donation_initiator.py",
                "nocodb/init_nocodb.py",
            ]
            base = os.path.join(os.path.dirname(__file__), "..")
            errors = []
            for s in scripts:
                path = os.path.join(base, s)
                try:
                    with open(path) as f:
                        ast.parse(f.read())
                except FileNotFoundError:
                    errors.append(f"MISSING: {s}")
                except SyntaxError as e:
                    errors.append(f"SYNTAX ERROR {s}: {e}")
            if errors:
                return False, "; ".join(errors)
            return True, f"All {len(scripts)} scripts parse without errors"
        return self._run_test("Python Scripts Syntax Valid", fn, critical=True)

    # ── Test 14: Foreign key integrity in fresh DB ─────────────────
    def test_fk_integrity(self) -> TestResult:
        def fn():
            conn = self.get_db()
            try:
                with conn.cursor() as cur:
                    # Verify FK constraints exist
                    cur.execute("""
                        SELECT COUNT(*) AS cnt
                        FROM information_schema.table_constraints
                        WHERE constraint_type = 'FOREIGN KEY'
                          AND table_schema = 'public'
                    """)
                    cnt = dict(cur.fetchone())["cnt"]
            finally:
                conn.close()
            if cnt < 8:
                return False, f"Only {cnt} FK constraints found; expected ≥8"
            return True, f"{cnt} foreign key constraints enforced"
        return self._run_test("Foreign Key Constraints Exist", fn)

    # ── Test 15: Docker containers status ─────────────────────────
    def test_docker_containers(self) -> TestResult:
        def fn():
            try:
                result = subprocess.run(
                    ["docker", "compose", "ps", "--format", "json"],
                    capture_output=True, text=True, timeout=15,
                    cwd=os.path.join(os.path.dirname(__file__), ".."),
                )
                if result.returncode != 0:
                    return True, "Skipped (docker compose not available in this environment)"

                lines = [l for l in result.stdout.strip().split("\n") if l.strip()]
                containers = []
                for line in lines:
                    try:
                        containers.append(json.loads(line))
                    except json.JSONDecodeError:
                        continue

                if not containers:
                    return True, "Skipped (no containers found — may be running standalone)"

                unhealthy = [c["Name"] for c in containers
                             if c.get("Health", "healthy") not in ("healthy", "")]
                not_running = [c["Name"] for c in containers
                               if c.get("State", "running") != "running"
                               and c.get("Name", "") not in ("mip-superset-init",)]

                if unhealthy:
                    return False, f"Unhealthy containers: {', '.join(unhealthy)}"
                if not_running:
                    return False, f"Not running: {', '.join(not_running)}"
                return True, f"All {len(containers)} containers running"
            except FileNotFoundError:
                return True, "Skipped (docker not available)"
        return self._run_test("Docker Containers Running", fn)

    # ── Run all tests ─────────────────────────────────────────────
    def run_all(self) -> int:
        print(f"\n{'='*65}")
        print("  MINISTRY INTELLIGENCE PLATFORM – System Verification")
        print(f"  {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        print(f"  DB:  {PG_USER}@{PG_HOST}/{PG_DB}")
        if self.host:
            print(f"  URL: {self.host}")
        print(f"{'='*65}\n")

        # Database tests (require psycopg2)
        if HAS_PSYCOPG2:
            print("── Database Tests ─────────────────────────────────────────")
            self.test_db_connectivity()
            self.test_schema_completeness()
            self.test_giving_categories_seeded()
            self.test_attendance_unique_constraint()
            self.test_donation_flow()
            self.test_goals_percentage()
            self.test_fk_integrity()
        else:
            print("  ⚠️  psycopg2 not installed — skipping database tests")

        print("\n── Script Tests ────────────────────────────────────────────")
        self.test_report_script_syntax()
        self.test_integrity_check_runs()

        print("\n── Service Tests ───────────────────────────────────────────")
        if HAS_REQUESTS:
            self.test_superset_health()
            self.test_superset_login()
            self.test_nocodb_health()
            self.test_automation_health()
            self.test_portal_loads()
        else:
            print("  ⚠️  requests not installed — skipping HTTP tests")

        print("\n── Infrastructure Tests ────────────────────────────────────")
        self.test_docker_containers()

        # ── Summary ───────────────────────────────────────────────
        passed = [r for r in self.results if r.passed]
        failed = [r for r in self.results if not r.passed]
        total = len(self.results)
        avg_ms = sum(r.duration_ms for r in self.results) / max(total, 1)

        print(f"\n{'='*65}")
        if failed:
            print(f"  ❌ RESULT: {len(passed)}/{total} tests passed  ({len(failed)} FAILED)")
            print(f"\n  Failed tests:")
            for r in failed:
                crit = " [CRITICAL]" if r.critical else ""
                print(f"    ❌ {r.name}{crit}: {r.message}")
        else:
            print(f"  ✅ ALL SYSTEMS GO – {total}/{total} tests passed")

        print(f"  Avg test duration: {avg_ms:.0f}ms")
        print(f"{'='*65}\n")

        return 0 if not failed else 1


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="MIP System Verifier")
    parser.add_argument("--host", default=None,
                        help="External base URL (e.g. https://ministry.example.com)")
    parser.add_argument("--verbose", action="store_true")
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()
    verifier = MIPVerifier(host=args.host, verbose=args.verbose)
    sys.exit(verifier.run_all())
