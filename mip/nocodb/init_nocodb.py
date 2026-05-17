#!/usr/bin/env python3
"""
Ministry Intelligence Platform (MIP) – NocoDB Initialisation Script
Creates workspace, tables, form views, and user roles via NocoDB REST API.
Idempotent: safe to run multiple times.

Usage:
    python3 init_nocodb.py
"""

import os
import sys
import json
import time
import logging
import requests
from typing import Optional

# ── Logging ──────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
log = logging.getLogger(__name__)

# ── Configuration ─────────────────────────────────────────────────
NOCODB_URL = os.environ.get("NOCODB_URL", "http://localhost:8080")
ADMIN_EMAIL = os.environ.get("NOCODB_ADMIN_EMAIL", "admin@ministry.org")
ADMIN_PASSWORD = os.environ.get("NOCODB_ADMIN_PASSWORD", "admin")
POSTGRES_HOST = os.environ.get("POSTGRES_HOST", "db")
POSTGRES_USER = os.environ.get("POSTGRES_USER", "ministry")
POSTGRES_PASSWORD = os.environ.get("POSTGRES_PASSWORD", "ministry")
POSTGRES_DB = os.environ.get("POSTGRES_DB", "ministry_db")

# Retry config
MAX_RETRIES = 30
RETRY_DELAY = 10


class NocoDB:
    """NocoDB REST API client."""

    def __init__(self, base_url: str):
        self.base_url = base_url.rstrip("/")
        self.session = requests.Session()
        self.token: Optional[str] = None

    def wait_until_ready(self) -> None:
        """Wait for NocoDB to respond to health checks."""
        for attempt in range(1, MAX_RETRIES + 1):
            try:
                r = self.session.get(f"{self.base_url}/api/v1/health", timeout=5)
                if r.status_code == 200:
                    log.info("NocoDB is ready.")
                    return
            except requests.RequestException:
                pass
            log.info("Attempt %d/%d: NocoDB not ready yet...", attempt, MAX_RETRIES)
            time.sleep(RETRY_DELAY)
        raise RuntimeError("NocoDB did not become ready within the timeout period.")

    def signup_or_login(self, email: str, password: str) -> None:
        """Authenticate with NocoDB; signup first if needed."""
        # Try login first
        r = self.session.post(
            f"{self.base_url}/api/v1/auth/user/signin",
            json={"email": email, "password": password},
            timeout=10,
        )
        if r.status_code == 200:
            self.token = r.json()["token"]
            self.session.headers.update({"xc-auth": self.token})
            log.info("Authenticated as %s", email)
            return

        # Try signup
        r2 = self.session.post(
            f"{self.base_url}/api/v1/auth/user/signup",
            json={"email": email, "password": password},
            timeout=10,
        )
        if r2.status_code in (200, 201):
            self.token = r2.json()["token"]
            self.session.headers.update({"xc-auth": self.token})
            log.info("Admin user created and authenticated: %s", email)
            return

        raise RuntimeError(f"Failed to authenticate: {r.text}")

    def get(self, path: str, **kwargs) -> dict:
        r = self.session.get(f"{self.base_url}{path}", timeout=15, **kwargs)
        r.raise_for_status()
        return r.json()

    def post(self, path: str, data: dict = None, **kwargs) -> dict:
        r = self.session.post(
            f"{self.base_url}{path}", json=data, timeout=15, **kwargs
        )
        r.raise_for_status()
        return r.json()

    def patch(self, path: str, data: dict = None) -> dict:
        r = self.session.patch(
            f"{self.base_url}{path}", json=data, timeout=15
        )
        r.raise_for_status()
        return r.json()

    # ── Project / Base management ─────────────────────────────────

    def get_or_create_project(self, title: str) -> str:
        """Get or create a NocoDB project, return project ID."""
        projects = self.get("/api/v1/db/meta/projects/")
        for p in projects.get("list", []):
            if p["title"] == title:
                log.info("Project already exists: %s (id=%s)", title, p["id"])
                return p["id"]

        result = self.post(
            "/api/v1/db/meta/projects/",
            {"title": title},
        )
        log.info("Created project: %s (id=%s)", title, result["id"])
        return result["id"]

    def get_or_link_postgres(self, project_id: str) -> str:
        """Register the ministry PostgreSQL database as a base in the project."""
        bases = self.get(f"/api/v1/db/meta/projects/{project_id}/bases/")
        for b in bases.get("list", []):
            if b.get("type") == "pg" and b.get("alias") == "ministry_db":
                log.info("PostgreSQL base already linked.")
                return b["id"]

        result = self.post(
            f"/api/v1/db/meta/projects/{project_id}/bases/",
            {
                "alias": "ministry_db",
                "type": "pg",
                "config": {
                    "client": "pg",
                    "connection": {
                        "host": POSTGRES_HOST,
                        "port": 5432,
                        "user": POSTGRES_USER,
                        "password": POSTGRES_PASSWORD,
                        "database": POSTGRES_DB,
                    },
                    "searchPath": ["public"],
                },
                "inflection_column": "camelize",
                "inflection_table": "camelize",
            },
        )
        log.info("Linked PostgreSQL base: %s", result.get("id"))
        return result["id"]

    def sync_tables(self, project_id: str, base_id: str) -> None:
        """Trigger NocoDB to sync tables from the linked PostgreSQL database."""
        try:
            self.post(
                f"/api/v1/db/meta/projects/{project_id}/bases/{base_id}/tables/",
                {},
            )
        except Exception as e:
            log.info("Table sync (non-fatal): %s", e)

    def get_tables(self, project_id: str) -> list:
        """List tables in the project."""
        result = self.get(f"/api/v1/db/meta/projects/{project_id}/tables/")
        return result.get("list", [])

    def find_table_id(self, project_id: str, table_name: str) -> Optional[str]:
        """Find a table by name and return its ID."""
        tables = self.get_tables(project_id)
        for t in tables:
            if t["title"].lower() == table_name.lower():
                return t["id"]
        return None

    def get_or_create_form_view(
        self, table_id: str, view_title: str
    ) -> str:
        """Create a form view on a table if not already present."""
        views = self.get(f"/api/v1/db/meta/tables/{table_id}/views/")
        for v in views.get("list", []):
            if v["title"] == view_title:
                log.info("Form view already exists: %s", view_title)
                return v["id"]

        result = self.post(
            f"/api/v1/db/meta/tables/{table_id}/views/",
            {"title": view_title, "type": 1},  # type 1 = Form view
        )
        log.info("Created form view: %s", view_title)
        return result["id"]

    def invite_user(self, project_id: str, email: str, role: str) -> None:
        """Invite a user to a project with a specified role."""
        try:
            self.post(
                f"/api/v1/db/meta/projects/{project_id}/users/",
                {"email": email, "roles": role},
            )
            log.info("Invited user %s as %s", email, role)
        except Exception as e:
            log.info("User invite (may already exist – non-fatal): %s", e)


def main() -> None:
    """Main entrypoint for NocoDB initialisation."""
    nc = NocoDB(NOCODB_URL)

    # ── Step 1: Wait for NocoDB ───────────────────────────────────
    nc.wait_until_ready()

    # ── Step 2: Authenticate ──────────────────────────────────────
    nc.signup_or_login(ADMIN_EMAIL, ADMIN_PASSWORD)

    # ── Step 3: Get or create project ────────────────────────────
    project_id = nc.get_or_create_project("Ministry Intelligence Platform")

    # ── Step 4: Link PostgreSQL database ─────────────────────────
    base_id = nc.get_or_link_postgres(project_id)

    # ── Step 5: Sync tables ───────────────────────────────────────
    log.info("Syncing tables from PostgreSQL...")
    nc.sync_tables(project_id, base_id)
    time.sleep(5)  # Allow sync to complete

    # ── Step 6: Create form views for data entry ──────────────────
    form_views = {
        "members": [
            ("Add Member", "Use this form to register a new fellowship member."),
        ],
        "activities": [
            ("Create Activity", "Define a new ministry activity or event."),
        ],
        "attendance": [
            ("Record Attendance", "Mark attendance for a service or meeting."),
        ],
        "outreaches": [
            ("Log Outreach", "Record an outreach event with people reached and salvations."),
        ],
        "follow_ups": [
            ("Add Follow-Up", "Log a follow-up contact from an outreach."),
        ],
        "finance": [
            ("Record Income/Expense", "Add a financial transaction to the ledger."),
        ],
        "donations": [
            ("Submit Donation", "Record and initiate a mobile money donation."),
        ],
    }

    for table_name, views in form_views.items():
        table_id = nc.find_table_id(project_id, table_name)
        if not table_id:
            log.warning(
                "Table '%s' not found in NocoDB – it may not have synced yet. "
                "Run this script again after sync completes.",
                table_name,
            )
            continue

        for view_title, _description in views:
            nc.get_or_create_form_view(table_id, view_title)

    # ── Step 7: Invite workspace users ────────────────────────────
    log.info("Setting up workspace user roles...")
    default_users = [
        ("clerk@ministry.org", "editor"),
        ("finance@ministry.org", "editor"),
        ("leader@ministry.org", "viewer"),
    ]
    for email, role in default_users:
        nc.invite_user(project_id, email, role)

    # ── Step 8: Print summary ─────────────────────────────────────
    log.info("=" * 60)
    log.info("NocoDB initialisation COMPLETE.")
    log.info("  Project: Ministry Intelligence Platform")
    log.info("  Project ID: %s", project_id)
    log.info("  Base ID: %s", base_id)
    log.info("  URL: %s", NOCODB_URL)
    log.info("  Admin: %s", ADMIN_EMAIL)
    log.info("=" * 60)
    log.info(
        "IMPORTANT: Log into NocoDB and verify that all tables have synced."
    )
    log.info(
        "For each table, check that linked record fields (dropdowns) point to "
        "the correct reference tables (giving_categories, members, activities, etc.)."
    )


if __name__ == "__main__":
    main()
